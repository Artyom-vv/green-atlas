"""Separate saved CAD display from the source project's derived work overlays."""
from app.planting_zones.contracts import PlantingZoneAssignment
from app.planting_zones.domain import (
    attach_planting_zone_features,
    validate_planting_zones,
)
from app.projects.contracts import Project


def validate_pilot_working_zones(project: Project, zones: list[PlantingZoneAssignment]) -> None:
    """AOI validity only; native membership rejects each out-of-site planting.

    Never consult historical display CAD polygons for native-pilot admission.
    This does not certify that the whole user-drawn AOI is inside the CAD site.
    """
    validate_planting_zones(project, zones, changed_only=True, check_source_boundary=False)


def synchronize_pilot_display(project: Project) -> bool:
    """Keep CAD features and only this project's current working-area overlays.

    This does not calculate CAD geometry or change zones/plantings. A borrowed
    display snapshot can contain generated planting_area/allowed/forbidden
    features; those must not masquerade as original source objects.
    """
    if project.geometry is None:
        return False
    before = project.geometry.feature_collection.get("features", [])
    project.geometry.feature_collection["features"] = [
        feature for feature in before
        if feature.get("properties", {}).get("kind") not in {"allowed", "forbidden"}
    ]
    attach_planting_zone_features(project)
    return before != project.geometry.feature_collection["features"]
