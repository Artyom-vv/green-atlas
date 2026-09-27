import pytest
from shapely.geometry import box, mapping

from app.agent_memory import TaskPatch, TaskState
from app.agent_planning import DELEGATED_LAYOUT_SAMPLE_LIMIT, prepare_placement, prepare_task
from app.contracts import PlanObject
from test_placement_allocation import application


def test_mixed_contour_uses_one_validated_preview_without_mutating_plan():
    from math import hypot
    from app.planning.domain import required_spacing
    app, project = application()
    project.geometry.feature_collection['features'].append({'type': 'Feature', 'properties': {'kind': 'building'}, 'geometry': mapping(box(75, 75, 125, 125))})
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    task = TaskState().amended(TaskPatch(operation='place', scope='zones', zone_ids=['west'], plant_kind='mixed',
        quantity=60, quantity_mode='target', arrangement='building_contour', species_mode='automatic', constraints=['сохраняя нормативные отступы']), 'user')
    result = prepare_placement(app, project.id, task)
    assert result['requires_confirmation']
    assert result['condition_check']['coverage'] == 'partial'
    assert result['condition_check']['full_compliance_verified'] is False
    assert result['task']['values']['constraints'] == ['сохраняя нормативные отступы']
    additions = [PlanObject.model_validate(obj) for obj in result['change_set']['additions']]
    assert {obj.kind for obj in additions} == {'tree', 'shrub'}
    assert 0 < len(additions) <= 60
    assert all(obj.planting_zone_id == 'west' and obj.species_revision_id for obj in additions)
    assert result['shortfall'] == 60-len(additions)
    previews = [event for event in result['events'] if event['tool'] == 'preview_mask']
    assert 1 <= len(previews) <= DELEGATED_LAYOUT_SAMPLE_LIMIT
    assert all({obj['kind'] for obj in event['result']['change_set']['additions']} == {'tree', 'shrub'} for event in previews)
    assert result['change_set']['id'] in [event['result']['change_set']['id'] for event in previews]
    assert result['found'] == max(attempt['found'] for attempt in result['search']['attempts'] if attempt['usable'])
    assert result['search']['exhaustive'] is False
    assert result['shortfall_evidence']
    assert 'максимальная вместимость не установлена' in result['shortfall_explanation']
    assert result['task']['values']['quantity'] == 60
    assert result['task']['values']['zone_ids'] == ['west']
    for i, a in enumerate(additions):
        for b in additions[i+1:]:
            assert hypot(a.x-b.x, a.y-b.y) + 1e-6 >= required_spacing(a,b)
    assert app.get(project.id).model_dump_json() == before


def test_retained_oak_contour_task_reaches_real_preview():
    app, project = application()
    project.geometry.feature_collection['features'].append({'type': 'Feature', 'properties': {'kind': 'building'}, 'geometry': mapping(box(75, 75, 125, 125))})
    app.repository.save(project)
    state = TaskState().amended(TaskPatch(operation='place', plant_kind='tree', scope='zones', zone_ids=['west'], quantity=100,
                  quantity_mode='target', arrangement='building_contour', species_mode='specified', species_revision_ids=['quercus-robur@2026-08-28.1']), 'user')
    result = prepare_placement(app, project.id, state)
    assert result['requested'] == 100
    assert 0 < result['found'] <= 100
    assert result['shortfall'] == 100 - result['found']
    assert result['requires_confirmation']
    assert app.get(project.id).plan.objects == []


def test_delegated_edge_road_fill_reaches_real_preview_without_user_quantity():
    from shapely.geometry import LineString, mapping

    app, project = application()
    project.geometry.feature_collection['features'].append({
        'type': 'Feature', 'properties': {'kind': 'road', 'source_id': 'street-1'},
        'geometry': mapping(LineString([(0, 100), (200, 100)])),
    })
    app.repository.save(project)
    task = TaskState().amended(TaskPatch(
        operation='place', scope='zones', zone_ids=[], spatial_anchor='edge',
        alignment_target='road', plant_kind='tree', quantity_mode='fill_available',
        arrangement='road_edges', species_mode='automatic', post_action='focus_map',
    ), 'user')
    result = prepare_placement(app, project.id, task)
    assert result['resolved_zone_ids'] == ['west']
    assert result['quantity_mode'] == 'fill_available'
    assert result['delegated_quantity_limit'] == 30
    assert 0 < result['found'] <= 30
    assert result['change_set']['can_apply']
    assert all(item['planting_zone_id'] == 'west' for item in result['change_set']['additions'])
    assert app.get(project.id).plan.objects == []


def test_unknown_scope_and_conditions_are_not_silently_dropped():
    app, project = application()
    state = TaskState().amended(TaskPatch(operation='place', plant_kind='tree', zone_ids=['west'], quantity=10, arrangement='area', species_mode='automatic'), 'user')
    with pytest.raises(ValueError, match='Укажите участок'):
        prepare_placement(app, project.id, state)
    state = state.amended(TaskPatch(scope='zones', exclusions=['кроме северной части']), 'exception')
    with pytest.raises(ValueError, match='исключённые области'):
        prepare_placement(app, project.id, state)


