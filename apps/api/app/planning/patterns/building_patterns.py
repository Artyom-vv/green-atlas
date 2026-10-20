"""Кандидатные контуры и группы посадок возле зданий; безопасность определяет обычный
оценщик плана.
"""

from collections.abc import Iterator
from math import floor, pi

from shapely.geometry import LineString, Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, unary_union

from app.planning.config import MAX_PATTERN_CANDIDATES
from app.planning.patterns.contracts import PlacementMaskRequest
from app.planning.ports import PatternCandidate
from app.planning.search.sampling import (
    bounded_candidates,
    cluster_offsets,
    poisson_candidates,
)
from app.shared.failures import DomainFailure, FailureReason

# Эвристики геометрического поиска, а не нормативные безопасные расстояния.
MIN_FACADE_PROBE_OFFSET_M = 0.5
FACADE_PROBE_OFFSET_INCREMENT_M = 0.05
MAX_FACADE_PROBE_OFFSET_M = 60
MIN_FACADE_CONTOUR_SEGMENT_M = 0.01
FACADE_CONTOUR_BUFFER_QUAD_SEGMENTS = 32
BUILDING_SCREEN_MIN_BAND_WIDTH_M = 24
BUILDING_SCREEN_BAND_SPACING_MULTIPLIER = 3
BUILDING_SCREEN_ORIENTATION_COUNT = 12
BUILDING_SCREEN_ORIENTATION_DIVISOR = 6
MIN_BUILDING_GROUP_SIZE = 2


def generate_building_contour(
    request: PlacementMaskRequest, usable: BaseGeometry, guides: list[dict]
) -> list[PatternCandidate]:
    """Выбирает точки вдоль параллельных фасаду контуров.

    Вызывающий код передаёт рассчитанную безопасную область с учётом роста вида.
    Автоматический отступ следует ближайшему допустимому расстоянию; явный отступ не
    увеличивается без ведома пользователя. Итоговые проверки расстояний и геометрии
    остаются обязательными.
    """
    if usable.is_empty:
        return []
    buildings = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "building"
    ]
    if not buildings:
        return []
    if request.screen_side != "perimeter":
        raise DomainFailure(FailureReason.BUILDING_ROW_PERIMETER_REQUIRED)
    occupied = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "planned_plant_clearance"
    ]
    available = usable.difference(unary_union(occupied)) if occupied else usable
    if available.is_empty:
        return []
    footprint = unary_union(buildings)
    components = list(footprint.geoms) if hasattr(footprint, "geoms") else [footprint]
    accepted: list[PatternCandidate] = []

    def lines(geometry: BaseGeometry) -> Iterator[BaseGeometry]:
        if geometry.geom_type in {"LineString", "LinearRing"}:
            yield geometry
        elif hasattr(geometry, "geoms"):
            for part in geometry.geoms:
                yield from lines(part)

    for index, building in enumerate(components):
        if building.geom_type != "Polygon":
            continue
        offset = (
            request.building_offset_m
            if request.building_offset_m is not None
            else max(
                MIN_FACADE_PROBE_OFFSET_M,
                building.distance(usable) + FACADE_PROBE_OFFSET_INCREMENT_M,
            )
        )
        if offset > MAX_FACADE_PROBE_OFFSET_M:
            continue
        contour = building.buffer(
            offset, quad_segs=FACADE_CONTOUR_BUFFER_QUAD_SEGMENTS
        ).boundary.intersection(available)
        for line in lines(contour):
            if line.length < MIN_FACADE_CONTOUR_SEGMENT_M:
                continue
            count = max(1, floor(line.length / request.spacing_m))
            for number in range(count):
                point = line.interpolate((number + 0.5) * line.length / count)
                accepted.append(
                    PatternCandidate(
                        round(point.x, 6),
                        round(point.y, 6),
                        group_key=f"facade-{index}",
                    )
                )
                if len(accepted) > MAX_PATTERN_CANDIDATES:
                    return bounded_candidates(accepted)
    return bounded_candidates(accepted)


def generate_building_screen(
    request: PlacementMaskRequest, usable: BaseGeometry, guides: list[dict]
) -> list[PatternCandidate]:
    """Размещает группы во внешней полосе здания.

    Все координаты проходят обычную проверку безопасности и расстояний. Ориентация к
    дороге служит геометрическим критерием размещения, а не оценкой видимости.
    """
    buildings = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "building"
    ]
    roads = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "road"
    ]
    if usable.is_empty or not buildings:
        return []
    building_footprint = unary_union(buildings)
    band = (
        building_footprint.buffer(
            max(
                BUILDING_SCREEN_MIN_BAND_WIDTH_M,
                request.spacing_m * BUILDING_SCREEN_BAND_SPACING_MULTIPLIER,
            )
        )
        .difference(building_footprint)
        .intersection(usable)
    )
    occupied = [
        shape(f["geometry"])
        for f in guides
        if f.get("properties", {}).get("kind") == "planned_plant_clearance"
    ]
    if occupied:
        band = band.difference(unary_union(occupied))
    if request.screen_side == "roads" and not roads:
        raise DomainFailure(FailureReason.BUILDING_ROW_ROADS_MISSING)
    road_geometry = unary_union(roads) if roads else None

    def fits(point: Point) -> bool:
        if not band.covers(point):
            return False
        if request.screen_side == "perimeter":
            return True
        assert road_geometry is not None
        wall, _ = nearest_points(building_footprint, point)
        _, road = nearest_points(point, road_geometry)
        toward_road = (point.x - wall.x) * (road.x - wall.x) + (point.y - wall.y) * (
            road.y - wall.y
        ) > 0
        return (
            toward_road
            and point.distance(road_geometry) < wall.distance(road_geometry)
            and not LineString([point, road]).crosses(building_footprint)
        )

    # В узкой полосе пробуем несколько ориентаций группы. Сначала создаём полную
    # группу, затем фильтруем обращённые к дороге позиции: виды остаются сопоставимыми.
    centres = poisson_candidates(
        band,
        max(request.cluster_gap_m, request.spacing_m * 3),
        MAX_PATTERN_CANDIDATES,
        request.seed,
    )
    accepted: list[PatternCandidate] = []
    for index, centre in enumerate(centres):
        variants = []
        for turn in range(BUILDING_SCREEN_ORIENTATION_COUNT):
            points = [
                Point(round(centre.x + x, 6), round(centre.y + y, 6))
                for x, y in cluster_offsets(
                    request.spacing_m,
                    request.cluster_size,
                    turn * pi / BUILDING_SCREEN_ORIENTATION_DIVISOR,
                )
            ]
            variants.append([p for p in points if band.covers(p)])
        points = [p for p in max(variants, key=len) if fits(p)]
        if len(points) >= MIN_BUILDING_GROUP_SIZE:
            accepted.extend(
                PatternCandidate(p.x, p.y, group_key=f"grove-{index}") for p in points
            )
    return bounded_candidates(accepted)
