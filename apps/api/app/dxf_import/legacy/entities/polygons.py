"""Геометрия заполненных областей прежнего DXF через SDK и OCS с явным учётом кривых и
вертикальных примитивов.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from math import atan, atan2, cos, isfinite, pi, sin
from typing import Any, Literal

from ezdxf.math import OCS, Vec3
from ezdxf.path import Path, from_hatch, from_hatch_boundary_path
from shapely import STRtree
from shapely.errors import GEOSException
from shapely.geometry import LineString as ShapelyLineString
from shapely.geometry import Polygon, shape
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import shape as shapely_shape
from shapely.ops import polygonize, unary_union

from app.dxf_import.layers.contracts import BoundaryCandidate, BoundaryCandidateStatus
from app.geometry.contracts import DxfVerticalPrimitive

FLATTENING_DISTANCE = 0.1
MIN_CURVE_SEGMENTS = 16
COORDINATE_DECIMALS = 6
PolygonRings = list[list[list[float]]]
PolygonGeometry = dict[str, Any]


def mpolygon_geometry(
    entity: Any,
    factor: float,
) -> PolygonGeometry | None:
    """Отклонять неполные или неверные области без восстановления вымышленного габарита.

    MPOLYGON использует чётность вложенных колец независимо от флагов стиля. SDK
    учитывает extrusion/elevation, а отдельный offset передаётся явно.
    """
    try:
        offset = Vec3(entity.dxf.get("offset_vector", (0, 0, 0)))
        return _polygon_geometry(from_hatch(entity, offset=offset), factor)
    except Exception:
        return None


def hatch_geometry(
    entity: Any,
    factor: float,
) -> PolygonGeometry | None:
    """Сохранять стиль заполнения и раздельные внешние границы HATCH.

    SDK выбирает активные пути по hatch_style. Сборка нескольких полигонов не
    объявляет первый путь единственным внешним и не принимает частичный габарит.
    """

    def paths() -> Iterator[Path]:
        ocs = entity.ocs()
        elevation = entity.dxf.elevation.z
        for boundary in entity.paths.rendering_paths(entity.dxf.hatch_style):
            path = from_hatch_boundary_path(boundary, ocs, elevation=elevation)
            if path.has_sub_paths:
                yield from path.sub_paths()
            else:
                yield path

    return _polygon_geometry(paths(), factor)


def _polygon_geometry(
    paths: Iterable[Path],
    factor: float,
) -> PolygonGeometry | None:
    loops: PolygonRings = []
    try:
        for path in paths:
            loop = [
                [
                    round(point.x * factor, COORDINATE_DECIMALS),
                    round(point.y * factor, COORDINATE_DECIMALS),
                ]
                for point in path.flattening(
                    FLATTENING_DISTANCE, segments=MIN_CURVE_SEGMENTS
                )
            ]
            if len(loop) < 3 or not all(
                isfinite(coordinate) for point in loop for coordinate in point
            ):
                return None
            if loop[-1] != loop[0]:
                loop.append(loop[0])
            polygon = Polygon(loop)
            if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
                return None
            loops.append(loop)
        geometry = _hatch_polygon_geometry(loops)
        if geometry is None or not shape(geometry).is_valid:
            return None
        return geometry
    except Exception:
        # Reader отмечает непрочитанную геометрию и неполноту действующего слоя.
        # Исходная сущность не удаляется и не изменяется.
        return None


_HEIGHT_ATTRIBUTE_TAGS = {"BUILDING_HEIGHT_M", "HEIGHT_M", "ВЫСОТА_М"}


def _point(value: Any, factor: float) -> list[float]:
    return [round(float(value[0]) * factor, 6), round(float(value[1]) * factor, 6)]


def _point3(value: Any, factor: float) -> list[float]:
    """Переводить WCS-координату DXF в метры с сохранением Z."""
    point = Vec3(value)
    return [
        round(float(point.x) * factor, 6),
        round(float(point.y) * factor, 6),
        round(float(point.z) * factor, 6),
    ]


def _vertical_primitive(
    entity: Any,
    factor: float,
    source_units: str,
    primitive_id: str,
    terrain_evidence: dict[str, Any] | None = None,
) -> DxfVerticalPrimitive | None:
    """Извлекать явные WCS XYZ-свидетельства независимо от 2D-представления.

    MESH или 3DFACE подтверждает CAD-поверхность, но не рельеф без назначения
    источника или оператора. Поэтому примитив начинает с роли unmapped.
    """
    entity_type = entity.dxftype()
    vertices: list[list[float]] = []
    faces: list[list[int]] = []
    primitive_type: Literal["point", "polyline", "surface_mesh"] = "polyline"
    evidence: Literal["explicit_xyz", "explicit_elevation", "explicit_extrusion"] = (
        "explicit_xyz"
    )
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
                indices = [
                    vertex_index[id(vertex)]
                    for vertex in face
                    if id(vertex) in vertex_index
                ]
                if len(indices) >= 3:
                    faces.append(indices)
            primitive_type = "surface_mesh"
        elif entity_type == "POLYLINE" and entity.is_polygon_mesh:
            m_count, n_count = int(entity.dxf.m_count), int(entity.dxf.n_count)
            for m_index in range(m_count):
                for n_index in range(n_count):
                    vertices.append(
                        _point3(
                            entity.get_mesh_vertex((m_index, n_index)).dxf.location,
                            factor,
                        )
                    )
            m_range = range(m_count if entity.is_m_closed else max(0, m_count - 1))
            n_range = range(n_count if entity.is_n_closed else max(0, n_count - 1))
            for m_index in m_range:
                for n_index in n_range:
                    faces.append(
                        [
                            m_index * n_count + n_index,
                            ((m_index + 1) % m_count) * n_count + n_index,
                            ((m_index + 1) % m_count) * n_count
                            + ((n_index + 1) % n_count),
                            m_index * n_count + ((n_index + 1) % n_count),
                        ]
                    )
            primitive_type = "surface_mesh"
        elif entity_type == "POLYLINE" and entity.is_3d_polyline:
            vertices = [
                _point3(vertex.dxf.location, factor) for vertex in entity.vertices
            ]
            if entity.is_closed and vertices and vertices[-1] != vertices[0]:
                vertices.append(vertices[0])
        elif entity_type == "LINE":
            vertices = [
                _point3(entity.dxf.start, factor),
                _point3(entity.dxf.end, factor),
            ]
        elif entity_type == "POINT":
            vertices = [_point3(entity.dxf.location, factor)]
            primitive_type = "point"
        else:
            # Плоские сущности могут содержать высоту и толщину по нормали OCS. Сохраняем
            # эти сведения по нормализованному WCS-якорю; физический габарит задаёт 2D-геометрия.
            vertical = _source_vertical_properties(entity, factor)
            if (
                "source_base_elevation_m" not in vertical
                and "source_extrusion_height_m" not in vertical
            ):
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
            evidence = (
                "explicit_extrusion"
                if "source_extrusion_height_m" in vertical
                else "explicit_elevation"
            )
        if not vertices or any(
            not all(isfinite(value) for value in point) for point in vertices
        ):
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
            terrain_mapping_basis="dxf_document_metadata"
            if is_declared_terrain and terrain_evidence
            else None,
            terrain_confidence=terrain_evidence["confidence"]
            if is_declared_terrain and terrain_evidence
            else None,
            source_dataset=str(terrain_evidence["dataset"])
            if is_declared_terrain and terrain_evidence
            else None,
            source_url=str(terrain_evidence["url"])
            if is_declared_terrain and terrain_evidence
            else None,
            source_attribution=str(terrain_evidence["attribution"])
            if is_declared_terrain and terrain_evidence
            else None,
            vertical_datum=str(terrain_evidence["datum"])
            if is_declared_terrain and terrain_evidence
            else None,
            vertical_datum_offset_m=float(terrain_evidence["datum_offset"])
            if is_declared_terrain and terrain_evidence
            else None,
        )
    except Exception:
        return None


def _ocs_point(entity: Any, value: Any, factor: float) -> list[float]:
    """Нормализовать плоскую DXF-точку из её OCS.

    Нормаль OCS может отличаться от +Z; чтение сырых x/y тогда зеркалит или вращает
    план. Запасной путь используется только без OCS helper, исходник не изменяется.
    """

    try:
        return _point(entity.ocs().to_wcs(value), factor)
    except Exception:
        return _point(value, factor)


def _source_vertical_properties(
    entity: Any,
    factor: float,
    attributes: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Сохранять явные вертикальные сведения DXF без вывода объёма.

    Имя, цвет и 2D-габарит не доказывают высоту здания. thickness принимается при
    вертикальной extrusion, атрибут блока — при явно объявленных метрах. Остальные
    поверхности сцены остаются плоскими.
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
        if entity_type in {"LWPOLYLINE", "HATCH", "MPOLYGON"} and entity.dxf.hasattr(
            "elevation"
        ):
            elevation = entity.dxf.elevation
        elif entity_type == "POLYLINE" and entity.dxf.hasattr("elevation"):
            elevation = entity.dxf.elevation
        elif entity_type in {"CIRCLE", "ARC"} and entity.dxf.hasattr("center"):
            elevation = entity.dxf.center
        elif entity_type in {"INSERT", "POINT", "TEXT", "MTEXT"} and entity.dxf.hasattr(
            "insert"
        ):
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
    """Аппроксимировать дуги 2D-полилинии для отображения без изменения DXF."""

    def point(x: float, y: float) -> list[float]:
        return (
            transform(x, y)
            if transform
            else [round(x * factor, 6), round(y * factor, 6)]
        )

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
            points.append(
                point(center_x + radius * cos(angle), center_y + radius * sin(angle))
            )
    return points


def _lwpolyline_points(entity: Any, factor: float) -> list[list[float]]:
    elevation = float(entity.dxf.get("elevation", 0) or 0)
    return _flatten_bulged_points(
        [
            (float(point[0]), float(point[1]), float(point[2] or 0))
            for point in entity.get_points("xyb")
        ],
        entity.closed,
        factor,
        transform=lambda x, y: _ocs_point(entity, Vec3(x, y, elevation), factor),
    )


def _polyline_points(entity: Any, factor: float) -> list[list[float]]:
    """В отличие от LWPOLYLINE, POLYLINE хранит bulge в сущностях VERTEX."""
    elevation = entity.dxf.get("elevation", (0, 0, 0))
    elevation_z = float(
        elevation[2] if isinstance(elevation, (tuple, list, Vec3)) else 0
    )
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
    """Выбирать наибольшую объявленную ширину сегмента в исходных единицах.

    CAD-покрытие может задаваться осью с переменной шириной; максимальный сегмент
    консервативен и не создаёт ложную свободную площадь на сужающейся линии.
    """
    try:
        widths: list[float] = []
        if entity.dxftype() == "LWPOLYLINE":
            widths.append(float(entity.dxf.get("const_width", 0) or 0))
            for point in entity.get_points("xyseb"):
                widths.extend((float(point[2] or 0), float(point[3] or 0)))
        else:
            widths.extend(
                (
                    float(entity.dxf.get("default_start_width", 0) or 0),
                    float(entity.dxf.get("default_end_width", 0) or 0),
                )
            )
            for vertex in entity.vertices:
                widths.extend(
                    (
                        float(vertex.dxf.get("start_width", 0) or 0),
                        float(vertex.dxf.get("end_width", 0) or 0),
                    )
                )
        return max(0.0, *widths)
    except Exception:
        return 0.0


def _wide_polyline_geometry(
    coordinates: list[list[float]], width_m: float
) -> dict[str, Any] | None:
    """Представлять CAD-линию конечной ширины её видимым наземным габаритом."""
    if width_m <= 1e-9 or len(coordinates) < 2:
        return None
    try:
        # Плоские торцы сохраняют заданные концы CAD-сегмента. Круглые соединения
        # обеспечивают непрерывность кривых без вымышленного разрыва на вершинах.
        polygon = ShapelyLineString(coordinates).buffer(
            width_m / 2, cap_style="flat", join_style="round"
        )
    except Exception:
        return None
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return None
    rings = [polygon.exterior, *polygon.interiors]
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [round(float(point[0]), 6), round(float(point[1]), 6)]
                for point in ring.coords
            ]
            for ring in rings
        ],
    }


def _polyline_center_geometry(entity: Any, factor: float) -> dict[str, Any] | None:
    """Сохранять авторскую ось независимо от отображаемой ширины линии.

    Для замкнутой широкой границы расчётным кольцом является центральный путь, а
    карта показывает штрих. Отдельно проверенный путь используется при явном
    назначении site_border.
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
                basis="authored_centerline"
                if used_center_geometry
                else "source_geometry",
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
    """Показывать только достоверную внутреннюю рамку подрезки внешней подложки.

    PDF/DWF/DGN может быть недоступен. Внешняя подрезка и неподрезанная ссылка не
    задают конечную область и не объявляются территорией. Вершины DXF заданы в
    немасштабированной OCS подложки.
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
            point = ocs.to_wcs(
                Vec3(
                    float(insert.x) + local_x * cosine - local_y * sine,
                    float(insert.y) + local_x * sine + local_y * cosine,
                    float(insert.z),
                )
            )
            coordinates.append(_point(point, factor))
        if coordinates[-1] != coordinates[0]:
            coordinates.append(coordinates[0])
        return {"type": "Polygon", "coordinates": [coordinates]}
    except Exception:
        return None


def _hatch_polygon_geometry(loops: list[list[list[float]]]) -> dict[str, Any] | None:
    """Собирать кольца HATCH в Polygon/MultiPolygon без превращения островков в
    отверстия.

    Отдельные несвязанные пути не являются кольцами одного полигона. Вложенность
    задаёт чётность заполнения независимо от обхода, включая раздельные области и
    островки.
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

    # HATCH может содержать тысячи отдельных островков. Индекс проверяет только
    # полигоны, содержащие контрольную точку, вместо квадратичного обхода всех колец.
    # Правило наименьшего контейнера сохраняет настоящие отверстия и островки.
    polygons = [polygon for _, polygon in records]
    index = STRtree(polygons)
    parents: list[int | None] = []
    for record_index, polygon in enumerate(polygons):
        containers = [
            int(candidate_index)
            for candidate_index in index.query(
                polygon.representative_point(), predicate="within"
            )
            if int(candidate_index) != record_index
            and polygons[int(candidate_index)].area > polygon.area
        ]
        parents.append(
            min(containers, key=lambda candidate_index: polygons[candidate_index].area)
            if containers
            else None
        )

    def depth(index: int) -> int:
        result = 0
        parent = parents[index]
        while parent is not None:
            result += 1
            parent = parents[parent]
        return result

    polygon_rings = [
        [
            loop,
            *(
                child_loop
                for child_index, (child_loop, _) in enumerate(records)
                if parents[child_index] == index
            ),
        ]
        for index, (loop, _) in enumerate(records)
        if depth(index) % 2 == 0
    ]
    if len(polygon_rings) == 1:
        return {"type": "Polygon", "coordinates": polygon_rings[0]}
    return {"type": "MultiPolygon", "coordinates": polygon_rings}


