"""Чтение прежнего DXF для совместимости: слои, геометрия и адресные пропуски; новые
native-проекты готовит AutoCAD.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from io import StringIO
from math import cos, pi, sin
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

import ezdxf
from ezdxf.document import Drawing
from ezdxf.lldxf.tagger import binary_tags_loader
from ezdxf.math import Vec3

from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.contracts import DxfImportResult
from app.dxf_import.layers.contracts import (
    BoundaryCandidateStatus,
    Layer,
    LayerKind,
    LayerSuggestionConfidence,
)
from app.dxf_import.layers.suggestions import (
    assess_layer_suggestion,
    is_boundary_candidate_name,
    suggest_layer_kind,
)
from app.dxf_import.legacy.encoding import decode_text_dxf
from app.dxf_import.legacy.entities.acis_lookup import indexed_acis_lookup
from app.dxf_import.legacy.entities.axis_provenance import straight_axis_provenance
from app.dxf_import.legacy.entities.block_diagnostics import (
    BlockInspection,
    LayerInventory,
    _insert_instance_count,
    _insert_instance_geometries,
    inspect_document_blocks,
)
from app.dxf_import.legacy.entities.component_provenance import (
    CONTEXT_ONLY_ENTITY_TYPES,
    MAX_RENDERED_MESH_FACES,
    _coordinate_reference,
    _is_mesh_context,
    _mesh_face_count,
    _terrain_document_evidence,
    common_entity_properties,
    component_definition_provenance,
    mesh_geometry,
    raster_frame_geometry,
)
from app.dxf_import.legacy.entities.mleader_compat import prepare_multileader_transforms
from app.dxf_import.legacy.entities.polygons import (
    _boundary_candidate,
    _geometry_bounds,
    _has_only_finite_coordinates,
    _has_polygon,
    _ocs_point,
    _point,
    _underlay_clip_geometry,
    _vertical_primitive,
    hatch_geometry,
    mpolygon_geometry,
    polyline_geometry,
)
from app.dxf_import.legacy.preview_marker import read_preview_marker
from app.dxf_import.legacy.styles import SourceStyleResolver
from app.dxf_import.units import DXF_UNIT_FACTORS
from app.geometry.contracts import (
    GeometrySnapshot,
)
from app.geometry.geojson_size import coordinate_count as _geometry_coordinate_count

LAYER_COLORS = [
    "#0D7C6B",
    "#64748B",
    "#94A3B8",
    "#2563EB",
    "#F97316",
    "#22C55E",
    "#7C3AED",
]
MAX_PROXY_VIRTUAL_GEOMETRIES = 2_000
CIRCLE_SEGMENTS = 32
ARC_SEGMENTS = 24
CURVE_FLATTENING_DISTANCE = 0.1
CURVE_SEGMENTS = 16
SPLINE_SEGMENTS = 8
CURVE_CLOSURE_TOLERANCE = 1e-5


def _mesh_anchor(entity: Any, factor: float) -> dict[str, Any] | None:
    """Сохранять видимый якорь поверхности, если его можно получить достоверно.

    Отсутствующий якорь не заменяется точкой (0, 0), которая создала бы ложный обзор
    или привязку карты.
    """
    try:
        if entity.dxftype() == "POLYLINE" and entity.is_polygon_mesh:
            return {
                "type": "Point",
                "coordinates": _point(
                    entity.get_mesh_vertex((0, 0)).dxf.location, factor
                ),
            }
        if entity.dxftype() == "POLYLINE" and entity.vertices:
            return {
                "type": "Point",
                "coordinates": _point(entity.vertices[0].dxf.location, factor),
            }
        if entity.dxftype() == "MESH":
            vertices = entity.get_data().vertices
            if vertices:
                return {"type": "Point", "coordinates": _point(vertices[0], factor)}
    except Exception:
        pass
    return None


def _read_document(content: bytes | bytearray) -> Any:
    """Читать текстовый и бинарный DXF без промежуточной записи на диск."""
    if content.startswith(b"AutoCAD Binary DXF"):
        return Drawing.load(binary_tags_loader(content))
    # Как при файловом чтении DXF, нормализуем переводы строк: ezdxf ожидает
    # универсальные строки, а обычный StringIO сохраняет CR из Windows/CAD.
    return ezdxf.read(StringIO(decode_text_dxf(content), newline=None))


def _entity_geometry(entity: Any, factor: float) -> dict[str, Any] | None:
    entity_type = entity.dxftype()
    if entity_type == "ACAD_PROXY_ENTITY":
        # У proxy-сущности нет надёжной предметной семантики; её графика даёт лишь
        # визуальный контекст. Плотная или повреждённая графика не публикуется частично:
        # исходный DXF сохраняется, а карта не показывает вымышленный результат.
        try:
            geometries: list[dict[str, Any]] = []
            for virtual in entity.virtual_entities():
                # Вложенный proxy не имеет надёжно доступной геометрии и при повреждении
                # мог бы вызвать бесконечный рекурсивный обход.
                if virtual.dxftype() == "ACAD_PROXY_ENTITY":
                    continue
                if len(geometries) >= MAX_PROXY_VIRTUAL_GEOMETRIES:
                    return None
                geometry = _entity_geometry(virtual, factor)
                if geometry is not None:
                    geometries.append(geometry)
        except Exception:
            return None
        return (
            {"type": "GeometryCollection", "geometries": geometries}
            if geometries
            else None
        )
    if entity_type in {"DIMENSION", "LEADER", "MLEADER", "MULTILEADER", "MLINE"}:
        # Виртуальные LINE/ARC/TEXT/INSERT размерных аннотаций, MLINE и выносок
        # сохраняют чертёжный контекст, не превращая оформление в ограничения посадки.
        try:
            virtual_geometries = [
                _entity_geometry(virtual, factor)
                for virtual in entity.virtual_entities()
            ]
        except Exception:
            return None
        geometries = [
            geometry for geometry in virtual_geometries if geometry is not None
        ]
        return (
            {"type": "GeometryCollection", "geometries": geometries}
            if geometries
            else None
        )
    if entity_type == "HELIX":
        try:
            points = [
                _point(point, factor)
                for point in entity.flattening(
                    CURVE_FLATTENING_DISTANCE, segments=CURVE_SEGMENTS
                )
            ]
        except Exception:
            return None
        return (
            {"type": "LineString", "coordinates": points} if len(points) >= 2 else None
        )
    if entity_type == "MESH" or (
        entity_type == "POLYLINE"
        and (entity.is_poly_face_mesh or entity.is_polygon_mesh)
    ):
        if _mesh_face_count(entity) > MAX_RENDERED_MESH_FACES:
            return _mesh_anchor(entity, factor)
        return mesh_geometry(entity, factor)
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
        return {
            "type": "LineString",
            "coordinates": [
                _point(entity.dxf.start, factor),
                _point(entity.dxf.end, factor),
            ],
        }
    if entity_type == "LWPOLYLINE":
        return polyline_geometry(entity, factor)
    if entity_type == "POLYLINE":
        if entity.is_3d_polyline:
            coordinates = [
                _point(vertex.dxf.location, factor) for vertex in entity.vertices
            ]
            if entity.is_closed and coordinates and coordinates[-1] != coordinates[0]:
                coordinates.append(coordinates[0])
            return (
                {"type": "LineString", "coordinates": coordinates}
                if len(coordinates) >= 2
                else None
            )
        return polyline_geometry(entity, factor)
    if entity_type == "CIRCLE":
        center = entity.dxf.center
        radius = float(entity.dxf.radius)
        coordinates = [
            _ocs_point(
                entity,
                Vec3(
                    center.x + radius * cos(index * 2 * pi / CIRCLE_SEGMENTS),
                    center.y + radius * sin(index * 2 * pi / CIRCLE_SEGMENTS),
                    center.z,
                ),
                factor,
            )
            for index in range(CIRCLE_SEGMENTS + 1)
        ]
        return {"type": "Polygon", "coordinates": [coordinates]}
    if entity_type == "ARC":
        center = entity.dxf.center
        radius = float(entity.dxf.radius)
        start = float(entity.dxf.start_angle)
        sweep = (float(entity.dxf.end_angle) - start) % 360
        coordinates = []
        for index in range(ARC_SEGMENTS + 1):
            angle = (start + sweep * index / ARC_SEGMENTS) * pi / 180
            coordinates.append(
                _ocs_point(
                    entity,
                    Vec3(
                        center.x + radius * cos(angle),
                        center.y + radius * sin(angle),
                        center.z,
                    ),
                    factor,
                )
            )
        return {"type": "LineString", "coordinates": coordinates}
    if entity_type in {"ELLIPSE", "SPLINE"}:
        try:
            points = [
                _point(point, factor)
                for point in entity.flattening(
                    CURVE_FLATTENING_DISTANCE,
                    segments=CURVE_SEGMENTS
                    if entity_type == "ELLIPSE"
                    else SPLINE_SEGMENTS,
                )
            ]
        except Exception:
            return None
        # Полные эллипсы и замкнутые сплайны могут задавать границу области. Флаг DXF
        # сам по себе недостаточен: концы развёрнутой кривой должны совпадать.
        if (
            len(points) >= 3
            and max(abs(points[0][axis] - points[-1][axis]) for axis in (0, 1))
            <= CURVE_CLOSURE_TOLERANCE
        ):
            points[-1] = points[0]
            return {"type": "Polygon", "coordinates": [points]}
        return (
            {"type": "LineString", "coordinates": points} if len(points) >= 2 else None
        )
    if entity_type == "MPOLYGON":
        return mpolygon_geometry(entity, factor)
    if entity_type == "HATCH":
        return hatch_geometry(entity, factor)
    if entity_type in {"3DFACE", "SOLID", "TRACE"}:
        # У SOLID и TRACE вершины 2 и 3 являются противоположными углами, а не порядком
        # обхода. Буквальный порядок создаёт самопересечение; 3DFACE уже хранит обход грани.
        vertex_names = (
            ("vtx0", "vtx1", "vtx3", "vtx2")
            if entity_type in {"SOLID", "TRACE"}
            else ("vtx0", "vtx1", "vtx2", "vtx3")
        )
        points = [
            _ocs_point(entity, point, factor)
            if entity_type in {"SOLID", "TRACE"}
            else _point(point, factor)
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
        return raster_frame_geometry(entity, factor)
    if entity_type == "INSERT":
        geometries = _insert_instance_geometries(entity, factor, _entity_geometry)
        if geometries:
            # Прямой вызов получает первый видимый экземпляр; reader раскрывает MINSERT
            # в отдельные объекты верхнего уровня ниже.
            return geometries[0]
        return {"type": "Point", "coordinates": _point(entity.dxf.insert, factor)}
    if entity_type in {"TEXT", "ATTDEF"}:
        # Точка TEXT хранится в OCS; точку размещения MTEXT ezdxf уже отдаёт в WCS.
        return {
            "type": "Point",
            "coordinates": _ocs_point(entity, entity.dxf.insert, factor),
        }
    if entity_type == "MTEXT":
        return {"type": "Point", "coordinates": _point(entity.dxf.insert, factor)}
    if entity_type in {"RAY", "XLINE"}:
        # Бесконечную вспомогательную линию нельзя показать без viewport. Оставляем
        # якорь как упрощённый контекст, не выдумывая габариты чертежа.
        return {"type": "Point", "coordinates": _point(entity.dxf.start, factor)}
    return None


def _count_labels(counts: Counter[str]) -> str:
    return ", ".join(f"{name}: {count}" for name, count in sorted(counts.items()))


def _bounded_features(
    source_features: Iterable[dict[str, Any]],
    capacity: SourceGeometryCapacity,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    features: list[dict[str, Any]] = []
    nonfinite_geometry_by_layer: Counter[str] = Counter()
    rendered_coordinate_count = 0

    for feature in source_features:
        properties = feature.get("properties", {})
        source_layer = str(properties.get("source_layer", "0"))
        geometry = feature.get("geometry")
        if not isinstance(geometry, dict) or not _has_only_finite_coordinates(geometry):
            nonfinite_geometry_by_layer[source_layer] += 1
            continue
        coordinate_count = _geometry_coordinate_count(geometry)
        capacity.check(
            features=len(features) + 1,
            coordinates=rendered_coordinate_count + coordinate_count,
            feature_coordinates=coordinate_count,
            layer=source_layer,
        )
        features.append(feature)
        rendered_coordinate_count += coordinate_count

    return features, nonfinite_geometry_by_layer


def _source_features(
    entities: list[Any],
    factor: float,
    inventory: LayerInventory,
    inspection: BlockInspection,
    styles: SourceStyleResolver,
) -> Iterator[dict[str, Any]]:
    for index, entity in enumerate(entities):
        if index in inspection.oversized_meshes:
            source_layer = str(entity.dxf.layer)
            fallback_color = LAYER_COLORS[
                list(inventory.counts).index(source_layer) % len(LAYER_COLORS)
            ]
            face_count = inspection.oversized_meshes[index]
            anchor = _mesh_anchor(entity, factor)
            if anchor is None:
                continue
            yield (
                {
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
                }
            )
            continue
        if index in inspection.oversized_arrays:
            source_layer = str(entity.dxf.layer)
            fallback_color = LAYER_COLORS[
                list(inventory.counts).index(source_layer) % len(LAYER_COLORS)
            ]
            component_count = inspection.oversized_arrays[index]
            instance_count = _insert_instance_count(entity)
            yield (
                {
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
                    # Якорь делает нерешённый исходник доступным на карте, не изображая полный габарит.
                    "geometry": {
                        "type": "Point",
                        "coordinates": _point(entity.dxf.insert, factor),
                    },
                }
            )
            continue
        if index in inspection.explicit_components:
            parent_layer = str(entity.dxf.layer)
            block_name = str(entity.dxf.name)
            attributes = {
                str(attribute.dxf.tag): str(attribute.dxf.text)
                for attribute in entity.attribs
            }
            instances = inspection.explicit_components[index]
            for instance_index, components in enumerate(instances, start=1):
                for component_index, (geometry, virtual, source_layer) in enumerate(
                    components, start=1
                ):
                    fallback_color = LAYER_COLORS[
                        list(inventory.counts).index(source_layer) % len(LAYER_COLORS)
                    ]
                    component_properties: dict[str, Any] = {
                        "source_layer": source_layer,
                        "kind": suggest_layer_kind(source_layer).value,
                        "entity_type": virtual.dxftype(),
                        # У виртуального компонента нет постоянного DXF-дескриптора; связь аудита
                        # сохраняется через дескриптор родителя.
                        "source_handle": str(entity.dxf.get("handle", "")),
                        **styles.entity(virtual, source_layer, fallback_color),
                        **component_definition_provenance(virtual),
                        **common_entity_properties(virtual, factor, attributes),
                        "source_block": block_name,
                        "source_block_parent_layer": parent_layer,
                        "source_block_component": component_index,
                        "source_block_instance": instance_index,
                        "source_block_instances": len(instances),
                        "source_attributes": attributes,
                    }
                    yield (
                        {
                            "type": "Feature",
                            "id": f"dxf-{index}-instance-{instance_index}-component-{component_index}",
                            "properties": component_properties,
                            "geometry": geometry,
                        }
                    )
            continue
        block_instances = (
            _insert_instance_count(entity) if entity.dxftype() == "INSERT" else 1
        )
        if index in inspection.compact_geometries:
            raw_geometries: list[dict[str, Any] | None] = list(
                inspection.compact_geometries.pop(index)
            )
        else:
            raw_geometries = (
                list(_insert_instance_geometries(entity, factor, _entity_geometry))
                if entity.dxftype() == "INSERT" and block_instances > 1
                else [_entity_geometry(entity, factor)]
            )
        geometries = [geometry for geometry in raw_geometries if geometry is not None]
        if not geometries:
            source_layer = str(entity.dxf.layer)
            # Аннотации и внешние ссылки не задают конечный физический габарит. Сбой
            # нормализации остальных поддерживаемых сущностей оставляет неизвестный пропуск,
            # который должен быть видим при допуске назначений слоёв.
            if (
                entity.dxftype() not in CONTEXT_ONLY_ENTITY_TYPES
                and not _is_mesh_context(entity)
                and entity.dxftype() not in inventory.unsupported
                and entity.dxftype() != "INSERT"
            ):
                inventory.unreadable[source_layer] += 1
            continue
        source_layer = str(entity.dxf.layer)
        fallback_color = LAYER_COLORS[
            list(inventory.counts).index(source_layer) % len(LAYER_COLORS)
        ]
        for instance_index, geometry in enumerate(geometries):
            source_attributes = (
                {
                    str(attribute.dxf.tag): str(attribute.dxf.text)
                    for attribute in entity.attribs
                }
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
                **common_entity_properties(entity, factor, source_attributes),
            }
            if entity.dxftype() == "IMAGE":
                properties.update(
                    {
                        "source_raster_frame": True,
                        "source_context_only": True,
                        "source_raster_note": "В DXF сохранена рамка внешней растровой подложки; пиксели не встраиваются в DXF.",
                    }
                )
            if entity.dxftype() in {
                "PDFUNDERLAY",
                "PDFREFERENCE",
                "DWFUNDERLAY",
                "DGNUNDERLAY",
            }:
                properties.update(
                    {
                        "source_underlay_frame": True,
                        "source_context_only": True,
                        "source_underlay_type": entity.dxftype(),
                        "source_underlay_note": "Показана внутренняя граница обрезки внешней подложки; содержимое PDF/DWF/DGN не встраивается в DXF.",
                    }
                )
            if entity.dxftype() == "ACAD_PROXY_ENTITY":
                properties.update(
                    {
                        "source_proxy_graphic": True,
                        "source_context_only": True,
                        "source_proxy_geometries": len(geometry.get("geometries", []))
                        if geometry.get("type") == "GeometryCollection"
                        else 1,
                        "source_proxy_note": "Показана доступная proxy-графика неизвестного CAD-объекта; она не участвует в нормативном расчёте.",
                    }
                )
            if entity.dxftype() == "INSERT":
                properties.update(
                    {
                        "source_block": str(entity.dxf.name),
                        "source_rotation": round(
                            float(entity.dxf.get("rotation", 0) or 0), 3
                        ),
                        "source_scale": [
                            round(float(entity.dxf.get("xscale", 1) or 1), 4),
                            round(float(entity.dxf.get("yscale", 1) or 1), 4),
                        ],
                        "source_attributes": source_attributes or {},
                        "block_rendered": geometry["type"] == "GeometryCollection",
                        "source_block_instances": block_instances,
                        "source_block_instance": instance_index + 1,
                    }
                )
                if index in inspection.context_only_inserts:
                    properties["source_context_only"] = True
                    properties["source_block_context_note"] = (
                        "Блок состоит только из подписи или справочной CAD-геометрии и не участвует в расчёте."
                    )
            suffix = f"-instance-{instance_index + 1}" if len(geometries) > 1 else ""
            yield (
                {
                    "type": "Feature",
                    "id": f"dxf-{index}{suffix}",
                    "properties": properties,
                    "geometry": geometry,
                }
            )


def _source_layers(
    features: list[dict[str, Any]],
    inventory: LayerInventory,
    styles: SourceStyleResolver,
) -> list[Layer]:
    # Имя WASTE_SITES содержит site, но может обозначать наблюдения. Только
    # полигональная геометрия позволяет предложить роль SITE_BORDER.
    polygon_layers = {
        feature["properties"]["source_layer"]
        for feature in features
        if _has_polygon(feature["geometry"])
        and not feature["properties"].get("source_context_only")
    }
    for feature in features:
        if feature["properties"].get("source_context_only"):
            feature["properties"]["kind"] = LayerKind.IGNORE.value
        elif (
            feature["properties"]["kind"] == LayerKind.SITE_BORDER.value
            and feature["properties"]["source_layer"] not in polygon_layers
        ):
            feature["properties"]["kind"] = LayerKind.IGNORE.value

    features_by_layer: dict[str, list[dict[str, Any]]] = {}
    for feature in features:
        source_layer = feature["properties"].get("source_layer")
        if source_layer is not None:
            features_by_layer.setdefault(source_layer, []).append(feature)
    layers: list[Layer] = []
    for index, (name, count) in enumerate(inventory.counts.items()):
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
            inventory.unreadable[name] or inventory.unsupported_by_layer.get(name)
        )
        confidence, reasons, review_required = assess_layer_suggestion(
            kind,
            entity_types=dict(inventory.entity_types[name]),
            has_polygon=name in polygon_layers,
            geometry_complete=geometry_complete,
            boundary_candidate=boundary_candidate,
        )
        style = styles.layer(name, LAYER_COLORS[index % len(LAYER_COLORS)])
        layer_bounds = _geometry_bounds(features_by_layer.get(name, []))
        layers.append(
            Layer(
                id=str(uuid5(NAMESPACE_URL, f"dxf-layer:{name}")),
                source_name=name,
                suggested_kind=kind,
                suggestion_confidence=confidence,
                suggestion_reasons=reasons,
                mapping_review_required=review_required,
                mapping_confirmed=not review_required,
                mapped_kind=kind,
                object_count=count,
                bounds=(
                    layer_bounds[0],
                    layer_bounds[1],
                    layer_bounds[2],
                    layer_bounds[3],
                )
                if layer_bounds is not None
                else None,
                color=style.color,
                linetype=style.linetype,
                lineweight_mm=style.lineweight_mm,
                entity_types=dict(inventory.entity_types[name]),
                geometry_complete=geometry_complete,
                unsupported_geometry_types=dict(
                    inventory.unsupported_by_layer.get(name, {})
                ),
                unreadable_geometry_count=inventory.unreadable[name],
                boundary_candidate=boundary_candidate,
                required=kind == LayerKind.SITE_BORDER,
            )
        )

    usable_boundaries = [
        layer
        for layer in layers
        if layer.boundary_candidate is not None
        and layer.boundary_candidate.status == BoundaryCandidateStatus.USABLE
    ]
    if len(usable_boundaries) > 1:
        # Несколько корректных поверхностей требуют смыслового выбора. Сохраняем
        # редактируемый импорт и ждём явного выбора оператора вместо предпочтения имени.
        for layer in usable_boundaries:
            if layer.suggested_kind == LayerKind.SITE_BORDER:
                layer.suggested_kind = LayerKind.IGNORE
                layer.mapped_kind = LayerKind.IGNORE
                layer.suggestion_confidence = LayerSuggestionConfidence.LOW
                layer.suggestion_reasons = [
                    "Найдено несколько подходящих контуров территории"
                ]
                # Выбор контура выполняет отдельный контрол границы; исключённые альтернативы
                # не участвуют в расчётной территории.
                layer.mapping_review_required = False
                layer.mapping_confirmed = True
                layer.required = False

    kind_by_layer = {layer.source_name: layer.suggested_kind for layer in layers}
    for feature in features:
        properties = feature["properties"]
        if properties.get("source_context_only"):
            continue
        feature_layer = properties.get("source_layer")
        if feature_layer in kind_by_layer:
            properties["kind"] = kind_by_layer[feature_layer].value

    return layers


class EzdxfReader:
    """Reads supported DXF entities and normalizes coordinates to meters."""

    def __init__(self, *, capacity: SourceGeometryCapacity | None = None) -> None:
        self.capacity = capacity or SourceGeometryCapacity()

    def read_prepared_file(self, path: Path) -> DxfImportResult:
        """Читать проверенный DXF потоком ezdxf в ограниченном работнике.

        Нормализация совпадает с прежним чтением. Полная Unicode-копия StringIO не
        удерживается рядом с крупным документом SDK; исходник не переписывается и не
        исправляется recover/audit.
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
            raise ValueError(
                "DXF повреждён или имеет неподдерживаемую структуру"
            ) from error

        with indexed_acis_lookup(document):
            return self._normalize_document(document)

    def _normalize_document(self, document: Drawing) -> DxfImportResult:
        prepare_multileader_transforms(document)
        preview_provenance = read_preview_marker(document)
        styles = SourceStyleResolver(document)
        unit_code = int(document.header.get("$INSUNITS", 0) or 0)
        units_assumed = unit_code not in DXF_UNIT_FACTORS
        units, factor = DXF_UNIT_FACTORS.get(unit_code, ("м (принято)", 1.0))
        entities: list[Any] = list(document.modelspace())
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
            )
            is not None
        ]
        paper_entity_count = 0
        for layout in document.layouts:
            if str(layout.name).lower() == "model":
                continue
            try:
                paper_entity_count += sum(1 for _ in layout)
            except Exception:
                # Повреждённое пространство листа не блокирует импорт пространства модели;
                # исходный файл всё равно сохраняется для выпуска.
                continue
        if not entities and not paper_entity_count:
            raise ValueError("В DXF нет объектов модели")

        inventory = LayerInventory.from_entities(entities)
        inspection = inspect_document_blocks(
            entities, factor, inventory, self.capacity, _entity_geometry
        )
        simplified = Counter(
            entity.dxftype()
            for entity in entities
            if entity.dxftype() in {"RAY", "XLINE"}
        )
        raster_underlays = sum(1 for entity in entities if entity.dxftype() == "IMAGE")
        document_underlays = Counter(
            entity.dxftype()
            for entity in entities
            if entity.dxftype()
            in {"PDFUNDERLAY", "PDFREFERENCE", "DWFUNDERLAY", "DGNUNDERLAY"}
        )
        proxy_entities = sum(
            1 for entity in entities if entity.dxftype() == "ACAD_PROXY_ENTITY"
        )
        features, nonfinite_geometry_by_layer = _bounded_features(
            _source_features(entities, factor, inventory, inspection, styles),
            self.capacity,
        )
        # DXF может содержать только пока неотображаемые proxy, 3D или аннотации.
        # Сохраняем импорт для просмотра метаданных, назначения другого слоя
        # и получения неизменённого исходника вместо потери всего чертежа.

        # append_feature отвергает неконечные координаты до включения в снимок,
        # не исправляя их в вымышленную точку.

        layers = _source_layers(features, inventory, styles)

        warnings = styles.warnings()
        warnings.extend(failure.warning() for failure in inspection.failures)
        if units_assumed:
            warnings.append(
                "Единицы чертежа не заданы; координаты интерпретированы как метры."
            )
        if simplified:
            labels = _count_labels(simplified)
            warnings.append(
                f"Часть конструктивных линий показана упрощённо в карте: {labels}; исходная геометрия сохранена в DXF."
            )
        if raster_underlays:
            warnings.append(
                f"Внешних растровых подложек: {raster_underlays}. На карте показана их рамка; изображение остаётся внешней ссылкой исходного DXF."
            )
        if document_underlays:
            labels = _count_labels(document_underlays)
            shown_frames = sum(
                1
                for feature in features
                if feature["properties"].get("source_underlay_frame")
            )
            warnings.append(
                f"Внешних PDF/DWF/DGN-подложек: {labels}. На карте показаны только безопасные внутренние границы обрезки ({shown_frames}); содержимое остаётся внешней ссылкой исходного DXF."
            )
        if proxy_entities:
            rendered_proxies = sum(
                1
                for feature in features
                if feature["properties"].get("source_proxy_graphic")
            )
            warnings.append(
                f"Proxy-объектов: {proxy_entities}. Доступная proxy-графика показана для {rendered_proxies}; неизвестная семантика не участвует в расчёте, исходный DXF сохранён полностью."
            )
        if inspection.oversized_meshes:
            total = sum(inspection.oversized_meshes.values())
            warnings.append(
                f"Крупных CAD-поверхностей: {len(inspection.oversized_meshes)} ({total} граней). На карте показаны точки привязки; поверхности не участвуют в расчёте, исходный DXF сохранён полностью."
            )
        if inspection.oversized_arrays:
            total = sum(inspection.oversized_arrays.values())
            warnings.append(
                f"Крупных блоковых массивов: {len(inspection.oversized_arrays)} (не менее {total} компонентов). Исходный DXF сохранён, но расчёт зон заблокирован: подготовьте чертёж фрагментом или уменьшите массив."
            )
        if nonfinite_geometry_by_layer:
            labels = ", ".join(
                f"{name}: {count}"
                for name, count in sorted(nonfinite_geometry_by_layer.items())
            )
            warnings.append(
                f"Объекты с некорректными координатами не показаны на карте: {labels}. Исходный DXF сохранён без изменений."
            )
        if inventory.unreadable:
            labels = ", ".join(
                f"{name}: {count}"
                for name, count in sorted(inventory.unreadable.items())
            )
            warnings.append(
                "Часть геометрии не удалось прочитать для карты: "
                f"{labels}. Неполный слой нельзя использовать для расчёта ограничений; "
                "проверьте объекты слоя и подготовьте их расчётное представление в полном DXF."
            )
        if inventory.unsupported:
            labels = ", ".join(
                f"{name}: {count}"
                for name, count in sorted(inventory.unsupported.items())
            )
            warnings.append(
                f"Часть типов доступна только в исходном файле: {labels}. "
                "Если такой слой назначен физическим ограничением, расчёт будет остановлен; "
                "проверьте назначение слоя и подготовьте расчётное представление его физических объектов."
            )
        if not features:
            warnings.append(
                "В карте нет визуализируемой геометрии; исходные объекты и слои сохранены для просмотра и повторного экспорта."
            )
        if paper_entity_count:
            warnings.append(
                f"В листах DXF есть объекты ({paper_entity_count}); карта показывает модельное пространство, исходный файл сохранён полностью."
            )
        # Территория может состоять из нескольких фрагментов. HATCH и блоки дают
        # MultiPolygon или коллекцию полигонов; они допустимы наравне с Polygon
        # при проверке предупреждений и последующем расчёте.
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

        # Далёкие подписи, рамки, якоря сеток и вспомогательные линии остаются на карте,
        # но не расширяют начальный обзор до пустого вида. Когда есть только такой
        # контекст, используем всю видимую геометрию без выдумывания габаритов.
        extent_features = [
            feature
            for feature in features
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
