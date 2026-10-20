"""Идентичность виртуальных DXF-компонентов и свидетельства источника; смысл объектов не
классифицируется.
"""

from math import isfinite
from typing import Any

from app.dxf_import.legacy.entities.polygons import (
    _point,
    _polyline_center_geometry,
    _polyline_width,
    _source_vertical_properties,
)
from app.geometry.contracts import CoordinateReference


def component_definition_provenance(entity: Any) -> dict[str, str]:
    original = getattr(entity, "origin_of_copy", None)
    if original is None or original.doc is None:
        return {}
    handle = original.dxf.get("handle")
    owner = original.doc.entitydb.get(original.dxf.get("owner", ""))
    if not handle or owner is None or owner.dxftype() != "BLOCK_RECORD":
        return {}
    return {
        "source_component_handle": str(handle),
        "source_component_block": str(owner.dxf.name),
    }


def _is_mesh_context(entity: Any) -> bool:
    """Определять CAD-поверхности без доказанного 2D-препятствия посадки.

    POLYLINE может задавать линию или сетку. Грани сетки остаются контекстом, как
    MESH: их уплощение и назначение слоя не подтверждает физический отступ.
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
    """Читать объявленный счётчик граней без разворачивания сетки."""
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


def _terrain_document_evidence(document: Any) -> dict[str, Any] | None:
    """Читать явную декларацию рельефа из документа тестового примера.

    Одно имя слоя не является доказательством. Полная декларация не даёт постороннему
    DXF объявить произвольные грани подтверждённым рельефом.
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
    if not all(
        values[key]
        for key in (
            "layer",
            "dataset",
            "url",
            "attribution",
            "confidence",
            "datum",
            "datum_offset",
        )
    ):
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
            definition_present = bool(
                str(getattr(geodata, "coordinate_system_definition", "")).strip()
            )
            return CoordinateReference(
                status="local",
                source="dxf_geodata",
                evidence="В DXF есть GEODATA, но CRS не распознан по EPSG."
                if definition_present
                else "В DXF есть локальная привязка без машиночитаемого определения CRS.",
            )

    # Примеры Green Atlas объявляют локальное касательное приближение, а не
    # проекционную CRS. Принимаем декларацию целиком: частичный origin создавал бы
    # ложную уверенность при последующем обогащении данных.
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
        return CoordinateReference(
            evidence="Локальная WGS84-привязка DXF неполна и не используется."
        )
    try:
        origin_parts = [
            float(part.strip()) for part in str(values["origin"]).split(",")
        ]
        earth_radius = float(values["earth_radius"])
    except (TypeError, ValueError):
        return CoordinateReference(
            evidence="Локальная WGS84-привязка DXF повреждена и не используется."
        )
    if (
        len(origin_parts) != 2
        or not all(isfinite(value) for value in origin_parts)
        or not -90 <= origin_parts[0] <= 90
        or not -180 <= origin_parts[1] <= 180
        or str(values["projection"]) != "local_equirectangular_wgs84"
        or not isfinite(earth_radius)
        or not 6_000_000 <= earth_radius <= 7_000_000
    ):
        return CoordinateReference(
            evidence="Локальная WGS84-привязка DXF повреждена и не используется."
        )
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


CONTEXT_ONLY_ENTITY_TYPES = {
    "TEXT",
    "MTEXT",
    "ATTDEF",
    "DIMENSION",
    "LEADER",
    "MLEADER",
    "MULTILEADER",
    "MLINE",
    "HELIX",
    "MESH",
    "RAY",
    "XLINE",
    "WIPEOUT",
    "IMAGE",
    "PDFUNDERLAY",
    "PDFREFERENCE",
    "DWFUNDERLAY",
    "DGNUNDERLAY",
    "ACAD_PROXY_ENTITY",
}

MAX_RENDERED_MESH_FACES = 20_000


def common_entity_properties(
    entity: Any, factor: float, attributes: dict[str, str] | None = None
) -> dict[str, Any]:
    """Общие свидетельства источника для исходных сущностей и виртуальных компонентов."""
    properties = _source_vertical_properties(entity, factor, attributes)
    if entity.dxftype() in {"RAY", "XLINE"}:
        properties.update(
            {
                "geometry_fallback": True,
                "geometry_fallback_reason": "Бесконечная конструктивная линия показана точкой привязки.",
            }
        )
    if entity.dxftype() in {"TEXT", "MTEXT", "ATTDEF"}:
        height_attribute = "char_height" if entity.dxftype() == "MTEXT" else "height"
        properties.update(
            {
                "source_text": str(entity.plain_text()),
                "source_text_height": round(
                    float(entity.dxf.get(height_attribute, 2.5) or 2.5) * factor, 3
                ),
                "source_rotation": round(float(entity.dxf.get("rotation", 0) or 0), 3),
            }
        )
    if entity.dxftype() in {"LWPOLYLINE", "POLYLINE"}:
        source_width_m = round(_polyline_width(entity) * factor, 6)
        if source_width_m > 0:
            properties.update(
                {
                    "source_polyline_width_m": source_width_m,
                    "source_width_mode": "conservative_max",
                }
            )
            center_geometry = _polyline_center_geometry(entity, factor)
            if center_geometry is not None:
                properties["source_polyline_center_geometry"] = center_geometry
    if entity.dxftype() in CONTEXT_ONLY_ENTITY_TYPES or _is_mesh_context(entity):
        properties["source_context_only"] = True
    if _is_mesh_context(entity):
        properties.update(
            {
                "source_mesh_context": True,
                "source_mesh_faces": _mesh_face_count(entity),
                "source_mesh_simplified": _mesh_face_count(entity)
                > MAX_RENDERED_MESH_FACES,
            }
        )
    return properties


