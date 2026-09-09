import pytest
from pydantic import ValidationError

from app.agent_memory import TaskPatch
from app.agent_perception import MapContext, StaleMapContext, bind_selection, perceive, select_zone_by_spatial_intent
from app.contracts import PlanObject
from test_placement_allocation import application
from shapely.geometry import LineString, mapping


def test_real_selection_is_project_scoped_and_read_only():
    app, project = application()
    project.plan.objects = [PlanObject(id="oak", kind="tree", x=12, y=14, radius=1.6,
                                      planting_zone_id="west", species_revision_id="quercus-robur@2026-08-28.1")]
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    context = MapContext(state_version=project.state_version, object_ids=["oak"], viewport=(0, 0, 30, 30))
    perception = perceive(project, context)
    assert perception["selected_objects"][0]["x"] == 12
    patch = bind_selection(TaskPatch(operation="delete", scope="selection"), perception)
    assert patch.scope == "objects" and patch.object_ids == ["oak"]
    assert app.get(project.id).model_dump_json() == before
    with pytest.raises(ValueError, match="не найден"):
        perceive(project, context.model_copy(update={"object_ids": ["other-project-object"]}))
    with pytest.raises(StaleMapContext):
        perceive(project, context.model_copy(update={"state_version": project.state_version + 1}))


def test_viewport_and_mixed_selection_never_silently_widen_scope():
    _, project = application()
    patch = TaskPatch(scope="selection", quantity=100)
    empty = perceive(project, MapContext(state_version=project.state_version, viewport=(0, 0, 500, 500)))
    assert bind_selection(patch, empty) == patch
    assert empty["viewport_is_selection"] is False
    mixed = {**empty, "selected_zone_ids": ["west"], "selected_objects": [{"id": "oak"}]}
    assert bind_selection(patch, mixed) == patch
    zones = {**empty, "selected_zone_ids": ["west"]}
    bound = bind_selection(patch, zones)
    assert bound.scope == "zones" and bound.zone_ids == ["west"] and bound.quantity == 100
    explicit = TaskPatch(scope="zones", zone_ids=["east"])
    assert bind_selection(explicit, zones) == explicit
    with pytest.raises(ValueError, match="не соответствуют"):
        bind_selection(TaskPatch(scope="selection", zone_ids=["east"]), zones)


@pytest.mark.parametrize("extra", [
    {"viewport": [10, 0, 1, 20]}, {"viewport": [0, 0, float("inf"), 20]},
    {"object_ids": ["one", "one"]}, {"selected_objects": [{"id": "invented"}]},
])
def test_map_context_rejects_invalid_coordinates_and_forged_details(extra):
    with pytest.raises(ValidationError):
        MapContext(state_version=1, **extra)


def test_spatial_zone_choice_uses_real_edge_and_road_evidence_without_mutation():
    app, project = application()
    project.geometry.feature_collection['features'].append({
        'type': 'Feature', 'properties': {'kind': 'road', 'source_id': 'street-1'},
        'geometry': mapping(LineString([(0, 20), (200, 20)])),
    })
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    selected = select_zone_by_spatial_intent(project, anchor='edge', alignment_target='road')
    assert selected['zone_id'] == 'west'
    assert selected['road_count'] == 1
    assert app.get(project.id).model_dump_json() == before


def test_spatial_zone_choice_does_not_substitute_any_zone_when_road_is_missing():
    app, project = application()
    with pytest.raises(ValueError, match='улиц|дорог|проезд'):
        select_zone_by_spatial_intent(project, anchor='edge', alignment_target='road')