def _geometry_bounds(features: list[dict[str, Any]]) -> list[float] | None:
    coordinates: list[list[float]] = []

    def collect(value: Any) -> None:
        if isinstance(value, dict):
            if "coordinates" in value:
                collect(value["coordinates"])
            if "geometries" in value:
                collect(value["geometries"])
        elif (
            isinstance(value, list)
            and len(value) >= 2
            and all(isinstance(item, (int, float)) for item in value[:2])
        ):
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
    """Отклонять GeoJSON, который нельзя безопасно показать или индексировать.

    NaN и Infinity из DXF не становятся координатами карты. Байты сохраняются для
    аудита и выпуска; повреждённая сущность не включается в нормализованный снимок.
    """

    def walk(value: Any) -> bool:
        if isinstance(value, dict):
            if "coordinates" in value:
                return walk(value["coordinates"])
            if "geometries" in value:
                return all(walk(item) for item in value["geometries"])
            return False
        if isinstance(value, (list, tuple)):
            if value and all(
                isinstance(item, (int, float)) and not isinstance(item, bool)
                for item in value
            ):
                return len(value) >= 2 and all(isfinite(float(item)) for item in value)
            return bool(value) and all(walk(item) for item in value)
        return False

    return walk(geometry)


def _has_polygon(geometry: dict[str, Any]) -> bool:
    geometry_type = geometry.get("type")
    if geometry_type in {"Polygon", "MultiPolygon"}:
        return True
    if geometry_type == "GeometryCollection":
        return any(
            _has_polygon(item)
            for item in geometry.get("geometries", [])
            if isinstance(item, dict)
        )
    return False


def polyline_geometry(entity: Any, factor: float) -> dict[str, Any] | None:
    """Единая политика габарита для 2D LWPOLYLINE и POLYLINE."""
    lightweight = entity.dxftype() == "LWPOLYLINE"
    coordinates = (
        _lwpolyline_points(entity, factor)
        if lightweight
        else _polyline_points(entity, factor)
    )
    wide_geometry = _wide_polyline_geometry(
        coordinates, _polyline_width(entity) * factor
    )
    if wide_geometry is not None:
        return wide_geometry
    closed = entity.closed if lightweight else entity.is_closed
    if closed and len(coordinates) >= 3:
        if coordinates[-1] != coordinates[0]:
            coordinates.append(coordinates[0])
        return {"type": "Polygon", "coordinates": [coordinates]}
    return (
        {"type": "LineString", "coordinates": coordinates}
        if len(coordinates) >= 2
        else None
    )
