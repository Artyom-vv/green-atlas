from __future__ import annotations

from collections import Counter
from io import StringIO
from math import atan, atan2, cos, isfinite, pi, sin
from pathlib import Path
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

import ezdxf
from ezdxf.document import Drawing
from ezdxf.lldxf.tagger import binary_tags_loader
from ezdxf.math import OCS, Vec3
from ezdxf.path import make_path
from shapely import STRtree
from shapely.errors import GEOSException
from shapely.geometry import LineString as ShapelyLineString
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import shape as shapely_shape
from shapely.ops import polygonize, unary_union

from app.dxf_import.acis_lookup import indexed_acis_lookup
from app.dxf_import.axis_provenance import straight_axis_provenance
from app.dxf_import.block_capacity import inspect_block_expansion
from app.dxf_import.block_diagnostics import BlockGeometryFailure, record_failure, source_handle
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.component_provenance import component_definition_provenance
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.encoding import decode_text_dxf
from app.dxf_import.layer_contracts import (
    BoundaryCandidate,
    BoundaryCandidateStatus,
    Layer,
    LayerKind,
    LayerSuggestionConfidence,
)
from app.dxf_import.layer_suggestions import (
    assess_layer_suggestion,
    is_boundary_candidate_name,
    suggest_layer_kind,
)
from app.dxf_import.mleader_compat import prepare_multileader_transforms
from app.dxf_import.polygons import hatch_geometry, mpolygon_geometry
from app.dxf_import.preview_marker import read_preview_marker
from app.dxf_import.styles import SourceStyleResolver
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.geometry.contracts import (
    CoordinateReference,
    DxfVerticalPrimitive,
    GeometrySnapshot,
)
from app.geometry.geojson_size import coordinate_count as _geometry_coordinate_count

SUPPORTED_TYPES = {
    "LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE", "HATCH", "MPOLYGON",
    "3DFACE", "SOLID", "TRACE", "POINT", "TEXT", "MTEXT", "ATTDEF", "INSERT", "DIMENSION", "WIPEOUT",
    # These entities are common in survey/CAD exports. They are rendered as
    # native geometry where possible and never participate in planting rules.
    "LEADER", "MLEADER", "MULTILEADER", "MLINE", "HELIX", "MESH", "RAY", "XLINE", "IMAGE",
    "PDFUNDERLAY", "PDFREFERENCE", "DWFUNDERLAY", "DGNUNDERLAY",
    "ACAD_PROXY_ENTITY",
}
LAYER_COLORS = ["#0D7C6B", "#64748B", "#94A3B8", "#2563EB", "#F97316", "#22C55E", "#7C3AED"]
MAX_PROXY_VIRTUAL_GEOMETRIES = 2_000
MAX_RENDERED_MESH_FACES = 20_000
MAX_NESTED_BLOCK_DEPTH = 16
# MINSERT stores a rectangular array in a single DXF entity.  Expanding an
# arbitrary array is normally correct (each member can carry a real planting
# constraint), but a tiny hostile or accidental file can describe millions of
# members.  At that point a partial expansion would be less safe than no
# calculation: it could make occupied ground look available.  Keep the source
# intact, represent the array as context, and explicitly block calculation.
MAX_EXPANDED_MINSERT_INSTANCES = 20_000
InsertComponents = list[list[tuple[dict[str, Any], Any, str]]]

# These entities describe drafting or display context, not a finite physical
# obstacle. They stay visible on the map, but must never acquire a planting
# constraint even if their source layer is mapped manually to a physical kind.
CONTEXT_ONLY_ENTITY_TYPES = {
    "TEXT", "MTEXT", "ATTDEF", "DIMENSION", "LEADER", "MLEADER", "MULTILEADER", "MLINE", "HELIX",
    "MESH", "RAY", "XLINE", "WIPEOUT", "IMAGE", "PDFUNDERLAY",
    "PDFREFERENCE", "DWFUNDERLAY", "DGNUNDERLAY", "ACAD_PROXY_ENTITY",
}


def _is_mesh_context(entity: Any) -> bool:
    """True for CAD surfaces that do not prove a 2D planting obstacle.

    A POLYLINE can either be a finite 2D line or a polygon/polyface mesh. The
    latter is a surface representation (often terrain), so flattening its
    faces and then applying a manually mapped layer as a setback is unsafe.
    It remains visible source context, just like a native MESH.
    """
    if entity.dxftype() == "MESH":
        return True
    if entity.dxftype() != "POLYLINE":
        return False
    try:
        return bool(entity.is_poly_face_mesh or entity.is_polygon_mesh)
    except Exception:
        return False


def _mesh_face_count(entity: Any) -> int:
    """Return an inexpensive declared face count without flattening a mesh."""
    try:
        if entity.dxftype() == "POLYLINE" and entity.is_polygon_mesh:
            rows = max(0, int(entity.dxf.m_count))
            columns = max(0, int(entity.dxf.n_count))
            row_cells = rows if entity.is_m_closed else max(0, rows - 1)
            column_cells = columns if entity.is_n_closed else max(0, columns - 1)
            return row_cells * column_cells
        if entity.dxftype() == "MESH":
            return len(entity.get_data().faces)
        if entity.dxftype() == "POLYLINE" and entity.is_poly_face_mesh:
            return max(0, len(entity.vertices))
    except Exception:
        return 0
    return 0


def _mesh_anchor(entity: Any, factor: float) -> dict[str, Any] | None:
    """Return a safe visible anchor when a surface cannot be flattened.

    Do not invent an origin for malformed CAD data: a missing anchor is more
    honest than making the map fit or snap to a fictitious point at (0, 0).
    """
    try:
        if entity.dxftype() == "POLYLINE" and entity.is_polygon_mesh:
            return {"type": "Point", "coordinates": _point(entity.get_mesh_vertex((0, 0)).dxf.location, factor)}
        if entity.dxftype() == "POLYLINE" and entity.vertices:
            return {"type": "Point", "coordinates": _point(entity.vertices[0].dxf.location, factor)}
        if entity.dxftype() == "MESH":
            vertices = entity.get_data().vertices
            if vertices:
                return {"type": "Point", "coordinates": _point(vertices[0], factor)}
    except Exception:
        pass
    return None


def _insert_instance_count(entity: Any) -> int:
    """Return the declared INSERT/MINSERT cardinality defensively."""
    try:
        return max(1, int(getattr(entity, "mcount", 1) or 1))
    except (TypeError, ValueError):
        return 1


def _read_document(content: bytes | bytearray) -> Any:
    """Read ASCII and binary DXF uploads without writing them to disk."""
    if content.startswith(b"AutoCAD Binary DXF"):
        return Drawing.load(binary_tags_loader(content))
    # Match file-based DXF loading: ezdxf expects universal newlines, while
    # StringIO's default preserves CR characters in Windows/CAD text uploads.
    return ezdxf.read(StringIO(decode_text_dxf(content), newline=None))


def _point(value: Any, factor: float) -> list[float]:
    return [round(float(value[0]) * factor, 6), round(float(value[1]) * factor, 6)]


def _point3(value: Any, factor: float) -> list[float]:
    """Convert a DXF WCS coordinate to metres without flattening Z."""
    point = Vec3(value)
    return [
        round(float(point.x) * factor, 6),
        round(float(point.y) * factor, 6),
        round(float(point.z) * factor, 6),
    ]


def _terrain_document_evidence(document: Any) -> dict[str, Any] | None:
    """Read the explicit terrain declaration written by our fixture pipeline.

    A layer name alone is not evidence: CAD authors routinely reuse names.
    Requiring the complete document declaration prevents an unrelated DXF
    from silently upgrading arbitrary faces to confirmed terrain.
    """
    custom = document.header.custom_vars
    values = {
        "layer": custom.get("GREEN_ATLAS_TERRAIN_LAYER"),
        "dataset": custom.get("GREEN_ATLAS_TERRAIN_DATASET"),
        "url": custom.get("GREEN_ATLAS_TERRAIN_SOURCE_URL"),
        "attribution": custom.get("GREEN_ATLAS_TERRAIN_ATTRIBUTION"),
        "confidence": custom.get("GREEN_ATLAS_TERRAIN_CONFIDENCE"),
        "datum": custom.get("GREEN_ATLAS_VERTICAL_DATUM"),
        "datum_offset": custom.get("GREEN_ATLAS_VERTICAL_DATUM_OFFSET_M_ASL"),
    }
    if not all(values[key] for key in ("layer", "dataset", "url", "attribution", "confidence", "datum", "datum_offset")):
        return None
    if values["confidence"] not in {"surveyed", "estimated"}:
        return None
    try:
        offset = float(values["datum_offset"])
    except (TypeError, ValueError):
        return None
    if not isfinite(offset):
        return None
    return {**values, "datum_offset": offset}


