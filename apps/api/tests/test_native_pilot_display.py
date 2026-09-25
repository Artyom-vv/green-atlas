import pytest

from app.native_query.pilot_display import (
    synchronize_pilot_display,
    validate_pilot_working_zones,
)
from app.planting_zones.contracts import PlantingZoneAssignment
from app.planting_zones.domain import validate_changed_planting_zones
from app.projects.contracts import Project


def test_borrowed_display_keeps_cad_but_replaces_old_working_area():
    cad = {"type": "Feature", "id": "cad-7BF", "geometry": {
        "type": "Point", "coordinates": [5, 5]}, "properties": {
        "kind": "building", "source_layer": "Здания", "source_handle": "7BF"}}
    ring = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 0]]]}
    project = Project.model_validate({
        "id": "native-pilot", "name": "pilot",
        "planting_zones": [{"id": "current", "label": "Текущий участок", "geometry": ring}],
        "geometry": {"feature_collection": {"type": "FeatureCollection", "features": [cad, *[
            {"type": "Feature", "id": f"old-{kind}", "geometry": ring,
             "properties": {"kind": kind, "planting_zone_id": "old", "label": "Ручной участок 1"}}
            for kind in ("planting_area", "allowed", "forbidden")]]}},
    })
    zones = project.planting_zones.copy()
    assert synchronize_pilot_display(project)
    features = project.geometry.feature_collection["features"]
    assert features[0] == cad
    assert len(features) == 2
    assert features[1]["properties"] == {
        "kind": "planting_area", "planting_zone_id": "current", "label": "Текущий участок"}
    assert project.planting_zones == zones
    assert not synchronize_pilot_display(project)


def test_native_working_domain_is_not_validated_against_display_cad_boundary():
    ring = {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]]}
    project = Project.model_validate({"name": "pilot", "geometry": {
        "feature_collection": {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": ring, "properties": {"kind": "site_surface"}}]}}})
    outside = PlantingZoneAssignment(id="search", label="Область поиска", geometry={
        "type": "Polygon", "coordinates": [[[20, 20], [30, 20], [30, 30], [20, 30], [20, 20]]]})
    with pytest.raises(ValueError, match="выходит за границы"):
        validate_changed_planting_zones(project, [outside])
    validate_pilot_working_zones(project, [outside])
    invalid = outside.model_copy(update={"geometry": {
        "type": "Polygon", "coordinates": [[[20, 20], [30, 30], [30, 20], [20, 30], [20, 20]]]}})
    with pytest.raises(ValueError, match="самопересекающийся"):
        validate_pilot_working_zones(project, [invalid])
