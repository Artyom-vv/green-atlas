"""Pure placement growth projection, separate from terrain and CAD context."""

from collections.abc import Callable

from app.projects.contracts import Project
from app.scene.config import HEIGHT_SCALE, INITIAL_HEIGHTS
from app.scene.contracts import ScenePlantObject
from app.scene.types import EvidenceStatus, GrowthStage
from app.species.contracts import SpeciesRevision
from app.species.forecast import forecast_at


def _scene_growth_stage(
    *,
    size_class: str,
    horizon_year: int,
    scale: float | None,
) -> tuple[GrowthStage, EvidenceStatus]:
    """Return a renderer variant band, never a fabricated biological age."""

    if horizon_year == 0:
        if size_class == "sapling":
            return "planting", "estimated"
        if size_class == "standard":
            return "young", "estimated"
        if size_class == "large":
            return "developing", "estimated"
        return "planting", "missing"
    if scale is None:
        return "planting", "missing"
    if scale < 0.35:
        return "planting", "estimated"
    if scale < 0.62:
        return "young", "estimated"
    if scale < 0.9:
        return "developing", "estimated"
    return "mature", "estimated"


def scene_origin(project: Project) -> tuple[float, float]:
    plan_objects = project.plan.objects if project.plan else []
    if plan_objects:
        min_x = min(item.x for item in plan_objects)
        max_x = max(item.x for item in plan_objects)
        min_y = min(item.y for item in plan_objects)
        max_y = max(item.y for item in plan_objects)
        origin_x = (min_x + max_x) / 2
        origin_y = (min_y + max_y) / 2
    elif project.source_file and project.source_file.bounds:
        origin_x = (project.source_file.bounds[0] + project.source_file.bounds[2]) / 2
        origin_y = (project.source_file.bounds[1] + project.source_file.bounds[3]) / 2
    else:
        origin_x = origin_y = 0.0

    return origin_x, origin_y


def scene_plants(
    project: Project,
    horizon_year: int,
    origin_x: float,
    origin_y: float,
    *,
    species: Callable[[str], SpeciesRevision],
) -> list[ScenePlantObject]:
    plan_objects = project.plan.objects if project.plan else []
    scene_objects: list[ScenePlantObject] = []
    for object_ in plan_objects:
        revision = (
            species(object_.species_revision_id)
            if object_.species_revision_id
            else None
        )
        canopy = forecast_at(object_.canopy_forecast, horizon_year)
        roots = forecast_at(object_.root_forecast, horizon_year)
        layout_radius = object_.layout_radius_m or object_.radius
        scale: float | None = None
        if horizon_year == 0:
            # Keep the current planting footprint for an unassigned
            # object, but use the same catalogue anchor as the 2D
            # forecast when one is available.
            canopy_min = canopy.radius_min_m if canopy else layout_radius
            canopy_max = canopy.radius_max_m if canopy else layout_radius
            heights = INITIAL_HEIGHTS.get(object_.size_class)
            confidence = canopy.confidence if canopy else "unknown"
        elif canopy is None:
            canopy_min = canopy_max = layout_radius
            heights = INITIAL_HEIGHTS.get(object_.size_class)
            confidence = "unknown"
        else:
            canopy_min = canopy.radius_min_m
            canopy_max = canopy.radius_max_m
            if revision:
                scale_anchors = HEIGHT_SCALE[revision.growth_rate]
                lower_year = max(year for year in scale_anchors if year <= horizon_year)
                upper_year = min(year for year in scale_anchors if year >= horizon_year)
                if lower_year == upper_year:
                    scale = scale_anchors[lower_year]
                else:
                    ratio = (horizon_year - lower_year) / (upper_year - lower_year)
                    scale = (
                        scale_anchors[lower_year]
                        + (scale_anchors[upper_year] - scale_anchors[lower_year])
                        * ratio
                    )
                heights = (
                    round(revision.mature_height_min_m * scale, 2),
                    round(revision.mature_height_max_m * min(1, scale + 0.12), 2),
                )
            else:
                heights = None
            confidence = canopy.confidence
        growth_stage, growth_stage_status = _scene_growth_stage(
            size_class=object_.size_class,
            horizon_year=horizon_year,
            scale=scale,
        )
        scene_objects.append(
            ScenePlantObject(
                object_id=object_.id,
                kind=object_.kind,
                species_revision_id=object_.species_revision_id,
                species_id=revision.species_id if revision else None,
                common_name=revision.common_name if revision else None,
                scientific_name=revision.scientific_name if revision else None,
                size_class=object_.size_class,
                model_variant_key=(
                    f"species:{revision.species_id}"
                    if revision
                    else f"generic:{object_.kind}:placeholder"
                ),
                growth_stage=growth_stage,
                growth_stage_status=growth_stage_status,
                forecast_horizon_year=horizon_year,
                local_x=round(object_.x - origin_x, 6),
                local_y=round(object_.y - origin_y, 6),
                crown_shape=revision.crown_shape if revision else "placeholder",
                canopy_radius_min_m=canopy_min,
                canopy_radius_max_m=canopy_max,
                height_min_m=heights[0] if heights else None,
                height_max_m=heights[1] if heights else None,
                root_radius_min_m=roots.radius_min_m if roots else None,
                root_radius_max_m=roots.radius_max_m if roots else None,
                height_status="estimated" if heights else "missing",
                confidence=confidence,
                status=object_.status,
                planting_zone_id=object_.planting_zone_id,
                pattern_id=object_.pattern_id,
                group_ids=object_.group_ids,
                locked=object_.locked,
            )
        )
    return scene_objects