def existing_application():
    app, project = application()
    project.plan.objects = [
        PlanObject(id="one", kind="tree", x=30, y=30, radius=1.6, planting_zone_id="west", group_ids=["group"]),
        PlanObject(id="two", kind="tree", x=90, y=90, radius=1.6, planting_zone_id="west"),
        PlanObject(id="other", kind="tree", x=450, y=50, radius=1.6, planting_zone_id="east"),
    ]
    app.repository.save(project)
    return app, app.get(project.id)


@pytest.mark.parametrize("action,initial,expected", [("lock", False, True), ("unlock", True, False)])
def test_agent_protection_toggle_preserves_all_other_properties(action, initial, expected):
    app, project = existing_application()
    project.plan.objects[0].locked = initial
    # A pre-existing invalid position must not prevent the protection toggle.
    project.plan.objects[0].x = -1000
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    state = TaskState().amended(TaskPatch(operation="edit", edit_action=action, scope="objects", object_ids=["one"]), "user")
    result = prepare_task(app, project.id, state)
    assert result["change_set"]["can_apply"]
    updated = result["change_set"]["updates"][0]
    original = project.plan.objects[0].model_dump(mode="json")
    assert updated == {**original, "locked": expected}
    assert app.get(project.id).model_dump_json() == before


def test_agent_relative_move_preserves_group_species_and_does_not_commit():
    app, project = existing_application()
    before = app.get(project.id).model_dump_json()
    state = TaskState().amended(TaskPatch(operation="edit", edit_action="move", scope="objects", object_ids=["one", "two"], move_dx_m=2.5, move_dy_m=1), "user")
    result = prepare_task(app, project.id, state)
    assert result["change_set"]["can_apply"]
    originals = {obj.id: obj.model_dump(mode="json") for obj in project.plan.objects}
    for updated in result["change_set"]["updates"]:
        original = originals[updated["id"]]
        assert updated == {**original, "x": original["x"] + 2.5, "y": original["y"] + 1}
    assert app.get(project.id).model_dump_json() == before


def test_agent_move_never_unlocks_or_relaxes_outside_zone_target():
    app, project = existing_application()
    project.plan.objects[0].locked = True
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    state = TaskState().amended(TaskPatch(operation="edit", edit_action="move", scope="objects", object_ids=["one", "two"], move_dx_m=-1000), "user")
    result = prepare_task(app, project.id, state)
    assert not result["change_set"]["can_apply"]
    assert app.get(project.id).model_dump_json() == before


def test_replace_species_previews_exact_targets_without_moving_or_committing():
    app, project = existing_application()
    before = app.get(project.id).model_dump_json()
    state = TaskState().amended(TaskPatch(operation="edit", scope="objects", object_ids=["one", "two"],
                                        species_mode="specified", species_revision_ids=["quercus-robur@2026-08-28.1"]), "request")
    result = prepare_task(app, project.id, state)
    assert result["requires_confirmation"]
    updates = result["change_set"]["updates"]
    assert {obj["id"] for obj in updates} == {"one", "two"}
    assert {(obj["x"], obj["y"]) for obj in updates} == {(30, 30), (90, 90)}
    assert updates[0]["group_ids"] == ["group"]
    assert all(obj["species_revision_id"] == "quercus-robur@2026-08-28.1" for obj in updates)
    assert app.get(project.id).model_dump_json() == before


def test_delete_zone_preserves_other_zones_and_never_commits():
    app, project = existing_application()
    before = app.get(project.id).model_dump_json()
    state = TaskState().amended(TaskPatch(operation="delete", scope="zones", zone_ids=["west"]), "request")
    result = prepare_task(app, project.id, state)
    assert result["requires_confirmation"]
    assert set(result["change_set"]["deletion_ids"]) == {"one", "two"}
    assert not result["change_set"]["updates"]
    assert app.get(project.id).model_dump_json() == before


def test_existing_changes_do_not_pick_arbitrary_count_or_ignore_missing_targets():
    app, project = existing_application()
    for patch, error in [
        (TaskPatch(operation="delete", scope="project", quantity=1), "Выберите конкретные"),
        (TaskPatch(operation="delete", scope="objects", object_ids=["one", "missing"]), "больше не существует"),
        (TaskPatch(operation="delete", scope="zones", zone_ids=["missing"]), "больше не существует"),
        (TaskPatch(operation="delete", scope="project", exclusions=["кроме северных"]), "условия сохранены"),
    ]:
        with pytest.raises(ValueError, match=error):
            prepare_task(app, project.id, TaskState().amended(patch, "request"))
    assert len(app.get(project.id).plan.objects) == 3


def test_locked_target_is_not_silently_unlocked_or_partially_applied():
    app, project = existing_application()
    project.plan.objects[0].locked = True
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    state = TaskState().amended(TaskPatch(operation="delete", scope="objects", object_ids=["one", "two"]), "delete")
    result = prepare_task(app, project.id, state)
    assert not result["requires_confirmation"]
    assert not result["change_set"]["can_apply"]
    assert any(item["status"] == "blocked" for item in result["change_set"]["candidate_results"])
    assert app.get(project.id).model_dump_json() == before
