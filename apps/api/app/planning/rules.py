"""Общие правила зон, габаритов, роста и направляющих для ручных и шаблонных посадок."""

from __future__ import annotations

from math import ceil

from shapely.geometry import Point, shape
from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry, prep

from app.planning.config import (
    DEFAULT_PLANNING_HORIZON_YEAR,
    SHRUB_LAYOUT_RADIUS_M,
    TREE_LAYOUT_RADIUS_M,
)
from app.planning.contracts import PlanObject
from app.planning.domain import required_spacing
from app.planning.patterns.contracts import (
    PatternPreviewRequest,
    PlacementMaskRequest,
    RowPatternRequest,
)
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.regulations.placement_config import PLACEMENT_CONFIG
from app.shared.failures import DomainFailure, FailureReason
from app.species.catalog import get_species, growth_forecasts
from app.species.forecast import calculation_growth_radii, forecast_at


def road_growth_request(
    request: PatternPreviewRequest,
    planning_horizon_year: int = DEFAULT_PLANNING_HORIZON_YEAR,
) -> PatternPreviewRequest:
    """Отодвигает оси кандидатов у дороги за пределы прогнозной кроны."""
    if isinstance(request, PlacementMaskRequest) and request.mask_id == "road_edges":
        radii = pattern_growth_radii(request, planning_horizon_year)
        if radii:
            return request.model_copy(
                update={
                    "road_offset_m": min(
                        PLACEMENT_CONFIG.layout.maximum_road_offset_m,
                        max(
                            request.road_offset_m,
                            radii[0]
                            + request.edge_offset_m
                            + PLACEMENT_CONFIG.layout.road_edge_extra_m,
                        ),
                    )
                }
            )
    return request


def compiled_planting_zones(
    project: Project,
) -> list[tuple[PlantingZoneAssignment, BaseGeometry, PreparedGeometry]]:
    """Подготавливает геометрию зон один раз для массовых операций на сложном DXF."""
    compiled = []
    for zone in project.planting_zones:
        geometry = shape(zone.geometry)
        if not geometry.is_empty:
            compiled.append((zone, geometry, prep(geometry)))
    return compiled


def planting_zone_at(
    project: Project,
    x: float,
    y: float,
    radius: float,
    compiled: list[tuple[PlantingZoneAssignment, BaseGeometry, PreparedGeometry]]
    | None = None,
) -> PlantingZoneAssignment | None:
    footprint = Point(x, y).buffer(radius)
    zones = compiled if compiled is not None else compiled_planting_zones(project)
    matching = [
        (zone, geometry.area)
        for zone, geometry, prepared in zones
        if prepared.covers(footprint)
    ]
    # Выбираем наиболее конкретную вложенную задачу вместо охватывающего
    # её общего контура территории.
    return min(matching, key=lambda item: item[1], default=(None, 0))[0]


def default_layout_radius(kind: str) -> float:
    return TREE_LAYOUT_RADIUS_M if kind == "tree" else SHRUB_LAYOUT_RADIUS_M


def growth_radii(
    revision_ids: list[str | None],
    size_class: str,
    planning_horizon_year: int = DEFAULT_PLANNING_HORIZON_YEAR,
) -> tuple[float, float] | None:
    canopy_radii: list[float] = []
    root_radii: list[float] = []
    for revision_id in revision_ids:
        if not revision_id:
            continue
        revision = get_species(revision_id)
        canopy, roots = growth_forecasts(revision, size_class)
        canopy_at_horizon = forecast_at(canopy, planning_horizon_year)
        roots_at_horizon = forecast_at(roots, planning_horizon_year)
        if canopy_at_horizon is None or roots_at_horizon is None:
            raise DomainFailure(FailureReason.GROWTH_FORECAST_UNAVAILABLE)
        canopy_radius, root_radius = calculation_growth_radii(
            canopy_at_horizon, roots_at_horizon
        )
        canopy_radii.append(canopy_radius)
        root_radii.append(root_radius)
    return (max(canopy_radii), max(root_radii)) if canopy_radii and root_radii else None


def pattern_growth_radii(
    request: PatternPreviewRequest,
    planning_horizon_year: int = DEFAULT_PLANNING_HORIZON_YEAR,
) -> tuple[float, float] | None:
    if isinstance(request, RowPatternRequest):
        revision_ids = [request.species_revision_id]
    elif request.composition == "mixed":
        revision_ids = [
            request.tree_species_revision_id,
            request.shrub_species_revision_id,
        ]
    else:
        revision_ids = [request.species_revision_id]
    return growth_radii(revision_ids, request.size_class, planning_horizon_year)


def mask_guide_geometries(
    project: Project, request: PlacementMaskRequest
) -> list[dict]:
    if (
        request.mask_id in {"building_screen", "building_contour"}
        and project.geometry is not None
    ):
        from app.building_screen import generation_features

        return generation_features(project, request)
    if request.mask_id != "road_edges" or project.geometry is None:
        return []
    return [
        feature["geometry"]
        for feature in project.geometry.feature_collection.get("features", [])
        if feature.get("properties", {}).get("kind") == "road"
        and feature.get("geometry")
    ]


def effective_pattern_spacing(
    request: PatternPreviewRequest,
    planning_horizon_year: int = DEFAULT_PLANNING_HORIZON_YEAR,
) -> float:
    if isinstance(request, RowPatternRequest):
        return request.spacing_m
    revision_id = (
        request.tree_species_revision_id
        if request.composition == "mixed"
        else request.species_revision_id
    )
    if not revision_id:
        return request.spacing_m
    revision = get_species(revision_id)
    if revision.kind != request.plant_kind:
        raise DomainFailure(
            FailureReason.SPECIES_KIND_MISMATCH,
            {
                "species_revision_id": revision.id,
                "expected_kind": request.plant_kind,
                "actual_kind": revision.kind,
            },
        )
    radius = request.layout_radius_m or default_layout_radius(request.plant_kind)
    canopy, roots = growth_forecasts(revision, request.size_class)
    prototype = PlanObject(
        id="spacing-prototype",
        kind=request.plant_kind,
        x=0,
        y=0,
        radius=radius,
        layout_radius_m=radius,
        size_class=request.size_class,
        species_revision_id=revision.id,
        canopy_forecast=canopy,
        root_forecast=roots,
        group_ids=["pattern-spacing-preview"],
        spacing_policy=request.spacing_policy,
    )
    required = required_spacing(prototype, prototype, planning_horizon_year)
    # Шаг до сантиметра округляется вверх: округление вниз делает каждого
    # соседнего кандидата заведомо недопустимым для того же проверяющего модуля.
    return ceil(required * 100) / 100