def _vertical_primitive(
    entity: Any,
    factor: float,
    source_units: str,
    primitive_id: str,
    terrain_evidence: dict[str, Any] | None = None,
) -> DxfVerticalPrimitive | None:
    """Extract explicit WCS XYZ evidence independently of the 2D projection.

    This is intentionally structural, not semantic: a MESH or 3DFACE is a
    proven CAD surface, but it is *not* terrain until a user/source mapping
    says so. Consequently every imported primitive starts as ``unmapped``.
    """
    entity_type = entity.dxftype()
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    primitive_type = "polyline"
    evidence = "explicit_xyz"
    try:
        if entity_type == "3DFACE":
            for name in ("vtx0", "vtx1", "vtx2", "vtx3"):
                value = entity.dxf.get(name)
                if value is not None:
                    point = _point3(value, factor)
                    if not vertices or point != vertices[-1]:
                        vertices.append(point)
            if len(vertices) < 3:
                return None
            faces = [list(range(len(vertices)))]
            primitive_type = "surface_mesh"
        elif entity_type == "MESH":
            data = entity.get_data()
            vertices = [_point3(point, factor) for point in data.vertices]
            faces = [
                [int(index) for index in face if 0 <= int(index) < len(vertices)]
                for face in data.faces
            ]
            faces = [face for face in faces if len(face) >= 3]
            primitive_type = "surface_mesh"
        elif entity_type == "POLYLINE" and entity.is_poly_face_mesh:
            vertex_index: dict[int, int] = {}
            for vertex in entity.vertices:
                if int(vertex.dxf.get("flags", 0) or 0) == 128:
                    continue
                vertex_index[id(vertex)] = len(vertices)
                vertices.append(_point3(vertex.dxf.location, factor))
            for face in entity.faces():
                indices = [vertex_index[id(vertex)] for vertex in face if id(vertex) in vertex_index]
                if len(indices) >= 3:
                    faces.append(indices)
            primitive_type = "surface_mesh"
        elif entity_type == "POLYLINE" and entity.is_polygon_mesh:
            m_count, n_count = int(entity.dxf.m_count), int(entity.dxf.n_count)
            for m_index in range(m_count):
                for n_index in range(n_count):
                    vertices.append(_point3(entity.get_mesh_vertex((m_index, n_index)).dxf.location, factor))
            m_range = range(m_count if entity.is_m_closed else max(0, m_count - 1))
            n_range = range(n_count if entity.is_n_closed else max(0, n_count - 1))
            for m_index in m_range:
                for n_index in n_range:
                    faces.append([
                        m_index * n_count + n_index,
                        ((m_index + 1) % m_count) * n_count + n_index,
                        ((m_index + 1) % m_count) * n_count + ((n_index + 1) % n_count),
                        m_index * n_count + ((n_index + 1) % n_count),
                    ])
            primitive_type = "surface_mesh"
        elif entity_type == "POLYLINE" and entity.is_3d_polyline:
            vertices = [_point3(vertex.dxf.location, factor) for vertex in entity.vertices]
            if entity.is_closed and vertices and vertices[-1] != vertices[0]:
                vertices.append(vertices[0])
        elif entity_type == "LINE":
            vertices = [_point3(entity.dxf.start, factor), _point3(entity.dxf.end, factor)]
        elif entity_type == "POINT":
            vertices = [_point3(entity.dxf.location, factor)]
            primitive_type = "point"
        else:
            # Planar entities can still carry an explicit elevation and/or a
            # thickness along their OCS normal. Preserve that evidence using
            # their already-normalised WCS anchor; the 2D geometry remains the
            # authoritative footprint.
            vertical = _source_vertical_properties(entity, factor)
            if "source_base_elevation_m" not in vertical and "source_extrusion_height_m" not in vertical:
                return None
            anchor = None
            for attribute in ("insert", "center", "start"):
                if entity.dxf.hasattr(attribute):
                    anchor = entity.dxf.get(attribute)
                    break
            if anchor is None and entity_type == "LWPOLYLINE":
                first = next(iter(entity.get_points("xy")), None)
                if first is not None:
                    elevation = float(entity.dxf.get("elevation", 0) or 0)
                    anchor = entity.ocs().to_wcs(Vec3(first[0], first[1], elevation))
            if anchor is None:
                return None
            vertices = [_point3(anchor, factor)]
            primitive_type = "point"
            evidence = "explicit_extrusion" if "source_extrusion_height_m" in vertical else "explicit_elevation"
        if not vertices or any(not all(isfinite(value) for value in point) for point in vertices):
            return None
        extrusion_vector_m = None
        vertical = _source_vertical_properties(entity, factor)
        if height := vertical.get("source_extrusion_height_m"):
            normal = Vec3(entity.dxf.get("extrusion", (0, 0, 1))).normalize()
            signed_height = float(entity.dxf.get("thickness", height)) * factor
            extrusion_vector_m = [
                round(float(normal.x) * signed_height, 6),
                round(float(normal.y) * signed_height, 6),
                round(float(normal.z) * signed_height, 6),
            ]
            evidence = "explicit_extrusion"
        source_layer = str(entity.dxf.layer)
        is_declared_terrain = bool(
            terrain_evidence
            and source_layer == terrain_evidence["layer"]
            and primitive_type == "surface_mesh"
        )
        return DxfVerticalPrimitive(
            primitive_id=primitive_id,
            primitive_type=primitive_type,
            vertices_m=vertices,
            faces=faces,
            source_layer=source_layer,
            source_entity_type=entity_type,
            source_handle=str(entity.dxf.get("handle", "")) or None,
            source_file_units=source_units,
            unit_scale_to_m=factor,
            vertical_evidence=evidence,
            extrusion_vector_m=extrusion_vector_m,
            terrain_mapping_status="confirmed" if is_declared_terrain else "unmapped",
            terrain_mapping_basis="dxf_document_metadata" if is_declared_terrain else None,
            terrain_confidence=str(terrain_evidence["confidence"]) if is_declared_terrain else None,
            source_dataset=str(terrain_evidence["dataset"]) if is_declared_terrain else None,
            source_url=str(terrain_evidence["url"]) if is_declared_terrain else None,
            source_attribution=str(terrain_evidence["attribution"]) if is_declared_terrain else None,
            vertical_datum=str(terrain_evidence["datum"]) if is_declared_terrain else None,
            vertical_datum_offset_m=float(terrain_evidence["datum_offset"]) if is_declared_terrain else None,
        )
    except Exception:
        return None


def _ocs_point(entity: Any, value: Any, factor: float) -> list[float]:
    """Normalize a planar DXF point from its object coordinate system.

    LWPOLYLINE, 2D POLYLINE, CIRCLE, ARC, SOLID and TRACE are allowed to use
    an OCS whose normal is not the default +Z. Reading their raw ``x``/``y``
    is then a quiet mirror/rotation of the plan. Fall back only for entity
    classes without an OCS helper; the source is still never modified.
    """

    try:
        return _point(entity.ocs().to_wcs(value), factor)
    except Exception:
        return _point(value, factor)


_HEIGHT_ATTRIBUTE_TAGS = {"BUILDING_HEIGHT_M", "HEIGHT_M", "ВЫСОТА_М"}


