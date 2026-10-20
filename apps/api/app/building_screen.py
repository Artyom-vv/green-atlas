"""Контекст зданий и проверяемое предложение через общий планировщик."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union

from app.geometry.building_contracts import BuildingScreenRequest, BuildingScreenTargets
from app.planning.contracts import PlanObject
from app.planning.domain import required_spacing
from app.planning.patterns.contracts import PlacementMaskRequest
from app.planning.recommendation.config import (
    BUILDING_SCREEN_BUFFER_QUADRANT_SEGMENTS,
    BUILDING_SCREEN_CLEARANCE_MARGIN_M,
    BUILDING_SCREEN_CLUSTER_GAP_M,
    BUILDING_SCREEN_CLUSTER_SIZE,
    BUILDING_SCREEN_CONTEXT_RADIUS_M,
    BUILDING_SCREEN_DEFAULT_TARGET_COUNT,
    BUILDING_SCREEN_EDGE_OFFSET_M,
)
from app.planning.recommendation.contracts import (
    EvidenceAssessment,
    RecommendationPreview,
)
from app.planning.rules import default_layout_radius
from app.projects.contracts import Project
from app.shared.error_messages import message
from app.shared.failures import DomainFailure, FailureReason
from app.species.catalog import get_species, growth_forecasts

if TYPE_CHECKING:
    from app.application import ProjectApplication


def screen_features(project: Project, zone_ids: list[str]) -> list[dict[str, Any]]:
    selected = set(zone_ids)
    zones = [zone for zone in project.planting_zones if zone.id in selected]
    if not selected or selected - {zone.id for zone in zones}:
        raise DomainFailure(FailureReason.BUILDING_SCREEN_ZONES_INVALID)
    scope = unary_union([shape(zone.geometry) for zone in zones]).buffer(
        BUILDING_SCREEN_CONTEXT_RADIUS_M
    )
    result: list[dict[str, Any]] = []
    features = (
        project.geometry.feature_collection.get("features", [])
        if project.geometry
        else []
    )
    for feature in features:
        if feature.get("properties", {}).get("kind") not in {
            "building",
            "road",
        } or not feature.get("geometry"):
            continue
        geometry = shape(feature["geometry"])
        if not geometry.is_empty and geometry.intersects(scope):
            if feature["properties"][
                "kind"
            ] == "building" and geometry.geom_type not in {"Polygon", "MultiPolygon"}:
                continue
            result.append(feature)
    return result


def targets(project: Project, zone_ids: list[str]) -> BuildingScreenTargets:
    features = screen_features(project, zone_ids)
    buildings = [
        shape(feature["geometry"])
        for feature in features
        if feature["properties"]["kind"] == "building"
    ]
    return BuildingScreenTargets(
        geometry=mapping(unary_union(buildings)) if buildings else None,
        has_roads=any(feature["properties"]["kind"] == "road" for feature in features),
    )


def generation_features(
    project: Project, request: PlacementMaskRequest
) -> list[dict[str, Any]]:
    features = screen_features(project, request.zone_ids)
    if not project.plan or not project.plan.objects:
        return features
    radius = request.layout_radius_m or default_layout_radius(request.plant_kind)
    prototype = PlanObject(
        kind=request.plant_kind,
        x=0,
        y=0,
        radius=radius,
        size_class=request.size_class,
        species_revision_id=request.species_revision_id,
    )
    if request.species_revision_id:
        prototype.canopy_forecast, prototype.root_forecast = growth_forecasts(
            get_species(request.species_revision_id), request.size_class
        )
    # Сохранённые посадки ограничивают сам поиск. Добавочный сантиметр защищает
    # округлённые координаты у границы буфера; окончательное решение даёт валидатор.
    occupied = unary_union(
        [
            Point(plant.x, plant.y).buffer(
                required_spacing(prototype, plant, request.scenario_horizon_year)
                + BUILDING_SCREEN_CLEARANCE_MARGIN_M,
                quad_segs=BUILDING_SCREEN_BUFFER_QUADRANT_SEGMENTS,
            )
            for plant in project.plan.objects
        ]
    )
    features.append(
        {
            "type": "Feature",
            "properties": {"kind": "planned_plant_clearance"},
            "geometry": mapping(occupied),
        }
    )
    return features


def preview(
    application: ProjectApplication, project_id: str, request: BuildingScreenRequest
) -> RecommendationPreview:
    project = application.get(project_id)
    context = targets(project, request.zone_ids)
    if not context.geometry:
        raise DomainFailure(FailureReason.BUILDING_SCREEN_BUILDINGS_MISSING)
    result = application.preview_pattern(
        project_id,
        PlacementMaskRequest(
            scenario_horizon_year=request.scenario_horizon_year,
            mask_id="building_contour"
            if request.arrangement == "contour"
            else "building_screen",
            screen_side=request.screen_side,
            base_plan_version=request.base_plan_version,
            zone_ids=request.zone_ids,
            placement_mode="count" if request.max_sites else "spacing",
            target_count=request.max_sites or BUILDING_SCREEN_DEFAULT_TARGET_COUNT,
            species_revision_id=request.species_revision_id,
            size_class=request.size_class,
            spacing_m=request.spacing_m,
            building_offset_m=request.building_offset_m,
            spacing_policy=request.spacing_policy,
            cluster_size=BUILDING_SCREEN_CLUSTER_SIZE,
            cluster_gap_m=BUILDING_SCREEN_CLUSTER_GAP_M,
            edge_offset_m=BUILDING_SCREEN_EDGE_OFFSET_M,
        ),
    )
    return RecommendationPreview(
        planning_horizon_year=result.planning_horizon_year,
        arrangement="building_screen",
        target_geometry=context.geometry,
        profile="shade",
        change_set=result.change_set,
        search_domains=result.search_domains,
        skipped=result.skipped,
        data_gaps=result.unverified_data,
        evidence=EvidenceAssessment(
            spatial_constraints="partial",
            species_catalog="verified",
            note=message(
                "building_screen.contour_evidence"
                if request.arrangement == "contour"
                else "building_screen.groves_evidence"
            ),
        ),
    )
