from app.agent_memory import TaskPatch, TaskState
from app.agent_readiness import next_question


def test_one_question_advances_without_repeating_answered_conditions():
    state = TaskState().amended(TaskPatch(operation="place", scope="zones", plant_kind="tree", quantity=100,
                                         arrangement="building_contour", species_mode="automatic"), "first")
    assert next_question(state) == "На каком участке выполнить посадку?"
    state = state.amended(TaskPatch(zone_ids=["six"]), "zone")
    assert next_question(state) is None
    state = state.amended(TaskPatch(species_mode="specified", species_revision_ids=["oak"]), "species")
    assert next_question(state) is None
    assert state.values.quantity == 100


def test_delete_does_not_require_placement_settings():
    state = TaskState().amended(TaskPatch(operation="delete", scope="objects", object_ids=["one"]), "delete")
    assert next_question(state) is None


def test_unbound_selection_cannot_be_treated_as_ready():
    state = TaskState().amended(TaskPatch(operation="delete", scope="selection"), "delete")
    assert next_question(state)


def test_protection_and_movement_do_not_ask_for_species():
    for action in ("lock", "unlock"):
        state = TaskState().amended(TaskPatch(operation="edit", edit_action=action, scope="objects", object_ids=["one"]), "user")
        assert next_question(state) is None
    state = TaskState().amended(TaskPatch(operation="edit", edit_action="move", scope="objects", object_ids=["one"]), "user")
    assert "расстояние" in next_question(state)
    state = state.amended(TaskPatch(move_dx_m=2.5), "distance")
    assert next_question(state) is None


def test_unsupported_constraint_is_not_marked_ready_for_execution():
    state = TaskState().amended(TaskPatch(operation="place", scope="zones", zone_ids=["six"],
                                         plant_kind="tree", quantity=20,
                                         arrangement="building_contour", species_mode="automatic",
                                         constraints=["отступ 10 метров"]), "user")
    assert next_question(state) == "Уточните ограничение для расчёта на карте."


def test_supported_constraint_is_ready_for_execution():
    state = TaskState().amended(TaskPatch(operation="place", scope="zones", zone_ids=["six"],
                                         plant_kind="tree", quantity=20,
                                         arrangement="building_contour", species_mode="automatic",
                                         constraints=["с соблюдением нормативных отступов"]), "user")
    assert next_question(state) is None
