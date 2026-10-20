"""Чистая проекция CAD-контекста в сцену с явным происхождением высот и покрытием
исходных данных.
"""

from dataclasses import dataclass
from math import isfinite
from typing import Any

from shapely.geometry import mapping, shape

from app.projects.contracts import Project
from app.scene.config import CONTEXT_KINDS, CONTEXT_SIMPLIFY_TOLERANCE_M
from app.scene.contracts import SceneContextFeature, SceneVerticalPrimitive
from app.scene.types import (
    BaseElevationSource,
    EvidenceStatus,
    GeometrySource,
    HeightSource,
)


@dataclass(frozen=True)
class SceneContext:
    features: list[SceneContextFeature]
    primitives: list[SceneVerticalPrimitive]
    geometry_source: GeometrySource
    building_count: int
    confirmed_heights: int
    estimated_heights: int
    height_sources: set[str]


def _translate_geojson(geometry: dict, origin_x: float, origin_y: float) -> dict:
    """Переводит координаты GeoJSON в локальную систему координат посадок."""

    def translate(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            if len(value) >= 2 and all(
                isinstance(item, (int, float)) for item in value[:2]
            ):
                return [value[0] - origin_x, value[1] - origin_y, *value[2:]]
            return [translate(item) for item in value]
        return value

    return {**geometry, "coordinates": translate(geometry.get("coordinates", []))}


def _context_height(
    properties: dict,
) -> tuple[float | None, EvidenceStatus | None, HeightSource | None]:
    """Читает только свидетельства высоты, нормализованные адаптером DXF.

    Общие ключи вроде height не распознаются: импортированные GIS/DXF часто
    используют их для размеров текста или символов. Два разрешённых ключа явно
    выражают метры и сохраняют происхождение данных.
    """

    source_layer = str(properties.get("source_layer", "")).upper()
    height_properties: tuple[tuple[str, HeightSource], ...] = (
        ("source_extrusion_height_m", "dxf_extrusion"),
        ("source_attribute_height_m", "dxf_attribute"),
    )
    for key, source in height_properties:
        value = properties.get(key)
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and isfinite(float(value))
            and float(value) > 0
        ):
            if source_layer == "GREEN_ATLAS_BUILDING_OSM_HEIGHT":
                return round(float(value), 6), "confirmed", "osm_height"
            if source_layer == "GREEN_ATLAS_BUILDING_OSM_LEVELS":
                return round(float(value), 6), "estimated", "osm_levels"
            return round(float(value), 6), "confirmed", source
    return None, None, None


def _context_base_elevation(properties: dict) -> float | None:
    value = properties.get("source_base_elevation_m")
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(float(value))
    ):
        return round(float(value), 6)
    return None


def _context_base_elevation_source(properties: dict) -> BaseElevationSource | None:
    if _context_base_elevation(properties) is None:
        return None
    source_layer = str(properties.get("source_layer", "")).upper()
    # Только обогащённые слои примера явно связаны с DEM из метаданных.
    # Высота общего слоя OSM_* может иметь другое происхождение и не обозначается Copernicus.
    if source_layer in {
        "GREEN_ATLAS_BUILDING_OSM_HEIGHT",
        "GREEN_ATLAS_BUILDING_OSM_LEVELS",
    }:
        return "copernicus_dem_glo90"
    return "dxf_elevation"


