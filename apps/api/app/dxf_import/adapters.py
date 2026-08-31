from __future__ import annotations

from collections import Counter
from io import StringIO
from math import atan, atan2, cos, isfinite, pi, sin
from numbers import Real
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

import ezdxf
from ezdxf.document import Drawing
from ezdxf.lldxf.tagger import binary_tags_loader
from ezdxf.math import OCS, Vec3
from ezdxf.path import from_hatch, make_path
from shapely import STRtree
from shapely.geometry import LineString as ShapelyLineString, Polygon as ShapelyPolygon

from app.contracts import CoordinateReference, DxfImportResult, GeometrySnapshot, Layer, LayerKind
from app.dxf_import.encoding import decode_text_dxf
from app.dxf_import.units import DXF_UNIT_FACTORS


SUPPORTED_TYPES = {
    "LINE", "LWPOLYLINE", "POLYLINE", "CIRCLE", "ARC", "ELLIPSE", "SPLINE", "HATCH",
    "3DFACE", "SOLID", "TRACE", "POINT", "TEXT", "MTEXT", "INSERT", "DIMENSION", "WIPEOUT",
    # These entities are common in survey/CAD exports. They are rendered as
    # native geometry where possible and never participate in planting rules.
    "LEADER", "MLEADER", "MLINE", "HELIX", "MESH", "RAY", "XLINE", "IMAGE",
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
# The map client keeps at most this many source features in its working cache.
# Matching that bound prevents one dense DXF from becoming a much larger
# server-side GeoJSON snapshot. The untouched DXF blob remains available.
MAX_NORMALIZED_DXF_FEATURES = 25_000
# A single survey polyline or hatch can have far more vertices than a useful
# interactive map can render. Both caps are on the derived GeoJSON only; the
# original drawing is never modified or discarded.
MAX_NORMALIZED_COORDINATES_PER_FEATURE = 20_000
MAX_NORMALIZED_COORDINATES = 250_000

# These entities describe drafting or display context, not a finite physical
# obstacle. They stay visible on the map, but must never acquire a planting
# constraint even if their source layer is mapped manually to a physical kind.
CONTEXT_ONLY_ENTITY_TYPES = {
    "TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER", "MLINE", "HELIX",
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


def _estimated_insert_component_count(entity: Any, depth: int = 0, ancestry: frozenset[str] = frozenset()) -> int:
    """Estimate visible block components without expanding a virtual tree.

    ``virtual_entities`` deliberately renders only the first member of a
    nested MINSERT. Before calling it, inspect the raw block graph and count
    all visible leaves. The same budget protects a normal INSERT which wraps a
    large nested array, or a recursive/corrupt block graph that otherwise
    cannot be safely interpreted as complete geometry.
    """
    if depth >= MAX_NESTED_BLOCK_DEPTH:
        return MAX_EXPANDED_MINSERT_INSTANCES + 1
    instances = _insert_instance_count(entity)
    if instances > MAX_EXPANDED_MINSERT_INSTANCES:
        return instances
    try:
        block_name = str(entity.dxf.name)
        if not block_name or block_name in ancestry:
            return MAX_EXPANDED_MINSERT_INSTANCES + 1
        components = list(entity.block())
    except Exception:
        return MAX_EXPANDED_MINSERT_INSTANCES + 1
    total = 0
    next_ancestry = ancestry | {block_name}
    for component in components:
        nested_count = (
            _estimated_insert_component_count(component, depth + 1, next_ancestry)
            if component.dxftype() == "INSERT"
            else 1
        )
        total += instances * nested_count
        if total > MAX_EXPANDED_MINSERT_INSTANCES:
            return MAX_EXPANDED_MINSERT_INSTANCES + 1
    return total


def _read_document(content: bytes | bytearray) -> Any:
    """Read ASCII and binary DXF uploads without writing them to disk."""
    if content.startswith(b"AutoCAD Binary DXF"):
        return Drawing.load(binary_tags_loader(content))
    return ezdxf.read(StringIO(decode_text_dxf(content)))


def _kind_for_layer(name: str) -> LayerKind:
    value = name.lower().replace("ё", "е")
    if any(word in value for word in ("site", "border", "boundary", "parcel", "границ", "участ")):
        return LayerKind.SITE_BORDER
    if any(word in value for word in ("build", "house", "structure", "здан", "сооруж")):
        return LayerKind.BUILDING
    if any(word in value for word in ("road", "street", "drive", "path", "trail", "foot", "walk", "alley", "lane", "sidewalk", "дорог", "проезд", "троп", "дорожк", "аллея")):
        return LayerKind.ROAD
    if any(word in value for word in ("hydro", "river", "lake", "pond", "stream", "waterbody", "водоем", "пруд", "река", "ручей")):
        return LayerKind.WATER
    if any(word in value for word in ("restricted", "obstacle", "technical_area", "equipment", "hardscape", "техзон", "технич", "препятств", "оборудован")):
        return LayerKind.RESTRICTED
    if any(word in value for word in ("util", "water", "heat", "gas", "sewer", "cable", "вод", "тепл", "газ", "канал", "кабел", "сет")):
        return LayerKind.UTILITY
    if any(word in value for word in ("green", "tree", "shrub", "exist", "park", "lawn", "flower", "landscape", "озелен", "дерев", "куст", "газон", "парк", "цветник")):
        return LayerKind.EXISTING_GREEN
    return LayerKind.IGNORE


def _point(value: Any, factor: float) -> list[float]:
    return [round(float(value[0]) * factor, 6), round(float(value[1]) * factor, 6)]


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


def _hex_color(rgb: Any) -> str:
    values = tuple(int(value) for value in rgb)
    return f"#{values[0]:02X}{values[1]:02X}{values[2]:02X}"


def _resolved_style(document: Any, entity: Any, layer_name: str, fallback: str) -> dict[str, Any]:
    layer = document.layers.get(layer_name)
    entity_rgb = getattr(entity, "rgb", None)
    if entity_rgb is not None:
        color = _hex_color(entity_rgb)
    else:
        aci = int(entity.dxf.get("color", 256) or 256)
        if aci in {0, 256}:
            layer_rgb = getattr(layer, "rgb", None)
            if layer_rgb is not None:
                color = _hex_color(layer_rgb)
            else:
                layer_aci = abs(int(layer.dxf.get("color", 7) or 7))
                color = _hex_color(ezdxf.colors.aci2rgb(layer_aci)) if 1 <= layer_aci <= 255 else fallback
        else:
            color = _hex_color(ezdxf.colors.aci2rgb(abs(aci))) if 1 <= abs(aci) <= 255 else fallback
    if color.upper() in {"#FFFFFF", "#000000"}:
        color = fallback
    linetype = str(entity.dxf.get("linetype", "BYLAYER") or "BYLAYER")
    if linetype.upper() in {"BYLAYER", "BYBLOCK"}:
        linetype = str(layer.dxf.get("linetype", "CONTINUOUS") or "CONTINUOUS")
    lineweight = int(entity.dxf.get("lineweight", -1) or -1)
    if lineweight < 0:
        lineweight = int(layer.dxf.get("lineweight", -1) or -1)
    return {"source_color": color, "source_linetype": linetype, "source_lineweight_mm": round(lineweight / 100, 2) if lineweight >= 0 else None}


def _flatten_insert_components(reference: Any, factor: float, inherited_layer: str, depth: int = 0) -> list[tuple[dict[str, Any], Any, str]]:
    """Flatten a virtual block tree while preserving each explicit layer.

    ``Insert.virtual_entities()`` correctly applies nested transformations,
    but returns a nested INSERT as an INSERT. Stopping at that first level
    collapses an explicit utility or boundary layer inside a common symbol
    into the outer symbol layer. Descend through the virtual tree so mapping
    and setbacks retain their original semantics.
    """
    if depth >= MAX_NESTED_BLOCK_DEPTH:
        return []
    components: list[tuple[dict[str, Any], Any, str]] = []
    try:
        count = _insert_instance_count(reference)
        if count > MAX_EXPANDED_MINSERT_INSTANCES:
            return []
        references = reference.multi_insert() if count > 1 else [reference]
        for instance in references:
            for virtual in instance.virtual_entities():
                declared_layer = str(virtual.dxf.get("layer", "0") or "0")
                effective_layer = inherited_layer if declared_layer == "0" else declared_layer
                if virtual.dxftype() == "INSERT":
                    components.extend(_flatten_insert_components(virtual, factor, effective_layer, depth + 1))
                    continue
                geometry = _entity_geometry(virtual, factor)
                if geometry is not None:
                    components.append((geometry, virtual, effective_layer))
    except Exception:
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


def _insert_instance_components(entity: Any, factor: float) -> list[list[tuple[dict[str, Any], Any, str]]]:
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
        for reference in references:
            instances.append(_flatten_insert_components(reference, factor, parent_layer))
        return instances
    except Exception:
        return []


def _insert_instance_geometries(entity: Any, factor: float) -> list[dict[str, Any]]:
    """Return one normalized geometry per visible INSERT/MINSERT instance.

    MINSERT is stored as a single DXF entity, but each grid position matters
    for both map visibility and normative geometry. Keeping instances separate
    lets the map query's feature budget work without omitting them from the
    exact backend calculation.
    """
    try:
        instances: list[dict[str, Any]] = []
        references = list(entity.multi_insert()) if _insert_instance_count(entity) > 1 else [entity]
        for reference, components in zip(references, _insert_instance_components(entity, factor), strict=False):
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
    if entity_type in {"DIMENSION", "LEADER", "MLEADER", "MLINE"}:
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
    if entity_type == "HATCH":
        # Boundary paths can be polylines, arcs, ellipses or splines. Flatten
        # each one independently: merging them first loses the distinction
        # between disconnected islands and holes.
        loops: list[list[list[float]]] = []
        try:
            for boundary_path in from_hatch(entity):
                loop = [_point(point, factor) for point in boundary_path.flattening(0.1, segments=16)]
                if len(loop) < 3:
                    continue
                if loop[-1] != loop[0]:
                    loop.append(loop[0])
                loops.append(loop)
        except Exception:
            loops = []
        polygon_geometry = _hatch_polygon_geometry(loops)
        if polygon_geometry is not None:
            return polygon_geometry
        try:
            hatch_path = make_path(entity)
            points = [_point(point, factor) for point in hatch_path.flattening(0.1, segments=16)]
        except Exception:
            return None
        return {"type": "LineString", "coordinates": points} if len(points) >= 2 else None
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
    if entity_type == "TEXT":
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


def _geometry_coordinate_count(geometry: dict[str, Any]) -> int:
    """Count GeoJSON positions without allocating a flattened coordinate list."""
    def count(value: Any) -> int:
        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and isinstance(value[0], Real) and isinstance(value[1], Real):
                return 1
            return sum(count(item) for item in value)
        if isinstance(value, dict):
            if "coordinates" in value:
                return count(value["coordinates"])
            if "geometries" in value:
                return sum(count(item) for item in value["geometries"])
        return 0

    return count(geometry)


def _has_polygon(geometry: dict[str, Any]) -> bool:
    geometry_type = geometry.get("type")
    if geometry_type in {"Polygon", "MultiPolygon"}:
        return True
    if geometry_type == "GeometryCollection":
        return any(_has_polygon(item) for item in geometry.get("geometries", []) if isinstance(item, dict))
    return False


def _coordinate_reference(document: Any) -> CoordinateReference:
    geodata = document.modelspace().get_geodata()
    if geodata is None:
        return CoordinateReference()
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


class EzdxfReader:
    """Reads supported DXF entities and normalizes coordinates to meters."""

    def read(self, filename: str, content: bytes | bytearray) -> DxfImportResult:
        if not filename.lower().endswith(".dxf"):
            raise ValueError("Поддерживаются только DXF-файлы")
        if not content:
            raise ValueError("Файл пуст")
        try:
            document = _read_document(content)
        except Exception as error:
            raise ValueError("DXF повреждён или имеет неподдерживаемую структуру") from error

        unit_code = int(document.header.get("$INSUNITS", 0) or 0)
        units_assumed = unit_code not in DXF_UNIT_FACTORS
        units, factor = DXF_UNIT_FACTORS.get(unit_code, ("м (принято)", 1.0))
        entities = list(document.modelspace())
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
            component_count = _estimated_insert_component_count(entity)
            if component_count > MAX_EXPANDED_MINSERT_INSTANCES:
                # ``multi_insert`` would materialize every member just to
                # audit its layers, which defeats the import safety limit.
                # The same applies when the array lives inside a nested
                # symbol: showing the first child would make unseen geometry
                # look safely available.
                oversized_minsert_instances[index] = component_count
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
            instances = _insert_instance_components(entity, factor)
            components = [virtual for instance in instances for _geometry, virtual, _layer in instance]
            if not components:
                if definition_components is None:
                    # An unresolved or corrupted block could contain any
                    # physical footprint on the INSERT's effective layer.
                    unrenderable_geometry_count_by_layer[parent_layer] += 1
                else:
                    for entity_type, component_layer in definition_components:
                        if entity_type in SUPPORTED_TYPES and entity_type not in CONTEXT_ONLY_ENTITY_TYPES:
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
        rendered_feature_count_by_layer: Counter[str] = Counter()
        truncated_feature_count_by_layer: Counter[str] = Counter()
        oversized_geometry_count_by_layer: Counter[str] = Counter()
        nonfinite_geometry_by_layer: Counter[str] = Counter()
        rendered_coordinate_count = 0

        def append_feature(feature: dict[str, Any]) -> None:
            """Keep a bounded, truthful map snapshot.

            We never simplify dropped objects into smaller obstacles: that
            could falsely turn occupied ground into an allowed planting zone.
            """
            nonlocal rendered_coordinate_count
            properties = feature.get("properties", {})
            source_layer = str(properties.get("source_layer", "0"))
            geometry = feature.get("geometry")
            if not isinstance(geometry, dict) or not _has_only_finite_coordinates(geometry):
                nonfinite_geometry_by_layer[source_layer] += 1
                return
            coordinate_count = _geometry_coordinate_count(geometry)
            if (
                coordinate_count > MAX_NORMALIZED_COORDINATES_PER_FEATURE
                or rendered_coordinate_count + coordinate_count > MAX_NORMALIZED_COORDINATES
            ):
                oversized_geometry_count_by_layer[source_layer] += 1
                return
            if len(features) >= MAX_NORMALIZED_DXF_FEATURES:
                truncated_feature_count_by_layer[source_layer] += 1
                return
            features.append(feature)
            rendered_feature_count_by_layer[source_layer] += 1
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
                        **_resolved_style(document, entity, source_layer, fallback_color),
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
                        **_resolved_style(document, entity, source_layer, fallback_color),
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
                            "kind": _kind_for_layer(source_layer).value,
                            "entity_type": virtual.dxftype(),
                            # A virtual component has no persistent DXF handle;
                            # retain its parent handle as the audit link.
                            "source_handle": str(entity.dxf.get("handle", "")),
                            **_resolved_style(document, virtual, source_layer, fallback_color),
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
                        if virtual.dxftype() in {"TEXT", "MTEXT"}:
                            height_attribute = "height" if virtual.dxftype() == "TEXT" else "char_height"
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
                properties: dict[str, Any] = {
                    "source_layer": source_layer,
                    "kind": _kind_for_layer(source_layer).value,
                    "entity_type": entity.dxftype(),
                    "source_handle": str(entity.dxf.get("handle", "")),
                    **_resolved_style(document, entity, source_layer, fallback_color),
                }
                if entity.dxftype() in simplified:
                    properties.update({
                        "geometry_fallback": True,
                        "geometry_fallback_reason": "Бесконечная конструктивная линия показана точкой привязки.",
                    })
                if entity.dxftype() in {"TEXT", "MTEXT"}:
                    height_attribute = "height" if entity.dxftype() == "TEXT" else "char_height"
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
                if entity.dxftype() == "INSERT":
                    properties.update({
                        "source_block": str(entity.dxf.name),
                        "source_rotation": round(float(entity.dxf.get("rotation", 0) or 0), 3),
                        "source_scale": [round(float(entity.dxf.get("xscale", 1) or 1), 4), round(float(entity.dxf.get("yscale", 1) or 1), 4)],
                        "source_attributes": {str(attribute.dxf.tag): str(attribute.dxf.text) for attribute in entity.attribs},
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

        layers: list[Layer] = []
        for index, (name, count) in enumerate(counts.items()):
            kind = _kind_for_layer(name)
            if kind == LayerKind.SITE_BORDER and name not in polygon_layers:
                kind = LayerKind.IGNORE
            layer = document.layers.get(name)
            layer_color = getattr(layer, "rgb", None)
            if layer_color is None:
                layer_aci = abs(int(layer.dxf.get("color", 7) or 7))
                color = _hex_color(ezdxf.colors.aci2rgb(layer_aci)) if 1 <= layer_aci <= 255 else LAYER_COLORS[index % len(LAYER_COLORS)]
            else:
                color = _hex_color(layer_color)
            if color.upper() in {"#FFFFFF", "#000000"}:
                color = LAYER_COLORS[index % len(LAYER_COLORS)]
            lineweight = int(layer.dxf.get("lineweight", -1) or -1)
            layers.append(Layer(
                id=str(uuid5(NAMESPACE_URL, f"dxf-layer:{name}")),
                source_name=name,
                suggested_kind=kind,
                mapped_kind=kind,
                object_count=count,
                color=color,
                linetype=str(layer.dxf.get("linetype", "CONTINUOUS") or "CONTINUOUS"),
                lineweight_mm=round(lineweight / 100, 2) if lineweight >= 0 else None,
                entity_types=dict(entity_types_by_layer[name]),
                geometry_complete=not (
                    truncated_feature_count_by_layer[name]
                    or oversized_geometry_count_by_layer[name]
                    or unrenderable_geometry_count_by_layer[name]
                    or unsupported_by_layer.get(name)
                ),
                required=kind == LayerKind.SITE_BORDER,
            ))

        warnings: list[str] = []
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
        if truncated_feature_count_by_layer:
            labels = ", ".join(
                f"{name}: показано {rendered_feature_count_by_layer[name]}, скрыто {count}"
                for name, count in sorted(truncated_feature_count_by_layer.items())
            )
            warnings.append(
                "Карта ограничена 25 000 объектами: "
                f"{labels}. Неполный слой нельзя использовать для расчёта ограничений; "
                "загрузите рабочий фрагмент или сопоставьте его как неиспользуемый."
            )
        if oversized_geometry_count_by_layer:
            labels = ", ".join(f"{name}: {count}" for name, count in sorted(oversized_geometry_count_by_layer.items()))
            warnings.append(
                "Слишком детальная геометрия не показана на карте: "
                f"{labels}. Неполный слой нельзя использовать для расчёта ограничений; "
                "загрузите рабочий фрагмент DXF."
            )
        if unrenderable_geometry_count_by_layer:
            labels = ", ".join(f"{name}: {count}" for name, count in sorted(unrenderable_geometry_count_by_layer.items()))
            warnings.append(
                "Часть геометрии не удалось прочитать для карты: "
                f"{labels}. Неполный слой нельзя использовать для расчёта ограничений; "
                "исправьте DXF или загрузите рабочий фрагмент."
            )
        if unsupported:
            labels = ", ".join(f"{name}: {count}" for name, count in sorted(unsupported.items()))
            warnings.append(
                f"Часть типов доступна только в исходном файле: {labels}. "
                "Если такой слой назначен физическим ограничением, расчёт будет остановлен; "
                "проверьте назначение слоя или загрузите рабочий фрагмент."
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
            warnings.append("Не найдена замкнутая граница участка; назначьте корректный слой с полигоном.")

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

        return DxfImportResult(
            layers=layers,
            geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": features}),
            dxf_version=document.dxfversion,
            units=units,
            units_assumed=units_assumed,
            entity_count=len(entities) + paper_entity_count,
            bounds=_geometry_bounds(extent_features),
            warnings=warnings,
            coordinate_reference=_coordinate_reference(document),
        )