def _source_vertical_properties(
    entity: Any,
    factor: float,
    attributes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Preserve explicit DXF vertical evidence without deriving a volume.

    A layer name, colour or 2D footprint does not prove a building height.
    ``thickness`` does, but only when its extrusion is vertical.  A block
    attribute is accepted only when its tag explicitly declares metres.  The
    scene API can therefore extrude confirmed objects while leaving every
    other footprint flat.
    """

    result: dict[str, Any] = {}
    try:
        if entity.dxf.hasattr("thickness"):
            thickness = float(entity.dxf.thickness)
            extrusion = Vec3(entity.dxf.get("extrusion", (0, 0, 1)))
            if (
                isfinite(thickness)
                and abs(thickness) > 1e-9
                and abs(float(extrusion.x)) <= 1e-9
                and abs(float(extrusion.y)) <= 1e-9
                and abs(abs(float(extrusion.z)) - 1.0) <= 1e-9
            ):
                result["source_extrusion_height_m"] = round(abs(thickness) * factor, 6)
    except Exception:
        pass

    try:
        elevation: Any | None = None
        entity_type = entity.dxftype()
        if entity_type in {"LWPOLYLINE", "HATCH", "MPOLYGON"} and entity.dxf.hasattr("elevation"):
            elevation = entity.dxf.elevation
        elif entity_type == "POLYLINE" and entity.dxf.hasattr("elevation"):
            elevation = entity.dxf.elevation
        elif entity_type in {"CIRCLE", "ARC"} and entity.dxf.hasattr("center"):
            elevation = entity.dxf.center
        elif entity_type in {"INSERT", "POINT", "TEXT", "MTEXT"} and entity.dxf.hasattr("insert"):
            elevation = entity.dxf.insert
        if elevation is not None:
            if isinstance(elevation, (tuple, list, Vec3)):
                value = float(elevation[2])
            else:
                value = float(elevation)
            if isfinite(value):
                result["source_base_elevation_m"] = round(value * factor, 6)
    except Exception:
        pass

    for raw_tag, raw_value in (attributes or {}).items():
        tag = str(raw_tag).strip().upper().replace(" ", "_")
        if tag not in _HEIGHT_ATTRIBUTE_TAGS:
            continue
        try:
            height = float(str(raw_value).strip().replace(",", "."))
        except ValueError:
            continue
        if isfinite(height) and height > 0:
            result["source_attribute_height_m"] = round(height, 6)
            result["source_attribute_height_tag"] = str(raw_tag)
            break
    return result


def _flatten_bulged_points(
    raw_points: list[tuple[float, float, float]],
    closed: bool,
    factor: float,
    transform: Callable[[float, float], list[float]] | None = None,
) -> list[list[float]]:
    """Flatten 2D polyline bulges for display without modifying the DXF."""
    point = lambda x, y: transform(x, y) if transform else [round(x * factor, 6), round(y * factor, 6)]
    if len(raw_points) < 2:
        return [point(item[0], item[1]) for item in raw_points]
    points: list[list[float]] = [point(raw_points[0][0], raw_points[0][1])]
    segment_count = len(raw_points) if closed else len(raw_points) - 1
    for index in range(segment_count):
        start = raw_points[index]
        end = raw_points[(index + 1) % len(raw_points)]
        bulge = float(start[2] or 0)
        x1, y1 = float(start[0]), float(start[1])
        x2, y2 = float(end[0]), float(end[1])
        if abs(bulge) < 1e-9:
            points.append(point(x2, y2))
            continue
        chord = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if chord < 1e-9:
            continue
        sweep = 4 * atan(bulge)
        midpoint_x = (x1 + x2) / 2
        midpoint_y = (y1 + y2) / 2
        normal_x = -(y2 - y1) / chord
        normal_y = (x2 - x1) / chord
        center_offset = chord * (1 - bulge * bulge) / (4 * bulge)
        center_x = midpoint_x + normal_x * center_offset
        center_y = midpoint_y + normal_y * center_offset
        radius = ((x1 - center_x) ** 2 + (y1 - center_y) ** 2) ** 0.5
        start_angle = atan2(y1 - center_y, x1 - center_x)
        steps = max(2, min(32, int(abs(sweep) / (pi / 12)) + 1))
        for step in range(1, steps + 1):
            angle = start_angle + sweep * step / steps
            points.append(point(center_x + radius * cos(angle), center_y + radius * sin(angle)))
    return points


def _lwpolyline_points(entity: Any, factor: float) -> list[list[float]]:
    elevation = float(entity.dxf.get("elevation", 0) or 0)
    return _flatten_bulged_points(
        [(float(point[0]), float(point[1]), float(point[2] or 0)) for point in entity.get_points("xyb")],
        entity.closed,
        factor,
        transform=lambda x, y: _ocs_point(entity, Vec3(x, y, elevation), factor),
    )


def _polyline_points(entity: Any, factor: float) -> list[list[float]]:
    """POLYLINE keeps its bulge on VERTEX entities, unlike LWPOLYLINE."""
    elevation = entity.dxf.get("elevation", (0, 0, 0))
    elevation_z = float(elevation[2] if isinstance(elevation, (tuple, list, Vec3)) else 0)
    return _flatten_bulged_points(
        [
            (
                float(vertex.dxf.location.x),
                float(vertex.dxf.location.y),
                float(vertex.dxf.get("bulge", 0) or 0),
            )
            for vertex in entity.vertices
        ],
        entity.is_closed,
        factor,
        transform=lambda x, y: _ocs_point(entity, Vec3(x, y, elevation_z), factor),
    )


def _polyline_width(entity: Any) -> float:
    """Return the widest declared segment of a 2D polyline in source units.

    CAD plans often encode a driveway or paved strip as a centre line with a
    constant (or per-vertex) width, rather than as a closed boundary. For a
    safety calculation the widest declared segment is the conservative choice:
    it cannot create an artificial available planting area on a tapered line.
    """
    try:
        widths: list[float] = []
        if entity.dxftype() == "LWPOLYLINE":
            widths.append(float(entity.dxf.get("const_width", 0) or 0))
            for point in entity.get_points("xyseb"):
                widths.extend((float(point[2] or 0), float(point[3] or 0)))
        else:
            widths.extend((
                float(entity.dxf.get("default_start_width", 0) or 0),
                float(entity.dxf.get("default_end_width", 0) or 0),
            ))
            for vertex in entity.vertices:
                widths.extend((
                    float(vertex.dxf.get("start_width", 0) or 0),
                    float(vertex.dxf.get("end_width", 0) or 0),
                ))
        return max(0.0, *widths)
    except Exception:
        return 0.0


def _wide_polyline_geometry(coordinates: list[list[float]], width_m: float) -> dict[str, Any] | None:
    """Convert a finite-width CAD line to its visible ground footprint."""
    if width_m <= 1e-9 or len(coordinates) < 2:
        return None
    try:
        # Flat caps reflect a CAD segment's stated endpoints. Round joins keep
        # bulged/curved segments continuous without a false gap at vertices.
        polygon = ShapelyLineString(coordinates).buffer(width_m / 2, cap_style="flat", join_style="round")
    except Exception:
        return None
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return None
    rings = [polygon.exterior, *polygon.interiors]
    return {
        "type": "Polygon",
        "coordinates": [
            [[round(float(point[0]), 6), round(float(point[1]), 6)] for point in ring.coords]
            for ring in rings
        ],
    }


def _polyline_center_geometry(entity: Any, factor: float) -> dict[str, Any] | None:
    """Preserve the authored path independently from a displayed line width.

    A closed, wide polyline used as a project boundary is visually a narrow
    stroke, but its centre path is the actual enclosing ring. The map keeps
    the stroke footprint; calculation may use this separately audited path
    when the operator maps the layer as ``site_border``.
    """
    try:
        if entity.dxftype() == "LWPOLYLINE":
            coordinates = _lwpolyline_points(entity, factor)
            closed = entity.closed
        elif entity.dxftype() == "POLYLINE" and entity.is_2d_polyline:
            coordinates = _polyline_points(entity, factor)
            closed = entity.is_closed
        else:
            return None
    except Exception:
        return None
    if len(coordinates) < 2:
        return None
    if closed and len(coordinates) >= 3:
        if coordinates[-1] != coordinates[0]:
            coordinates.append(coordinates[0])
        return {"type": "Polygon", "coordinates": [coordinates]}
    return {"type": "LineString", "coordinates": coordinates}


def _polygon_parts(geometry: Any) -> list[Any]:
    if geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        return [part for item in geometry.geoms for part in _polygon_parts(item)]
    return []


def _boundary_candidate(features: list[dict[str, Any]]) -> BoundaryCandidate:
    polygons: list[Any] = []
    lines: list[Any] = []
    used_center_geometry = False
    for feature in features:
        properties = feature.get("properties", {})
        source_geometry = properties.get("source_polyline_center_geometry")
        if isinstance(source_geometry, dict):
            used_center_geometry = True
        else:
            source_geometry = feature.get("geometry")
        try:
            geometry = shapely_shape(source_geometry)
        except (KeyError, TypeError, ValueError):
            return BoundaryCandidate(
                status=BoundaryCandidateStatus.INVALID,
                basis="source_geometry",
                issue="Контур слоя не читается как плоская геометрия",
            )
        if geometry.is_empty or not geometry.is_valid:
            return BoundaryCandidate(
                status=BoundaryCandidateStatus.INVALID,
                basis="authored_centerline" if used_center_geometry else "source_geometry",
                issue="Контур слоя пуст или самопересекается",
            )
        polygons.extend(_polygon_parts(geometry))
        if geometry.geom_type in {"LineString", "MultiLineString"}:
            lines.append(geometry)

    basis = "authored_centerline" if used_center_geometry else "source_surface"
    surfaces = polygons
    if not surfaces and lines:
        surfaces = list(polygonize(lines))
        basis = "polygonized_linework"
    if not surfaces:
        return BoundaryCandidate(
            status=BoundaryCandidateStatus.UNAVAILABLE,
            basis=basis,
            issue="Слой не образует замкнутую поверхность",
        )
    try:
        surface = unary_union(surfaces)
    except GEOSException:
        return BoundaryCandidate(
            status=BoundaryCandidateStatus.INVALID,
            basis=basis,
            issue="Контуры слоя конфликтуют при объединении",
        )
    if surface.is_empty or not surface.is_valid:
        return BoundaryCandidate(
            status=BoundaryCandidateStatus.INVALID,
            basis=basis,
            issue="Объединённая поверхность слоя некорректна",
        )
    inset = surface.buffer(-1.5)
    area = float(surface.area)
    inset_area = float(inset.area)
    status = (
        BoundaryCandidateStatus.USABLE
        if inset_area >= 24.0
        else BoundaryCandidateStatus.THIN
    )
    return BoundaryCandidate(
        status=status,
        basis=basis,
        area_m2=round(area, 3),
        inset_1_5m_area_m2=round(inset_area, 3),
        component_count=len(_polygon_parts(surface)),
        issue=(
            None
            if status == BoundaryCandidateStatus.USABLE
            else "После внутреннего отступа 1,5 м не остаётся рабочей площади"
        ),
    )


def _underlay_clip_geometry(entity: Any, factor: float) -> dict[str, Any] | None:
    """Show only a trustworthy, inside clipping frame of an external underlay.

    PDF/DWF/DGN pixels are external files and may be unavailable. A clipping
    path is still useful drawing context, but an outside clip or an unclipped
    reference has no finite visible extent and must not be invented as a site
    polygon. DXF defines these vertices in the unscaled OCS of the underlay.
    """
    try:
        flags = int(entity.dxf.get("flags", 0) or 0)
        if not entity.clipping or not entity.on or not flags & 16:
            return None
        boundary = list(entity.boundary_path)
        if len(boundary) == 2:
            lower_left, upper_right = boundary
            boundary = [
                lower_left,
                (float(upper_right[0]), float(lower_left[1])),
                upper_right,
                (float(lower_left[0]), float(upper_right[1])),
            ]
        if len(boundary) < 3:
            return None
        insert = entity.dxf.insert
        scale_x = float(entity.dxf.get("scale_x", 1) or 1)
        scale_y = float(entity.dxf.get("scale_y", 1) or 1)
        rotation = float(entity.dxf.get("rotation", 0) or 0) * pi / 180
        cosine, sine = cos(rotation), sin(rotation)
        ocs = OCS(entity.dxf.get("extrusion", (0, 0, 1)))
        coordinates: list[list[float]] = []
        for boundary_point in boundary:
            local_x = float(boundary_point[0]) * scale_x
            local_y = float(boundary_point[1]) * scale_y
            point = ocs.to_wcs(Vec3(
                float(insert.x) + local_x * cosine - local_y * sine,
                float(insert.y) + local_x * sine + local_y * cosine,
                float(insert.z),
            ))
            coordinates.append(_point(point, factor))
        if coordinates[-1] != coordinates[0]:
            coordinates.append(coordinates[0])
        return {"type": "Polygon", "coordinates": [coordinates]}
    except Exception:
        return None


def _hatch_polygon_geometry(loops: list[list[list[float]]]) -> dict[str, Any] | None:
    """Turn HATCH rings into Polygon/MultiPolygon without inverting islands.

    DXF stores every hatch boundary as a separate path. Passing all paths as
    rings of one GeoJSON polygon turns unrelated filled areas into holes. Ring
    nesting represents the even/odd hatch topology independently of winding
    direction and works for disconnected regions and islands.
    """
    records: list[tuple[list[list[float]], ShapelyPolygon]] = []
    for loop in loops:
        if len(loop) < 4:
            continue
        try:
            polygon = ShapelyPolygon(loop)
            if not polygon.is_valid:
                polygon = polygon.buffer(0)
        except Exception:
            continue
        if polygon.is_empty or polygon.geom_type != "Polygon" or polygon.area <= 0:
            continue
        records.append((loop, polygon))
    if not records:
        return None

    # A survey HATCH can contain thousands of separate paved/lawn islands.
    # Testing every ring against every other ring turns import into O(n²)
    # work before the map is even opened. The index asks only polygons that
    # actually contain the representative point, while preserving the same
    # smallest-container rule for real holes and islands.
    polygons = [polygon for _, polygon in records]
    index = STRtree(polygons)
    parents: list[int | None] = []
    for record_index, polygon in enumerate(polygons):
        containers = [
            int(candidate_index)
            for candidate_index in index.query(polygon.representative_point(), predicate="within")
            if int(candidate_index) != record_index and polygons[int(candidate_index)].area > polygon.area
        ]
        parents.append(min(containers, key=lambda candidate_index: polygons[candidate_index].area) if containers else None)

    def depth(index: int) -> int:
        result = 0
        parent = parents[index]
        while parent is not None:
            result += 1
            parent = parents[parent]
        return result

    polygons = [
        [loop, *(child_loop for child_index, (child_loop, _) in enumerate(records) if parents[child_index] == index)]
        for index, (loop, _) in enumerate(records)
        if depth(index) % 2 == 0
    ]
    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": polygons[0]}
    return {"type": "MultiPolygon", "coordinates": polygons}


def _flatten_insert_components(
    reference: Any, factor: float, inherited_layer: str, depth: int = 0,
    failures: list[BlockGeometryFailure] | None = None,
    insert_path: tuple[str, ...] = (),
) -> list[tuple[dict[str, Any], Any, str]]:
    """Flatten a virtual block tree while preserving each explicit layer.

    ``Insert.virtual_entities()`` correctly applies nested transformations,
    but returns a nested INSERT as an INSERT. Stopping at that first level
    collapses an explicit utility or boundary layer inside a common symbol
    into the outer symbol layer. Descend through the virtual tree so mapping
    and setbacks retain their original semantics.
    """
    if depth >= MAX_NESTED_BLOCK_DEPTH:
        record_failure(failures, reference, inherited_layer, insert_path, "Достигнута граница вложенности блоков")
        return []
    components: list[tuple[dict[str, Any], Any, str]] = []
    try:
        count = _insert_instance_count(reference)
        if count > MAX_EXPANDED_MINSERT_INSTANCES:
            record_failure(failures, reference, inherited_layer, insert_path, "Превышен предел экземпляров массива")
            return []
        references = reference.multi_insert() if count > 1 else [reference]
        for instance_index, instance in enumerate(references, start=1):
            path = (*insert_path, f"{source_handle(reference)}[{instance_index}]")

            def skipped(virtual: Any, reason: str) -> None:
                record_failure(failures, virtual, inherited_layer, path, reason)

            for virtual in instance.virtual_entities(skipped_entity_callback=skipped):
                declared_layer = str(virtual.dxf.get("layer", "0") or "0")
                effective_layer = inherited_layer if declared_layer == "0" else declared_layer
                if virtual.dxftype() == "INSERT":
                    components.extend(_flatten_insert_components(virtual, factor, effective_layer, depth + 1, failures, path))
                    continue
                geometry = _entity_geometry(virtual, factor)
                if geometry is not None:
                    components.append((geometry, virtual, effective_layer))
                elif virtual.dxftype() in SUPPORTED_TYPES and virtual.dxftype() not in CONTEXT_ONLY_ENTITY_TYPES:
                    record_failure(failures, virtual, inherited_layer, path, "Не удалось получить полный плоский контур")
    except Exception as error:
        record_failure(failures, reference, inherited_layer, insert_path, f"Ошибка раскрытия блока: {error}")
        return []
    return components


def _block_definition_components(reference: Any, inherited_layer: str, depth: int = 0) -> list[tuple[str, str]] | None:
    """List raw block-definition entities with their effective layers.

    This intentionally walks the raw definition rather than
    ``virtual_entities``. Some unsupported entities fail during virtual
    transformation and are then absent from that iterator altogether; the
    raw block is still enough to determine their layer. ``None`` means the
    block definition itself could not be inspected, which is distinct from a
    deliberately empty block.
    """
    if depth >= MAX_NESTED_BLOCK_DEPTH:
        return None
    components: list[tuple[str, str]] = []
    try:
        for component in reference.block():
            declared_layer = str(component.dxf.get("layer", "0") or "0")
            effective_layer = inherited_layer if declared_layer == "0" else declared_layer
            if component.dxftype() == "INSERT":
                nested = _block_definition_components(component, effective_layer, depth + 1)
                if nested is None:
                    return None
                components.extend(nested)
            else:
                components.append((component.dxftype(), effective_layer))
    except Exception:
        return None
    return components


def _insert_instance_components(entity: Any, factor: float, failures: list[BlockGeometryFailure] | None = None) -> list[list[tuple[dict[str, Any], Any, str]]]:
    """Flatten an INSERT into its instance components and effective layers.

    Layer ``0`` follows the layer of the parent INSERT in DXF. A non-zero
    layer belongs to the component itself and must remain available for layer
    mapping and constraints; otherwise a utility embedded in a symbol can
    silently turn into decorative geometry.
    """
    parent_layer = str(entity.dxf.layer)
    try:
        instance_count = _insert_instance_count(entity)
        references = entity.multi_insert() if instance_count > 1 else [entity]
        instances: list[list[tuple[dict[str, Any], Any, str]]] = []
        for instance_index, reference in enumerate(references, start=1):
            path = (f"{source_handle(entity)}[{instance_index}]",) if instance_count > 1 else ()
            instances.append(_flatten_insert_components(reference, factor, parent_layer, 0, failures, path))
        return instances
    except Exception as error:
        record_failure(failures, entity, parent_layer, (source_handle(entity),), f"Ошибка раскрытия массива: {error}")
        return []


def _insert_instance_geometries(
    entity: Any,
    factor: float,
    components_by_instance: InsertComponents | None = None,
) -> list[dict[str, Any]]:
    """Return one normalized geometry per visible INSERT/MINSERT instance.

    MINSERT is stored as a single DXF entity, but each grid position matters
    for both map visibility and normative geometry. Keeping instances separate
    lets the map query's feature budget work without omitting them from the
    exact backend calculation.
    """
    try:
        instances: list[dict[str, Any]] = []
        references = list(entity.multi_insert()) if _insert_instance_count(entity) > 1 else [entity]
        if components_by_instance is None:
            components_by_instance = _insert_instance_components(entity, factor)
        for reference, components in zip(references, components_by_instance, strict=False):
            geometries = [geometry for geometry, _virtual, _layer in components]
            if geometries:
                instances.append({"type": "GeometryCollection", "geometries": geometries})
            else:
                instances.append({"type": "Point", "coordinates": _point(reference.dxf.insert, factor)})
        return instances
    except Exception:
        return []


def _entity_geometry(entity: Any, factor: float) -> dict[str, Any] | None:
    entity_type = entity.dxftype()
    if entity_type == "ACAD_PROXY_ENTITY":
        # A proxy entity has no reliable domain semantics, but its embedded
        # proxy graphic can safely restore visual context. Never return a
        # partial collection: if the graphic is too dense or malformed, the
        # source DXF remains the single authority and the map shows nothing.
        try:
            geometries: list[dict[str, Any]] = []
            for virtual in entity.virtual_entities():
                # A nested proxy has no safely inspectable geometry and could
                # otherwise recurse indefinitely on malformed proxy graphics.
                if virtual.dxftype() == "ACAD_PROXY_ENTITY":
                    continue
                if len(geometries) >= MAX_PROXY_VIRTUAL_GEOMETRIES:
                    return None
                geometry = _entity_geometry(virtual, factor)
                if geometry is not None:
                    geometries.append(geometry)
        except Exception:
            return None
        return {"type": "GeometryCollection", "geometries": geometries} if geometries else None
    if entity_type in {"DIMENSION", "LEADER", "MLEADER", "MULTILEADER", "MLINE"}:
        # ezdxf exposes the rendered dimension as virtual LINE/ARC/TEXT/INSERT
        # entities (and MLINE/leader strokes) as virtual entities. Keeping that collection preserves the drafting context
        # without turning annotation geometry into a planting constraint.
        try:
            geometries = [_entity_geometry(virtual, factor) for virtual in entity.virtual_entities()]
        except Exception:
            return None
        geometries = [geometry for geometry in geometries if geometry is not None]
        return {"type": "GeometryCollection", "geometries": geometries} if geometries else None
    if entity_type == "HELIX":
        try:
            points = [_point(point, factor) for point in entity.flattening(0.1, segments=16)]
        except Exception:
            return None
        return {"type": "LineString", "coordinates": points} if len(points) >= 2 else None
    if entity_type == "MESH":
        # A mesh has no single planar boundary. Preserve each face as a
        # polygon; OpenLayers can draw the resulting MultiPolygon and the
        # geometry remains clearly marked as source-only context.
        if _mesh_face_count(entity) > MAX_RENDERED_MESH_FACES:
            return _mesh_anchor(entity, factor)
        try:
            data = entity.get_data()
            polygons: list[list[list[list[float]]]] = []
            for face in data.faces:
                points = [_point(data.vertices[index], factor) for index in face if 0 <= index < len(data.vertices)]
                if len(points) < 3:
                    continue
                if points[-1] != points[0]:
                    points.append(points[0])
                polygons.append([points])
            return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None
        except Exception:
            return None
    if entity_type == "WIPEOUT":
        try:
            boundary = entity.boundary_path_wcs
            boundary = boundary() if callable(boundary) else boundary
            points = [_point(point, factor) for point in boundary]
        except Exception:
            return None
        if len(points) >= 3:
            if points[-1] != points[0]:
                points.append(points[0])
            return {"type": "Polygon", "coordinates": [points]}
        return None
    if entity_type == "LINE":
        return {"type": "LineString", "coordinates": [_point(entity.dxf.start, factor), _point(entity.dxf.end, factor)]}
    if entity_type == "LWPOLYLINE":
        coordinates = _lwpolyline_points(entity, factor)
        wide_geometry = _wide_polyline_geometry(coordinates, _polyline_width(entity) * factor)
        if wide_geometry is not None:
            return wide_geometry
        if entity.closed and len(coordinates) >= 3:
            if coordinates[-1] != coordinates[0]:
                coordinates.append(coordinates[0])
            return {"type": "Polygon", "coordinates": [coordinates]}
        return {"type": "LineString", "coordinates": coordinates} if len(coordinates) >= 2 else None
    if entity_type == "POLYLINE":
        if entity.is_poly_face_mesh:
            if _mesh_face_count(entity) > MAX_RENDERED_MESH_FACES:
                return _mesh_anchor(entity, factor)
            polygons: list[list[list[list[float]]]] = []
            try:
                for face in entity.faces():
                    points = [_point(vertex.dxf.location, factor) for vertex in face if int(vertex.dxf.get("flags", 0) or 0) != 128]
                    if len(points) < 3:
                        continue
                    if points[-1] != points[0]:
                        points.append(points[0])
                    polygons.append([points])
            except Exception:
                return None
            return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None
        if entity.is_polygon_mesh:
            if _mesh_face_count(entity) > MAX_RENDERED_MESH_FACES:
                return _mesh_anchor(entity, factor)
            polygons = []
            try:
                m_count = int(entity.dxf.m_count)
                n_count = int(entity.dxf.n_count)
                m_range = range(m_count if entity.is_m_closed else max(0, m_count - 1))
                n_range = range(n_count if entity.is_n_closed else max(0, n_count - 1))
                for m_index in m_range:
                    for n_index in n_range:
                        corners = [
                            entity.get_mesh_vertex((m_index, n_index)).dxf.location,
                            entity.get_mesh_vertex(((m_index + 1) % m_count, n_index)).dxf.location,
                            entity.get_mesh_vertex(((m_index + 1) % m_count, (n_index + 1) % n_count)).dxf.location,
                            entity.get_mesh_vertex((m_index, (n_index + 1) % n_count)).dxf.location,
                        ]
                        points = [_point(corner, factor) for corner in corners]
                        points.append(points[0])
                        polygons.append([points])
            except Exception:
                return None
            return {"type": "MultiPolygon", "coordinates": polygons} if polygons else None
        if entity.is_3d_polyline:
            coordinates = [_point(vertex.dxf.location, factor) for vertex in entity.vertices]
            if entity.is_closed and coordinates and coordinates[-1] != coordinates[0]:
                coordinates.append(coordinates[0])
            return {"type": "LineString", "coordinates": coordinates} if len(coordinates) >= 2 else None
        coordinates = _polyline_points(entity, factor)
        wide_geometry = _wide_polyline_geometry(coordinates, _polyline_width(entity) * factor)
        if wide_geometry is not None:
            return wide_geometry
        if entity.is_closed and len(coordinates) >= 3:
            if coordinates[-1] != coordinates[0]:
                coordinates.append(coordinates[0])
            return {"type": "Polygon", "coordinates": [coordinates]}
        return {"type": "LineString", "coordinates": coordinates} if len(coordinates) >= 2 else None
    if entity_type == "CIRCLE":
        center = entity.dxf.center
        radius = float(entity.dxf.radius)
        coordinates = [
            _ocs_point(entity, Vec3(center.x + radius * cos(index * 2 * pi / 32), center.y + radius * sin(index * 2 * pi / 32), center.z), factor)
            for index in range(33)
        ]
        return {"type": "Polygon", "coordinates": [coordinates]}
    if entity_type == "ARC":
        center = entity.dxf.center
        radius = float(entity.dxf.radius)
        start = float(entity.dxf.start_angle)
        sweep = (float(entity.dxf.end_angle) - start) % 360
        coordinates = []
        for index in range(25):
            angle = (start + sweep * index / 24) * pi / 180
            coordinates.append(_ocs_point(entity, Vec3(center.x + radius * cos(angle), center.y + radius * sin(angle), center.z), factor))
        return {"type": "LineString", "coordinates": coordinates}
    if entity_type in {"ELLIPSE", "SPLINE"}:
        try:
            points = [_point(point, factor) for point in entity.flattening(0.1, segments=16 if entity_type == "ELLIPSE" else 8)]
        except Exception:
            return None
        # Full ellipses and genuinely closed splines are valid area boundaries
        # in CAD plans. Do not infer closure from a DXF flag alone: only close
        # a curve when its flattened endpoints actually coincide.
        if len(points) >= 3 and max(abs(points[0][axis] - points[-1][axis]) for axis in (0, 1)) <= 1e-5:
            points[-1] = points[0]
            return {"type": "Polygon", "coordinates": [points]}
        return {"type": "LineString", "coordinates": points} if len(points) >= 2 else None
    if entity_type == "MPOLYGON":
        return mpolygon_geometry(entity, factor, _hatch_polygon_geometry)
    if entity_type == "HATCH":
        return hatch_geometry(entity, factor, _hatch_polygon_geometry)
    if entity_type in {"3DFACE", "SOLID", "TRACE"}:
        # SOLID and TRACE use the DXF storage order 0, 1, 2, 3, where 2 and
        # 3 are opposite corners rather than the next perimeter vertex. A
        # literal order creates a bow-tie polygon (and can crash GEOS during
        # a union).  3DFACE already stores its vertices around the face.
        vertex_names = ("vtx0", "vtx1", "vtx3", "vtx2") if entity_type in {"SOLID", "TRACE"} else ("vtx0", "vtx1", "vtx2", "vtx3")
        points = [
            _ocs_point(entity, point, factor) if entity_type in {"SOLID", "TRACE"} else _point(point, factor)
            for name in vertex_names
            if (point := entity.dxf.get(name)) is not None
        ]
        if len(points) < 3:
            return None
        if points[-1] != points[0]:
            points.append(points[0])
        return {"type": "Polygon", "coordinates": [points]}
    if entity_type == "POINT":
        return {"type": "Point", "coordinates": _point(entity.dxf.location, factor)}
    if entity_type in {"PDFUNDERLAY", "PDFREFERENCE", "DWFUNDERLAY", "DGNUNDERLAY"}:
        return _underlay_clip_geometry(entity, factor)
    if entity_type == "IMAGE":
        # Raster pixels live outside the DXF, but their affine frame is part
        # of the drawing. Show the frame so an aerial/survey underlay cannot
        # silently disappear or distort the planning extent.
        try:
            insert = entity.dxf.insert
            u_pixel = entity.dxf.u_pixel
            v_pixel = entity.dxf.v_pixel
            boundary = list(getattr(entity, "boundary_path", []) or [])
            if not boundary:
                size = entity.dxf.image_size
                boundary = [(0, 0), (float(size.x), 0), (float(size.x), float(size.y)), (0, float(size.y))]
            elif len(boundary) == 2:
                # DXF stores an unclipped IMAGE boundary as opposite corners.
                # Expand it before converting the affine pixel frame to map
                # coordinates.
                minimum, maximum = boundary
                boundary = [
                    minimum,
                    (float(maximum[0]), float(minimum[1])),
                    maximum,
                    (float(minimum[0]), float(maximum[1])),
                ]
            points = [
                [
                    round((float(insert.x) + float(u_pixel.x) * float(pixel[0]) + float(v_pixel.x) * float(pixel[1])) * factor, 6),
                    round((float(insert.y) + float(u_pixel.y) * float(pixel[0]) + float(v_pixel.y) * float(pixel[1])) * factor, 6),
                ]
                for pixel in boundary
            ]
        except Exception:
            return None
        if len(points) < 3:
            return None
        if points[-1] != points[0]:
            points.append(points[0])
        return {"type": "Polygon", "coordinates": [points]}
    if entity_type == "INSERT":
        geometries = _insert_instance_geometries(entity, factor)
        if geometries:
            # Direct callers receive the first visible instance. The reader
            # expands MINSERT into separate top-level features below.
            return geometries[0]
        return {"type": "Point", "coordinates": _point(entity.dxf.insert, factor)}
    if entity_type in {"TEXT", "ATTDEF"}:
        # TEXT keeps its insertion point in OCS (unlike MTEXT, whose layout
        # point is already exposed by ezdxf in world coordinates).
        return {"type": "Point", "coordinates": _ocs_point(entity, entity.dxf.insert, factor)}
    if entity_type == "MTEXT":
        return {"type": "Point", "coordinates": _point(entity.dxf.insert, factor)}
    if entity_type in {"RAY", "XLINE"}:
        # Infinite construction lines cannot be represented without a
        # viewport. Keep their anchor visible and mark it as a simplified
        # source geometry; this avoids inventing a drawing extent.
        return {"type": "Point", "coordinates": _point(entity.dxf.start, factor)}
    return None


def _geometry_bounds(features: list[dict[str, Any]]) -> list[float] | None:
    coordinates: list[list[float]] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if "coordinates" in value:
                collect(value["coordinates"])
            if "geometries" in value:
                collect(value["geometries"])
        elif isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            coordinates.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for feature in features:
        collect(feature["geometry"])
    if not coordinates:
        return None
    xs = [item[0] for item in coordinates]
    ys = [item[1] for item in coordinates]
    return [min(xs), min(ys), max(xs), max(ys)]


def _has_only_finite_coordinates(geometry: dict[str, Any]) -> bool:
    """Reject GeoJSON that cannot be rendered or indexed safely.

    DXF permits floating-point values such as ``NaN`` and ``Infinity``. They
    are not useful map coordinates: OpenLayers cannot render them reliably,
    while Shapely may only fail later during a union or a viewport query. Keep
    the original bytes for audit/export, but never let one malformed entity
    poison the normalized geometry snapshot.
    """

    def walk(value: Any) -> bool:
        if isinstance(value, dict):
            if "coordinates" in value:
                return walk(value["coordinates"])
            if "geometries" in value:
                return all(walk(item) for item in value["geometries"])
            return False
        if isinstance(value, (list, tuple)):
            if value and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value):
                return len(value) >= 2 and all(isfinite(float(item)) for item in value)
            return bool(value) and all(walk(item) for item in value)
        return False

    return walk(geometry)


def _has_polygon(geometry: dict[str, Any]) -> bool:
    geometry_type = geometry.get("type")
    if geometry_type in {"Polygon", "MultiPolygon"}:
        return True
    if geometry_type == "GeometryCollection":
        return any(_has_polygon(item) for item in geometry.get("geometries", []) if isinstance(item, dict))
    return False


def _coordinate_reference(document: Any) -> CoordinateReference:
    geodata = document.modelspace().get_geodata()
    if geodata is not None:
        try:
            epsg, xy_ordering = geodata.get_crs()
            return CoordinateReference(
                status="declared",
                crs_id=f"EPSG:{epsg}",
                name=f"Система координат EPSG:{epsg}",
                source="dxf_geodata",
                axis_order="xy" if xy_ordering else "yx",
                evidence="Идентификатор прочитан из DXF GEODATA; требуется сверка с контрольной точкой городской основы.",
            )
        except Exception:
            definition_present = bool(str(getattr(geodata, "coordinate_system_definition", "")).strip())
            return CoordinateReference(
                status="local",
                source="dxf_geodata",
                evidence="В DXF есть GEODATA, но CRS не распознан по EPSG." if definition_present else "В DXF есть локальная привязка без машиночитаемого определения CRS.",
            )

    # Green Atlas fixtures use a documented local tangent approximation, not
    # a projected CRS. Accept it only as an atomic declaration: exposing a
    # half-parsed origin would make downstream enrichment appear more certain
    # than the source permits.
    custom = document.header.custom_vars
    values = {
        "source": custom.get("GREEN_ATLAS_HORIZONTAL_SOURCE"),
        "origin": custom.get("GREEN_ATLAS_ORIGIN_WGS84"),
        "projection": custom.get("GREEN_ATLAS_LOCAL_PROJECTION"),
        "earth_radius": custom.get("GREEN_ATLAS_EARTH_RADIUS_M"),
    }
    if not any(values.values()):
        return CoordinateReference()
    if not all(values.values()):
        return CoordinateReference(evidence="Локальная WGS84-привязка DXF неполна и не используется.")
    try:
        origin_parts = [float(part.strip()) for part in str(values["origin"]).split(",")]
        earth_radius = float(values["earth_radius"])
    except (TypeError, ValueError):
        return CoordinateReference(evidence="Локальная WGS84-привязка DXF повреждена и не используется.")
    if (
        len(origin_parts) != 2
        or not all(isfinite(value) for value in origin_parts)
        or not -90 <= origin_parts[0] <= 90
        or not -180 <= origin_parts[1] <= 180
        or str(values["projection"]) != "local_equirectangular_wgs84"
        or not isfinite(earth_radius)
        or not 6_000_000 <= earth_radius <= 7_000_000
    ):
        return CoordinateReference(evidence="Локальная WGS84-привязка DXF повреждена и не используется.")
    return CoordinateReference(
        status="declared",
        name="Локальная эквиректангулярная аппроксимация WGS84",
        source="dxf_custom_georeference",
        axis_order="xy",
        origin_wgs84=origin_parts,
        local_projection="local_equirectangular_wgs84",
        earth_radius_m=earth_radius,
        horizontal_source=str(values["source"]),
        evidence=(
            "Из DXF прочитана объявленная локальная WGS84-привязка; "
            "преобразование воспроизводимо, но не сверено по контрольным точкам и не является EPSG/GEODATA."
        ),
    )


class EzdxfReader:
    """Reads supported DXF entities and normalizes coordinates to meters."""

    def __init__(self, *, capacity: SourceGeometryCapacity | None = None) -> None:
        self.capacity = capacity or SourceGeometryCapacity()

    def read_prepared_file(self, path: Path) -> DxfImportResult:
        """Use ezdxf's file stream for a verified prepared DXF in a bounded worker.

        Normalization is identical to upload reading. Avoid constructing a full
        Unicode StringIO copy alongside the SDK document for large local sources.
        This never rewrites the source or invokes recover/audit repairs.
        """
        if path.suffix.lower() != ".dxf":
            raise ValueError("Поддерживаются только DXF-файлы")
        document = ezdxf.readfile(path)
        with indexed_acis_lookup(document):
            return self._normalize_document(document)

    def read(self, filename: str, content: bytes | bytearray) -> DxfImportResult:
        if not filename.lower().endswith(".dxf"):
            raise ValueError("Поддерживаются только DXF-файлы")
        if not content:
            raise ValueError("Файл пуст")
        try:
            document = _read_document(content)
        except Exception as error:
            raise ValueError("DXF повреждён или имеет неподдерживаемую структуру") from error

        with indexed_acis_lookup(document):
            return self._normalize_document(document)

    def _normalize_document(self, document: Drawing) -> DxfImportResult:
        prepare_multileader_transforms(document)
        preview_provenance = read_preview_marker(document)
        styles = SourceStyleResolver(document)
        unit_code = int(document.header.get("$INSUNITS", 0) or 0)
        units_assumed = unit_code not in DXF_UNIT_FACTORS
        units, factor = DXF_UNIT_FACTORS.get(unit_code, ("м (принято)", 1.0))
        entities = list(document.modelspace())
        terrain_document_evidence = _terrain_document_evidence(document)
        vertical_primitives = [
            primitive
            for index, entity in enumerate(entities)
            if (
                primitive := _vertical_primitive(
                    entity,
                    factor,
                    units,
                    f"dxf-vertical-{index}",
                    terrain_document_evidence,
                )
            ) is not None
        ]
        paper_entity_count = 0
        for layout in document.layouts:
            if str(layout.name).lower() == "model":
                continue
            try:
                paper_entity_count += sum(1 for _ in layout)
            except Exception:
                # A malformed paper-space layout must not prevent model-space
                # import; the original file is still preserved for export.
                continue
        if not entities and not paper_entity_count:
            raise ValueError("В DXF нет объектов модели")

        counts = Counter(str(entity.dxf.layer) for entity in entities)
        entity_types_by_layer: dict[str, Counter[str]] = {}
        for entity in entities:
            entity_types_by_layer.setdefault(str(entity.dxf.layer), Counter())[entity.dxftype()] += 1
        unsupported = Counter(entity.dxftype() for entity in entities if entity.dxftype() not in SUPPORTED_TYPES)
        unsupported_by_layer: dict[str, Counter[str]] = {}
        unrenderable_geometry_count_by_layer: Counter[str] = Counter()
        block_failures: list[BlockGeometryFailure] = []
        for entity in entities:
            entity_type = entity.dxftype()
            if entity_type not in SUPPORTED_TYPES:
                source_layer = str(entity.dxf.layer)
                unsupported_by_layer.setdefault(source_layer, Counter())[entity_type] += 1
        # Most block definitions use layer 0 and correctly inherit the INSERT
        # layer. Only split references that contain an explicit different
        # layer: this preserves the compact, fast representation for ordinary
        # repeated symbols while exposing semantically meaningful components.
        explicit_block_components: dict[int, list[list[tuple[dict[str, Any], Any, str]]]] = {}
        compact_block_geometries: dict[int, list[dict[str, Any]]] = {}
        block_definition_component_cache: dict[tuple[str, str], list[tuple[str, str]] | None] = {}
        context_only_block_inserts: set[int] = set()
        oversized_minsert_instances: dict[int, int] = {}
        oversized_mesh_faces: dict[int, int] = {}
        for index, entity in enumerate(entities):
            if _is_mesh_context(entity):
                face_count = _mesh_face_count(entity)
                if face_count > MAX_RENDERED_MESH_FACES:
                    oversized_mesh_faces[index] = face_count
            if entity.dxftype() != "INSERT":
                continue
            instance_count = _insert_instance_count(entity)
            expansion = inspect_block_expansion(
                entity,
                max_components=self.capacity.max_features,
                max_array_instances=MAX_EXPANDED_MINSERT_INSTANCES,
                max_depth=MAX_NESTED_BLOCK_DEPTH,
            )
            if expansion.blocked:
                # ``multi_insert`` would materialize every member just to
                # audit its layers, which defeats the import safety limit.
                # The same applies when the array lives inside a nested
                # symbol: showing the first child would make unseen geometry
                # look safely available.
                oversized_minsert_instances[index] = expansion.components
                continue
            parent_layer = str(entity.dxf.layer)
            definition_key = (str(entity.dxf.name), parent_layer)
            if definition_key not in block_definition_component_cache:
                block_definition_component_cache[definition_key] = _block_definition_components(entity, parent_layer)
            definition_components = block_definition_component_cache[definition_key]
            for entity_type, component_layer in definition_components or []:
                if entity_type in SUPPORTED_TYPES:
                    continue
                unsupported[entity_type] += 1
                unsupported_by_layer.setdefault(component_layer, Counter())[entity_type] += 1
                counts[component_layer] += 1
                entity_types_by_layer.setdefault(component_layer, Counter())[entity_type] += 1
            failures: list[BlockGeometryFailure] = []
            instances = _insert_instance_components(entity, factor, failures)
            block_failures.extend(failures)
            diagnosed_components: Counter[tuple[str, str]] = Counter()
            for failure in failures:
                if failure.entity_type not in SUPPORTED_TYPES or failure.entity_type in CONTEXT_ONLY_ENTITY_TYPES:
                    continue
                component_layer = failure.source_layer
                unrenderable_geometry_count_by_layer[component_layer] += 1
                diagnosed_components[failure.entity_type, component_layer] += 1
                if component_layer != parent_layer:
                    counts[component_layer] += 1
                entity_types_by_layer.setdefault(component_layer, Counter())[failure.entity_type] += 1
            components = [virtual for instance in instances for _geometry, virtual, _layer in instance]
            if not components:
                if definition_components is None:
                    # An unresolved or corrupted block could contain any
                    # physical footprint on the INSERT's effective layer.
                    unrenderable_geometry_count_by_layer[parent_layer] += 1
                else:
                    for entity_type, component_layer in definition_components:
                        if entity_type in SUPPORTED_TYPES and entity_type not in CONTEXT_ONLY_ENTITY_TYPES:
                            if diagnosed_components[entity_type, component_layer]:
                                diagnosed_components[entity_type, component_layer] -= 1
                                continue
                            unrenderable_geometry_count_by_layer[component_layer] += 1
                            # A failed virtual transform can otherwise hide
                            # an explicitly layered component from the whole
                            # mapping table. Retain its source-layer record
                            # so the operator can exclude it consciously.
                            if component_layer != parent_layer:
                                counts[component_layer] += 1
                            entity_types_by_layer.setdefault(component_layer, Counter())[entity_type] += 1
            # A title/symbol block can sit on a layer named BUILDING or ROAD.
            # Mapping its text anchor as an obstacle creates an invented
            # setback. The existing inspection pass already has every virtual
            # component, so classify it here instead of expanding the block a
            # third time during feature creation.
            if (
                not components
                and definition_components is not None
                and all(
                    entity_type in CONTEXT_ONLY_ENTITY_TYPES
                    for entity_type, _component_layer in definition_components
                )
            ) or (components and all(
                virtual.dxftype() in CONTEXT_ONLY_ENTITY_TYPES or _is_mesh_context(virtual)
                for virtual in components
            )):
                context_only_block_inserts.add(index)
            if not any(component_layer != parent_layer for instance in instances for _geometry, _virtual, component_layer in instance):
                # The inspection pass already transformed this exact INSERT.
                # Reuse its geometry; keep the cache within this one document
                # so a different instance/transform can never share its result.
                compact_block_geometries[index] = _insert_instance_geometries(entity, factor, instances)
                if not compact_block_geometries[index] and instance_count == 1:
                    # Match the existing single-INSERT fallback. The layer's
                    # unrenderable diagnosis still blocks physical use; its
                    # original anchor remains available for inspection.
                    compact_block_geometries[index] = [
                        {"type": "Point", "coordinates": _point(entity.dxf.insert, factor)}
                    ]
                continue
            explicit_block_components[index] = instances
            for instance in instances:
                for _geometry, virtual, component_layer in instance:
                    if component_layer == parent_layer:
                        continue
                    counts[component_layer] += 1
                    entity_types_by_layer.setdefault(component_layer, Counter())[virtual.dxftype()] += 1
        simplified = Counter(entity.dxftype() for entity in entities if entity.dxftype() in {"RAY", "XLINE"})
        raster_underlays = sum(1 for entity in entities if entity.dxftype() == "IMAGE")
        document_underlays = Counter(entity.dxftype() for entity in entities if entity.dxftype() in {"PDFUNDERLAY", "PDFREFERENCE", "DWFUNDERLAY", "DGNUNDERLAY"})
        proxy_entities = sum(1 for entity in entities if entity.dxftype() == "ACAD_PROXY_ENTITY")
        features: list[dict[str, Any]] = []
        nonfinite_geometry_by_layer: Counter[str] = Counter()
        rendered_coordinate_count = 0

        def append_feature(feature: dict[str, Any]) -> None:
            """Keep source geometry complete within explicit server capacity.

            Display detail is bounded by the viewport query. Capacity failure
            aborts this read before any project/source revision is persisted.
            """
            nonlocal rendered_coordinate_count
            properties = feature.get("properties", {})
            source_layer = str(properties.get("source_layer", "0"))
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict) or not _has_only_finite_coordinates(geometry):
                nonfinite_geometry_by_layer[source_layer] += 1
                return
            coordinate_count = _geometry_coordinate_count(geometry)
            self.capacity.check(
                features=len(features) + 1,
                coordinates=rendered_coordinate_count + coordinate_count,
                feature_coordinates=coordinate_count,
                layer=source_layer,
            )
            features.append(feature)
            rendered_coordinate_count += coordinate_count
        for index, entity in enumerate(entities):
            if index in oversized_mesh_faces:
                source_layer = str(entity.dxf.layer)
                fallback_color = LAYER_COLORS[list(counts).index(source_layer) % len(LAYER_COLORS)]
                face_count = oversized_mesh_faces[index]
                anchor = _mesh_anchor(entity, factor)
                if anchor is None:
                    continue
                append_feature({
                    "type": "Feature",
                    "id": f"dxf-{index}-simplified-mesh",
                    "properties": {
                        "source_layer": source_layer,
                        "kind": LayerKind.IGNORE.value,
                        "entity_type": entity.dxftype(),
                        "source_handle": str(entity.dxf.get("handle", "")),
                        **styles.entity(entity, source_layer, fallback_color),
                        "source_context_only": True,
                        "source_mesh_context": True,
                        "source_mesh_faces": face_count,
                        "source_mesh_simplified": True,
                        "source_mesh_note": "Поверхность слишком велика для безопасного развёртывания на карте. Показана точка привязки; исходная геометрия сохранена в DXF.",
                    },
                    "geometry": anchor,
                })
                continue
            if index in oversized_minsert_instances:
                source_layer = str(entity.dxf.layer)
                fallback_color = LAYER_COLORS[list(counts).index(source_layer) % len(LAYER_COLORS)]
                component_count = oversized_minsert_instances[index]
                instance_count = _insert_instance_count(entity)
                append_feature({
                    "type": "Feature",
                    "id": f"dxf-{index}-unexpanded-array",
                    "properties": {
                        "source_layer": source_layer,
                        "kind": LayerKind.IGNORE.value,
                        "entity_type": "INSERT",
                        "source_handle": str(entity.dxf.get("handle", "")),
                        **styles.entity(entity, source_layer, fallback_color),
                        "source_block": str(entity.dxf.name),
                        "source_block_instances": instance_count,
                        "source_block_components": component_count,
                        "source_unexpanded_array": True,
                        "source_context_only": True,
                        "source_analysis_blocking": True,
                        "source_array_note": "Массив или вложенный блок слишком велик для безопасного развёртывания. Он сохранён в исходном DXF, но расчёт зон заблокирован до подготовки фрагмента.",
                    },
                    # The anchor makes this unresolved source discoverable on
                    # the map without pretending to show its full footprint.
                    "geometry": {"type": "Point", "coordinates": _point(entity.dxf.insert, factor)},
                })
                continue
            if index in explicit_block_components:
                parent_layer = str(entity.dxf.layer)
                block_name = str(entity.dxf.name)
                attributes = {str(attribute.dxf.tag): str(attribute.dxf.text) for attribute in entity.attribs}
                instances = explicit_block_components[index]
                for instance_index, components in enumerate(instances, start=1):
                    for component_index, (geometry, virtual, source_layer) in enumerate(components, start=1):
                        fallback_color = LAYER_COLORS[list(counts).index(source_layer) % len(LAYER_COLORS)]
                        properties: dict[str, Any] = {
                            "source_layer": source_layer,
                            "kind": suggest_layer_kind(source_layer).value,
                            "entity_type": virtual.dxftype(),
                            # A virtual component has no persistent DXF handle;
                            # retain its parent handle as the audit link.
                            "source_handle": str(entity.dxf.get("handle", "")),
                            **styles.entity(virtual, source_layer, fallback_color),
                            **component_definition_provenance(virtual),
                            **_source_vertical_properties(virtual, factor, attributes),
                            "source_block": block_name,
                            "source_block_parent_layer": parent_layer,
                            "source_block_component": component_index,
                            "source_block_instance": instance_index,
                            "source_block_instances": len(instances),
                            "source_attributes": attributes,
                        }
                        if virtual.dxftype() in {"RAY", "XLINE"}:
                            properties.update({
                                "geometry_fallback": True,
                                "geometry_fallback_reason": "Бесконечная конструктивная линия показана точкой привязки.",
                            })
                        if virtual.dxftype() in {"TEXT", "MTEXT", "ATTDEF"}:
                            height_attribute = "char_height" if virtual.dxftype() == "MTEXT" else "height"
                            properties.update({
                                "source_text": str(virtual.plain_text()),
                                "source_text_height": round(float(virtual.dxf.get(height_attribute, 2.5) or 2.5) * factor, 3),
                                "source_rotation": round(float(virtual.dxf.get("rotation", 0) or 0), 3),
                            })
                        if virtual.dxftype() in {"LWPOLYLINE", "POLYLINE"}:
                            source_width_m = round(_polyline_width(virtual) * factor, 6)
                            if source_width_m > 0:
                                properties.update({
                                    "source_polyline_width_m": source_width_m,
                                    "source_width_mode": "conservative_max",
                                })
                                center_geometry = _polyline_center_geometry(virtual, factor)
                                if center_geometry is not None:
                                    properties["source_polyline_center_geometry"] = center_geometry
                        if virtual.dxftype() in CONTEXT_ONLY_ENTITY_TYPES or _is_mesh_context(virtual):
                            properties["source_context_only"] = True
                        if _is_mesh_context(virtual):
                            properties.update({
                                "source_mesh_context": True,
                                "source_mesh_faces": _mesh_face_count(virtual),
                                "source_mesh_simplified": _mesh_face_count(virtual) > MAX_RENDERED_MESH_FACES,
                            })
                        append_feature({
                            "type": "Feature",
                            "id": f"dxf-{index}-instance-{instance_index}-component-{component_index}",
                            "properties": properties,
                            "geometry": geometry,
                        })
                continue
            block_instances = _insert_instance_count(entity) if entity.dxftype() == "INSERT" else 1
            if index in compact_block_geometries:
                geometries = compact_block_geometries.pop(index)
            else:
                geometries = _insert_instance_geometries(entity, factor) if entity.dxftype() == "INSERT" and block_instances > 1 else [_entity_geometry(entity, factor)]
            geometries = [geometry for geometry in geometries if geometry is not None]
            if not geometries:
                source_layer = str(entity.dxf.layer)
                # Annotation and external-reference classes deliberately do
                # not define a finite physical footprint. Every other
                # supported top-level entity that fails normalisation leaves
                # an unknown gap and must be visible to the layer-mapping
                # safety gate.
                if (
                    entity.dxftype() not in CONTEXT_ONLY_ENTITY_TYPES
                    and not _is_mesh_context(entity)
                    and entity.dxftype() not in unsupported
                    and entity.dxftype() != "INSERT"
                ):
                    unrenderable_geometry_count_by_layer[source_layer] += 1
                continue
            source_layer = str(entity.dxf.layer)
            fallback_color = LAYER_COLORS[list(counts).index(source_layer) % len(LAYER_COLORS)]
            for instance_index, geometry in enumerate(geometries):
                source_attributes = (
                    {str(attribute.dxf.tag): str(attribute.dxf.text) for attribute in entity.attribs}
                    if entity.dxftype() == "INSERT"
                    else None
                )
                properties: dict[str, Any] = {
                    "source_layer": source_layer,
                    "kind": suggest_layer_kind(source_layer).value,
                    "entity_type": entity.dxftype(),
                    "source_handle": str(entity.dxf.get("handle", "")),
                    **straight_axis_provenance(entity, geometry),
                    **styles.entity(entity, source_layer, fallback_color),
                    **_source_vertical_properties(entity, factor, source_attributes),
                }
                if entity.dxftype() in simplified:
                    properties.update({
                        "geometry_fallback": True,
                        "geometry_fallback_reason": "Бесконечная конструктивная линия показана точкой привязки.",
                    })
                if entity.dxftype() in {"TEXT", "MTEXT", "ATTDEF"}:
                    height_attribute = "char_height" if entity.dxftype() == "MTEXT" else "height"
                    properties.update({
                        "source_text": str(entity.plain_text()),
                        "source_text_height": round(float(entity.dxf.get(height_attribute, 2.5) or 2.5) * factor, 3),
                        "source_rotation": round(float(entity.dxf.get("rotation", 0) or 0), 3),
                    })
                if entity.dxftype() == "IMAGE":
                    properties.update({
                        "source_raster_frame": True,
                        "source_context_only": True,
                        "source_raster_note": "В DXF сохранена рамка внешней растровой подложки; пиксели не встраиваются в DXF.",
                    })
                if entity.dxftype() in {"PDFUNDERLAY", "PDFREFERENCE", "DWFUNDERLAY", "DGNUNDERLAY"}:
                    properties.update({
                        "source_underlay_frame": True,
                        "source_context_only": True,
                        "source_underlay_type": entity.dxftype(),
                        "source_underlay_note": "Показана внутренняя граница обрезки внешней подложки; содержимое PDF/DWF/DGN не встраивается в DXF.",
                    })
                if entity.dxftype() == "ACAD_PROXY_ENTITY":
                    properties.update({
                        "source_proxy_graphic": True,
                        "source_context_only": True,
                        "source_proxy_geometries": len(geometry.get("geometries", [])) if geometry.get("type") == "GeometryCollection" else 1,
                        "source_proxy_note": "Показана доступная proxy-графика неизвестного CAD-объекта; она не участвует в нормативном расчёте.",
                    })
                if entity.dxftype() in {"LWPOLYLINE", "POLYLINE"}:
                    source_width_m = round(_polyline_width(entity) * factor, 6)
                    if source_width_m > 0:
                        properties.update({
                            "source_polyline_width_m": source_width_m,
                            "source_width_mode": "conservative_max",
                        })
                        center_geometry = _polyline_center_geometry(entity, factor)
                        if center_geometry is not None:
                            properties["source_polyline_center_geometry"] = center_geometry
                if entity.dxftype() == "INSERT":
                    properties.update({
                        "source_block": str(entity.dxf.name),
                        "source_rotation": round(float(entity.dxf.get("rotation", 0) or 0), 3),
                        "source_scale": [round(float(entity.dxf.get("xscale", 1) or 1), 4), round(float(entity.dxf.get("yscale", 1) or 1), 4)],
                        "source_attributes": source_attributes or {},
                        "block_rendered": geometry["type"] == "GeometryCollection",
                        "source_block_instances": block_instances,
                        "source_block_instance": instance_index + 1,
                    })
                    if index in context_only_block_inserts:
                        properties["source_context_only"] = True
                        properties["source_block_context_note"] = "Блок состоит только из подписи или справочной CAD-геометрии и не участвует в расчёте."
                if entity.dxftype() in CONTEXT_ONLY_ENTITY_TYPES or _is_mesh_context(entity):
                    properties["source_context_only"] = True
                if _is_mesh_context(entity):
                    properties.update({
                        "source_mesh_context": True,
                        "source_mesh_faces": _mesh_face_count(entity),
                        "source_mesh_simplified": _mesh_face_count(entity) > MAX_RENDERED_MESH_FACES,
                    })
                suffix = f"-instance-{instance_index + 1}" if len(geometries) > 1 else ""
                append_feature({
                    "type": "Feature",
                    "id": f"dxf-{index}{suffix}",
                    "properties": properties,
                    "geometry": geometry,
                })
        # A valid DXF may contain only proxy/3D/annotation entities that the
        # viewer cannot flatten yet. Keep the project importable so the user
        # can inspect layer metadata, map a different layer, or download the
        # untouched source instead of losing the whole drawing at upload.

        # ``append_feature`` rejects non-finite coordinates before they enter
        # the bounded snapshot. Never repair them into an invented point.

        # A name such as ``WASTE_SITES`` can contain the heuristic token
        # ``site`` while describing observations, not a planning boundary.
        # Only polygon geometry may turn that name into SITE_BORDER.
        polygon_layers = {
            feature["properties"]["source_layer"]
            for feature in features
            if _has_polygon(feature["geometry"]) and not feature["properties"].get("source_context_only")
        }
        for feature in features:
            if feature["properties"].get("source_context_only"):
                feature["properties"]["kind"] = LayerKind.IGNORE.value
            elif feature["properties"]["kind"] == LayerKind.SITE_BORDER.value and feature["properties"]["source_layer"] not in polygon_layers:
                feature["properties"]["kind"] = LayerKind.IGNORE.value

        features_by_layer: dict[str, list[dict[str, Any]]] = {}
        for feature in features:
            source_layer = feature["properties"].get("source_layer")
            if source_layer is not None:
                features_by_layer.setdefault(source_layer, []).append(feature)
        layers: list[Layer] = []
        for index, (name, count) in enumerate(counts.items()):
            kind = suggest_layer_kind(name)
            boundary_candidate = (
                _boundary_candidate(features_by_layer.get(name, []))
                if is_boundary_candidate_name(name)
                else None
            )
            if kind == LayerKind.SITE_BORDER and name not in polygon_layers:
                kind = LayerKind.IGNORE
            if (
                kind == LayerKind.SITE_BORDER
                and boundary_candidate is not None
                and boundary_candidate.status != BoundaryCandidateStatus.USABLE
            ):
                kind = LayerKind.IGNORE
            geometry_complete = not (
                unrenderable_geometry_count_by_layer[name]
                or unsupported_by_layer.get(name)
            )
            confidence, reasons, review_required = assess_layer_suggestion(
                kind,
                entity_types=dict(entity_types_by_layer[name]),
                has_polygon=name in polygon_layers,
                geometry_complete=geometry_complete,
                boundary_candidate=boundary_candidate,
            )
            style = styles.layer(name, LAYER_COLORS[index % len(LAYER_COLORS)])
            layers.append(Layer(
                id=str(uuid5(NAMESPACE_URL, f"dxf-layer:{name}")),
                source_name=name,
                suggested_kind=kind,
                suggestion_confidence=confidence,
                suggestion_reasons=reasons,
                mapping_review_required=review_required,
                mapping_confirmed=not review_required,
                mapped_kind=kind,
                object_count=count,
                bounds=_geometry_bounds(features_by_layer.get(name, [])),
                color=style.color,
                linetype=style.linetype,
                lineweight_mm=style.lineweight_mm,
                entity_types=dict(entity_types_by_layer[name]),
                geometry_complete=geometry_complete,
                unsupported_geometry_types=dict(unsupported_by_layer.get(name, {})),
                unreadable_geometry_count=unrenderable_geometry_count_by_layer[name],
                boundary_candidate=boundary_candidate,
                required=kind == LayerKind.SITE_BORDER,
            ))

        usable_boundaries = [
            layer
            for layer in layers
            if layer.boundary_candidate is not None
            and layer.boundary_candidate.status == BoundaryCandidateStatus.USABLE
        ]
        if len(usable_boundaries) > 1:
            # Several valid surfaces are a semantic choice, not evidence that
            # every one is the project territory. Leave the source import
            # editable and require one explicit operator selection instead of
            # silently preferring a name heuristic.
            for layer in usable_boundaries:
                if layer.suggested_kind == LayerKind.SITE_BORDER:
                    layer.suggested_kind = LayerKind.IGNORE
                    layer.mapped_kind = LayerKind.IGNORE
                    layer.suggestion_confidence = (
                        LayerSuggestionConfidence.LOW
                    )
                    layer.suggestion_reasons = [
                        "Найдено несколько подходящих контуров территории"
                    ]
                    # Choosing one contour is handled by the dedicated
                    # boundary control; excluded alternatives are safe.
                    layer.mapping_review_required = False
                    layer.mapping_confirmed = True
                    layer.required = False

        kind_by_layer = {layer.source_name: layer.suggested_kind for layer in layers}
        for feature in features:
            properties = feature["properties"]
            if properties.get("source_context_only"):
                continue
            source_layer = properties.get("source_layer")
            if source_layer in kind_by_layer:
                properties["kind"] = kind_by_layer[source_layer].value

        warnings = styles.warnings()
        warnings.extend(failure.warning() for failure in block_failures)
        if units_assumed:
            warnings.append("Единицы чертежа не заданы; координаты интерпретированы как метры.")
        if simplified:
            labels = ", ".join(f"{name}: {count}" for name, count in sorted(simplified.items()))
            warnings.append(f"Часть конструктивных линий показана упрощённо в карте: {labels}; исходная геометрия сохранена в DXF.")
        if raster_underlays:
            warnings.append(f"Внешних растровых подложек: {raster_underlays}. На карте показана их рамка; изображение остаётся внешней ссылкой исходного DXF.")
        if document_underlays:
            labels = ", ".join(f"{name}: {count}" for name, count in sorted(document_underlays.items()))
            shown_frames = sum(1 for feature in features if feature["properties"].get("source_underlay_frame"))
            warnings.append(f"Внешних PDF/DWF/DGN-подложек: {labels}. На карте показаны только безопасные внутренние границы обрезки ({shown_frames}); содержимое остаётся внешней ссылкой исходного DXF.")
        if proxy_entities:
            rendered_proxies = sum(1 for feature in features if feature["properties"].get("source_proxy_graphic"))
            warnings.append(f"Proxy-объектов: {proxy_entities}. Доступная proxy-графика показана для {rendered_proxies}; неизвестная семантика не участвует в расчёте, исходный DXF сохранён полностью.")
        if oversized_mesh_faces:
            total = sum(oversized_mesh_faces.values())
            warnings.append(f"Крупных CAD-поверхностей: {len(oversized_mesh_faces)} ({total} граней). На карте показаны точки привязки; поверхности не участвуют в расчёте, исходный DXF сохранён полностью.")
        if oversized_minsert_instances:
            total = sum(oversized_minsert_instances.values())
            warnings.append(f"Крупных блоковых массивов: {len(oversized_minsert_instances)} (не менее {total} компонентов). Исходный DXF сохранён, но расчёт зон заблокирован: подготовьте чертёж фрагментом или уменьшите массив.")
        if nonfinite_geometry_by_layer:
            labels = ", ".join(f"{name}: {count}" for name, count in sorted(nonfinite_geometry_by_layer.items()))
            warnings.append(f"Объекты с некорректными координатами не показаны на карте: {labels}. Исходный DXF сохранён без изменений.")
        if unrenderable_geometry_count_by_layer:
            labels = ", ".join(f"{name}: {count}" for name, count in sorted(unrenderable_geometry_count_by_layer.items()))
            warnings.append(
                "Часть геометрии не удалось прочитать для карты: "
                f"{labels}. Неполный слой нельзя использовать для расчёта ограничений; "
                "проверьте объекты слоя и подготовьте их расчётное представление в полном DXF."
            )
        if unsupported:
            labels = ", ".join(f"{name}: {count}" for name, count in sorted(unsupported.items()))
            warnings.append(
                f"Часть типов доступна только в исходном файле: {labels}. "
                "Если такой слой назначен физическим ограничением, расчёт будет остановлен; "
                "проверьте назначение слоя и подготовьте расчётное представление его физических объектов."
            )
        if not features:
            warnings.append("В карте нет визуализируемой геометрии; исходные объекты и слои сохранены для просмотра и повторного экспорта.")
        if paper_entity_count:
            warnings.append(f"В листах DXF есть объекты ({paper_entity_count}); карта показывает модельное пространство, исходный файл сохранён полностью.")
        # A site can consist of several separate planting fragments. HATCH
        # boundaries and CAD blocks may therefore yield a MultiPolygon (or a
        # geometry collection containing polygons), which is just as valid as
        # one Polygon for both the warning and the later geometry engine.
        site_features = [
            feature
            for feature in features
            if feature["properties"]["kind"] == LayerKind.SITE_BORDER.value
            and _has_polygon(feature["geometry"])
        ]
        if not site_features:
            warnings.append(
                "Авторский контур задаёт область просмотра; расчётный участок ещё не подготовлен."
                if preview_provenance
                else "Не найдена замкнутая граница участка; назначьте корректный слой с полигоном."
            )

        # A title, underlay frame, mesh anchor or infinite construction line
        # can sit kilometres away from the actual territory. It remains
        # available in the map response, but must not make the initial fit so
        # wide that an operator opens an apparently empty drawing. If a file
        # contains only such context, fall back to all visible geometry rather
        # than inventing an extent.
        extent_features = [
            feature for feature in features
            if not feature["properties"].get("source_context_only")
            and not feature["properties"].get("geometry_fallback")
        ] or features

        if preview_provenance and preview_provenance.omitted_entity_count:
            warnings.append(
                "В предварительную карту не перенесены CAD-объекты с неполным "
                f"геометрическим описанием: {preview_provenance.omitted_entity_count}. "
                "Их адреса сохранены в журнале подготовки; требуется проверка полного исходника."
            )
        return DxfImportResult(
            layers=layers,
            geometry=GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": features},
                vertical_primitives=vertical_primitives,
            ),
            dxf_version=document.dxfversion,
            units=units,
            units_assumed=units_assumed,
            entity_count=len(entities) + paper_entity_count,
            bounds=(
                list(preview_provenance.boundary_bounds_m)
                if preview_provenance and preview_provenance.boundary_bounds_m
                else _geometry_bounds(extent_features)
            ),
            warnings=warnings,
            preview_provenance=preview_provenance,
            coordinate_reference=_coordinate_reference(document),
        )