def scene_context(project: Project, origin_x: float, origin_y: float) -> SceneContext:
    context_features: list[SceneContextFeature] = []
    vertical_primitives: list[SceneVerticalPrimitive] = []
    context_snapshot = project.geometry or project.source_geometry
    geometry_source: GeometrySource = (
        "prepared_geometry"
        if project.geometry is not None
        else "source_geometry"
        if project.source_geometry is not None
        else "missing"
    )
    building_feature_count = 0
    building_height_confirmed_count = 0
    building_height_estimated_count = 0
    building_height_sources: set[str] = set()
    if context_snapshot is not None:
        for primitive in context_snapshot.vertical_primitives:
            # Плоские DXF-сущности уже передают высоту и extrusion в контексте сцены.
            # Повторение каждой точечным примитивом раздувает нагрузку без новой геометрии.
            if primitive.primitive_type == "point" and primitive.source_entity_type in {
                "LWPOLYLINE",
                "CIRCLE",
            }:
                continue
            vertical_primitives.append(
                SceneVerticalPrimitive(
                    primitive_id=primitive.primitive_id,
                    primitive_type=primitive.primitive_type,
                    vertices=[
                        [
                            round(vertex[0] - origin_x, 6),
                            round(vertex[1] - origin_y, 6),
                            vertex[2],
                        ]
                        for vertex in primitive.vertices_m
                    ],
                    faces=primitive.faces,
                    source_layer=primitive.source_layer,
                    source_entity_type=primitive.source_entity_type,
                    source_handle=primitive.source_handle,
                    source_file_units=primitive.source_file_units,
                    unit_scale_to_m=primitive.unit_scale_to_m,
                    source_space=primitive.source_space,
                    vertical_evidence=primitive.vertical_evidence,
                    extrusion_vector_m=primitive.extrusion_vector_m,
                    terrain_mapping_status=primitive.terrain_mapping_status,
                    terrain_mapping_basis=primitive.terrain_mapping_basis,
                    terrain_confidence=primitive.terrain_confidence,
                    source_dataset=primitive.source_dataset,
                    source_url=primitive.source_url,
                    source_attribution=primitive.source_attribution,
                    vertical_datum=primitive.vertical_datum,
                    vertical_datum_offset_m=primitive.vertical_datum_offset_m,
                )
            )
        # Сохраняем все подходящие объекты выбранной области. Браузер объединяет их
        # по смысловым видам; произвольный лимит количества рвал модель города
        # без уменьшения числа отрисовок. Рельеф, глубины и высоты не выдумываются.
        # Сцена использует ту же исходную карту, что и 2D, включая пустые рабочие зоны.
        # Специалист может осмотреть другую часть чертежа до размещения посадок.
        for index, feature in enumerate(
            context_snapshot.feature_collection.get("features", [])
        ):
            properties = feature.get("properties", {})
            kind = str(properties.get("kind", ""))
            if kind not in CONTEXT_KINDS:
                continue
            try:
                geometry = shape(feature["geometry"])
                if geometry.is_empty:
                    continue
                # Per-feature simplification preserves one polygon's topology,
                # not its shared edges with adjacent pavement. Different ring
                # starts/vertices turn the same arc into different chords and
                # open visible seams. Keep accepted surface rings (including
                # holes) and their linear boundaries unchanged in both the raw
                # 3D scene and the render packet. Only independent linework may
                # use the display tolerance; this adds no precision evidence.
                if kind not in {
                    "road",
                    "lawn",
                    "water",
                    "building",
                }:
                    geometry = geometry.simplify(
                        CONTEXT_SIMPLIFY_TOLERANCE_M, preserve_topology=True
                    )
                translated = mapping(geometry)
            except Exception:
                continue
            height_m, height_status, height_source = (
                _context_height(properties)
                if kind == "building"
                else (None, None, None)
            )
            if kind == "building":
                building_feature_count += 1
                if height_status == "confirmed":
                    building_height_confirmed_count += 1
                elif height_status == "estimated":
                    building_height_estimated_count += 1
                if height_source is not None:
                    building_height_sources.add(height_source)
            base_elevation_m = _context_base_elevation(properties)
            context_features.append(
                SceneContextFeature(
                    feature_id=str(
                        feature.get("id")
                        or properties.get("source_handle")
                        or f"context-{index}"
                    ),
                    kind=kind,
                    geometry=_translate_geojson(translated, origin_x, origin_y),
                    label=str(properties.get("label"))
                    if properties.get("label")
                    else None,
                    source_layer=str(properties.get("source_layer"))
                    if properties.get("source_layer")
                    else None,
                    source_entity_type=str(properties.get("entity_type"))
                    if properties.get("entity_type")
                    else None,
                    source_handle=str(properties.get("source_handle"))
                    if properties.get("source_handle")
                    else None,
                    base_elevation_m=base_elevation_m,
                    base_elevation_status="confirmed"
                    if base_elevation_m is not None
                    else "missing",
                    base_elevation_source=_context_base_elevation_source(properties),
                    height_m=height_m,
                    height_status=(height_status or "missing")
                    if kind == "building"
                    else None,
                    height_source=height_source,
                )
            )
    return SceneContext(
        features=context_features,
        primitives=vertical_primitives,
        geometry_source=geometry_source,
        building_count=building_feature_count,
        confirmed_heights=building_height_confirmed_count,
        estimated_heights=building_height_estimated_count,
        height_sources=building_height_sources,
    )