def mesh_geometry(entity: Any, factor: float) -> dict[str, Any] | None:
    """Показывать грани CAD-поверхности с явной ролью только исходного контекста."""
    entity_type = entity.dxftype()
    if entity_type == "MESH":
        # У сетки нет единой плоской границы. Сохраняем каждую грань отдельным полигоном
        # для MultiPolygon-карты, явно оставляя геометрию контекстом исходника.
        try:
            data = entity.get_data()
            polygons: list[list[list[list[float]]]] = []
            for face in data.faces:
                points = [
                    _point(data.vertices[index], factor)
                    for index in face
                    if 0 <= index < len(data.vertices)
                ]
                if len(points) < 3:
                    continue
                if points[-1] != points[0]:
                    points.append(points[0])
                polygons.append([points])
            return (
                {"type": "MultiPolygon", "coordinates": polygons} if polygons else None
            )
        except Exception:
            return None
    if entity.is_poly_face_mesh:
        mesh_polygons: list[list[list[list[float]]]] = []
        try:
            for face in entity.faces():
                points = [
                    _point(vertex.dxf.location, factor)
                    for vertex in face
                    if int(vertex.dxf.get("flags", 0) or 0) != 128
                ]
                if len(points) < 3:
                    continue
                if points[-1] != points[0]:
                    points.append(points[0])
                mesh_polygons.append([points])
        except Exception:
            return None
        return (
            {"type": "MultiPolygon", "coordinates": mesh_polygons}
            if mesh_polygons
            else None
        )
    if entity.is_polygon_mesh:
        mesh_polygons = []
        try:
            m_count = int(entity.dxf.m_count)
            n_count = int(entity.dxf.n_count)
            m_range = range(m_count if entity.is_m_closed else max(0, m_count - 1))
            n_range = range(n_count if entity.is_n_closed else max(0, n_count - 1))
            for m_index in m_range:
                for n_index in n_range:
                    corners = [
                        entity.get_mesh_vertex((m_index, n_index)).dxf.location,
                        entity.get_mesh_vertex(
                            ((m_index + 1) % m_count, n_index)
                        ).dxf.location,
                        entity.get_mesh_vertex(
                            ((m_index + 1) % m_count, (n_index + 1) % n_count)
                        ).dxf.location,
                        entity.get_mesh_vertex(
                            (m_index, (n_index + 1) % n_count)
                        ).dxf.location,
                    ]
                    points = [_point(corner, factor) for corner in corners]
                    points.append(points[0])
                    mesh_polygons.append([points])
        except Exception:
            return None
        return (
            {"type": "MultiPolygon", "coordinates": mesh_polygons}
            if mesh_polygons
            else None
        )
    return None


def raster_frame_geometry(entity: Any, factor: float) -> dict[str, Any] | None:
    """Проецировать авторскую аффинную рамку без загрузки внешних пикселей."""
    # Растровые пиксели хранятся вне DXF, но их аффинная рамка входит в чертёж.
    # Показываем её, чтобы подложка не исчезала и не искажала размер рабочей области.
    try:
        insert = entity.dxf.insert
        u_pixel = entity.dxf.u_pixel
        v_pixel = entity.dxf.v_pixel
        boundary = list(getattr(entity, "boundary_path", []) or [])
        if not boundary:
            size = entity.dxf.image_size
            boundary = [
                (0, 0),
                (float(size.x), 0),
                (float(size.x), float(size.y)),
                (0, float(size.y)),
            ]
        elif len(boundary) == 2:
            # Неподрезанная рамка IMAGE хранит противоположные углы. Разворачиваем их
            # до перевода аффинной пиксельной рамки в координаты карты.
            minimum, maximum = boundary
            boundary = [
                minimum,
                (float(maximum[0]), float(minimum[1])),
                maximum,
                (float(minimum[0]), float(maximum[1])),
            ]
        points = [
            [
                round(
                    (
                        float(insert.x)
                        + float(u_pixel.x) * float(pixel[0])
                        + float(v_pixel.x) * float(pixel[1])
                    )
                    * factor,
                    6,
                ),
                round(
                    (
                        float(insert.y)
                        + float(u_pixel.y) * float(pixel[0])
                        + float(v_pixel.y) * float(pixel[1])
                    )
                    * factor,
                    6,
                ),
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
