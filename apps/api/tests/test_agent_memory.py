import pytest

from app.agent_memory import ConversationConflict, ConversationStore, TaskPatch, TaskState


def test_switching_edit_actions_cannot_reuse_an_old_displacement():
    state = TaskState().amended(TaskPatch(operation="edit", edit_action="move", scope="objects", object_ids=["one"],
                                        move_dx_m=5, move_dy_m=10), "move")
    state = state.amended(TaskPatch(move_dx_m=2), "distance")
    assert state.values.move_dy_m == 10
    state = state.amended(TaskPatch(edit_action="lock"), "lock")
    assert state.values.move_dx_m is None and state.values.move_dy_m is None
    assert state.provenance["move_dx_m"] == "lock"
    assert state.values.object_ids == ["one"]
    state = state.amended(TaskPatch(edit_action="move", move_dx_m=3), "new-move")
    assert state.values.move_dx_m == 3 and state.values.move_dy_m is None


def message(store, chat, record, text, patch=None):
    return store.append(chat["project_id"], chat["id"], expected_revision=chat["revision"], record_id=record,
                        kind="message", payload={"role": "user", "text": text}, patch=patch)


def test_followups_preserve_task_and_provenance_after_restart(tmp_path):
    path = tmp_path / "chat.sqlite3"
    store = ConversationStore(path)
    chat = message(store, store.create("project"), "first", "100 деревьев вдоль зданий на участке 6",
                   TaskPatch(operation="place", quantity=100, quantity_mode="target", arrangement="building_contour", plant_kind="tree"))
    chat = message(store, chat, "zone", "Допустимая область 6", TaskPatch(zone_ids=["zone-6"]))
    chat = message(store, chat, "auto", "Сам выбери", TaskPatch(species_mode="automatic"))
    chat = message(store, chat, "oak", "Дуб", TaskPatch(species_mode="specified", species_revision_ids=["oak@1"]))
    identity = chat["id"]
    store.close()
    store = ConversationStore(path)
    restored = store.get("project", identity)
    assert restored["task"]["values"] | {} == chat["task"]["values"]
    assert restored["task"]["values"]["quantity"] == 100
    assert restored["task"]["values"]["arrangement"] == "building_contour"
    assert restored["task"]["values"]["zone_ids"] == ["zone-6"]
    assert restored["task"]["provenance"]["quantity"] == "first"
    assert restored["task"]["provenance"]["species_revision_ids"] == "oak"
    assert len(restored["records"]) == 4
    store.close()


def test_decline_does_not_replace_message_or_task(tmp_path):
    store = ConversationStore(tmp_path / "chat.db")
    chat = message(store, store.create("p"), "request", "100 деревьев", TaskPatch(quantity=100))
    chat = store.append("p", chat["id"], expected_revision=chat["revision"], record_id="decline", kind="tool_event", payload={"status": "declined", "proposal_id": "preview"})
    assert chat["task"]["values"]["quantity"] == 100
    assert chat["records"][0]["payload"]["content"]["text"] == "100 деревьев"
    assert chat["records"][1]["kind"] == "tool_event"
    store.close()


def test_retry_idempotency_concurrency_and_project_isolation(tmp_path):
    path = tmp_path / "chat.db"
    first, second = ConversationStore(path), ConversationStore(path)
    initial = first.create("p")
    updated = message(first, initial, "one", "100", TaskPatch(quantity=100))
    assert message(second, initial, "one", "100", TaskPatch(quantity=100))["revision"] == updated["revision"]
    with pytest.raises(ConversationConflict):
        message(second, initial, "two", "200", TaskPatch(quantity=200))
    with pytest.raises(ConversationConflict):
        message(second, updated, "one", "300", TaskPatch(quantity=300))
    with pytest.raises(KeyError):
        first.get("other-project", initial["id"])
    assert first.list("other-project") == []
    other = first.create("p")
    assert other["task"]["values"]["quantity"] is None
    first.close()
    second.close()


def test_explicit_reset_and_automatic_species_do_not_clear_other_fields(tmp_path):
    store = ConversationStore(tmp_path / "chat.db")
    chat = message(store, store.create("p"), "one", "Дуб", TaskPatch(quantity=100, species_mode="specified", species_revision_ids=["oak@1"]))
    chat = message(store, chat, "two", "Любая порода", TaskPatch(species_mode="automatic"))
    assert chat["task"]["values"]["species_revision_ids"] is None
    assert chat["task"]["values"]["quantity"] == 100
    chat = message(store, chat, "three", "Без количества", TaskPatch(quantity=None))
    assert chat["task"]["values"]["quantity"] is None
    with pytest.raises(ValueError):
        store.append("p", chat["id"], expected_revision=chat["revision"], record_id="bad", kind="tool_event", payload={}, patch=TaskPatch(quantity=10))
    store.close()


def test_turn_and_answer_commit_together_and_explicit_new_task_resets(tmp_path):
    store = ConversationStore(tmp_path / "chat.db")
    chat = message(store, store.create("p"), "first", "100", TaskPatch(quantity=100))
    chat = store.append("p", chat["id"], expected_revision=chat["revision"], record_id="second", kind="message",
                        payload={"role": "user", "text": "Новая задача: удалить"}, patch=TaskPatch(operation="delete"),
                        assistant_text="Условия сохранены", reset_task=True)
    assert chat["revision"] == 4
    assert chat["task"]["values"]["quantity"] is None
    assert chat["task"]["values"]["operation"] == "delete"
    assert chat["records"][-1]["record_id"] == "answer:second"
    assert chat["records"][-1]["payload"]["content"]["role"] == "assistant"
    store.close()


def test_create_request_survives_restart_and_retains_current_conversation(tmp_path):
    path = tmp_path / "chat.db"
    store = ConversationStore(path)
    chat = store.create("p", "Фасады", request_id="request-1")
    updated = message(store, chat, "m1", "100 деревьев")
    store.close()
    store = ConversationStore(path)
    assert store.create("p", "Фасады", request_id="request-1") == updated
    assert len(store.list("p")) == 1
    with pytest.raises(ConversationConflict):
        store.create("p", "Сквер", request_id="request-1")
    assert len(store.list("p")) == 1
    store.close()
