"""HTTP mutation recovery uses durable receipts across all operation kinds."""

from application_factory import recompose_application
from app.composition import get_runtime
from contextlib import ExitStack, closing
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api, planning_assistant as local
from app.contracts import PlanChangeSetApplyRequest
from app.agent_runtime import routes, preview_routes
from app.agent_runtime.store import AgentRunStore
from app.agent_runtime.zone_workflow import zone_geometry_references
from app.history.adapters import SqliteProjectHistory
from app.projects.adapters import SqliteProjectRepository
from test_agent_zone_lifecycle import fail_zone_completion_once, recover_request
from test_zone_workflow import project_with_contour


@pytest.fixture
def persistent_runtime(tmp_path, monkeypatch):
    with ExitStack() as resources:
        project_id = None
        def reopen():
            nonlocal project_id
            app, seed, _reference = project_with_contour()
            repository = SqliteProjectRepository(tmp_path / "project.sqlite3")
            resources.enter_context(closing(repository._connection))
            app = recompose_application(app, repository=repository)
            app = recompose_application(app, history=SqliteProjectHistory(repository))
            if project_id is None:
                project_id = repository.create(seed).id
            project = repository.get(project_id)
            store = AgentRunStore(tmp_path / "runs.sqlite3")
            resources.enter_context(closing(store))
            monkeypatch.setattr(get_runtime(), "application", app)
            monkeypatch.setattr(routes, "_store", lambda: store)
            monkeypatch.setattr(preview_routes, "_store", lambda: store)
            monkeypatch.setattr(local, "configured_model", lambda: "test")
            http = FastAPI()
            http.include_router(routes.router)
            http.include_router(preview_routes.router)
            return app, project, store, TestClient(http)
        yield reopen


def prepared(runtime, operation, monkeypatch):
    app, project, store, client = runtime
    draft = {"operation": operation, "scope_mode": "explicit", "target_count": 1, "plant_kind": "tree",
        "zone_labels": ["West"]}
    texts = {"edit": "Закрепи 1 дерево на участке West.", "delete": "Удали 1 дерево на участке West.",
        "place": "Посади 1 дерево на участке East по площади, порода tilia-cordata@2026-08-28.1.",
        "zones": "Создай участок «Сад» из контура source-area."}
    if operation == "place":
        draft.update(zone_labels=["East"], arrangement="area", species_ids=["tilia-cordata@2026-08-28.1"])
    if operation == "zones":
        reference = next(item["reference"] for item in zone_geometry_references(project) if item["reference"]["feature_id"] == "source-area")
        draft = {"operation": "zones", "scope_mode": "project", "zone": {"operation": "create", "label": "Сад", "geometry_reference": reference}}
    monkeypatch.setattr(local, "local_json", lambda *_a, **_k: {"message": {"content": json.dumps(draft)}})
    base = f"/api/projects/{project.id}/agent-runs"
    created = client.post(base, json={"text": texts[operation]})
    assert created.status_code == 201, created.text
    path = base + "/" + created.json()["state"]["run_id"]
    started = client.post(path + "/run")
    assert started.status_code == 200, started.text
    ready = client.get(path).json()
    assert ready["state"]["status"] == "waiting_approval", ready
    reference = ready["state"]["pending_approval"]["preview_ref"]
    full = client.get(path + ("/zone-preview" if operation == "zones" else "/preview"), params={"preview_ref": reference})
    assert full.status_code == 200, full.text
    assert app.get(project.id).state_version == project.state_version
    return path, reference, full.json()


@pytest.mark.parametrize("operation", ["place", "edit", "delete", "zones"])
@pytest.mark.parametrize("endpoint", ["get", "approve", "resume", "cancel", "answer", "run"])
def test_http_commit_checkpoint_loss_recovers_after_process_restart(persistent_runtime, monkeypatch, operation, endpoint):
    app, project, store, client = persistent_runtime()
    path, reference, preview = prepared((app, project, store, client), operation, monkeypatch)
    run_id = path.rsplit("/", 1)[1]
    fail_zone_completion_once(monkeypatch, store)
    with pytest.raises(OSError):
        client.post(path + "/approve", json={"preview_ref": reference})
    committed = app.get(project.id)
    assert committed.state_version == project.state_version + 1
    interrupted = store.get(project.id, run_id)
    attempt = interrupted.events[-1].payload
    assert interrupted.events[-1].kind == "commit_started"
    assert attempt["execution_attempt_id"] == interrupted.state.execution_attempt_id
    assert attempt["preview_id"] == preview["id"] and attempt["digest"] == preview["digest"]
    # Recovery must work from the receipt even when the project has advanced.
    later = app.get(project.id)
    later.name = "Another manual change after the approved mutation"
    app.repository.save(later)
    expected = app.get(project.id).model_dump(mode="json")
    store.close()
    app.repository._connection.close()
    app, _current, store, client = persistent_runtime()
    assert not app.changes._applied and not app.changes._previews
    response = recover_request(client, path, endpoint, reference)
    assert response.status_code == 200, response.text
    recovered = response.json()
    assert recovered["state"]["status"] == "finished", recovered
    assert recovered["state"]["snapshot_version"] == committed.state_version
    assert sum(event["kind"] == "commit_applied" for event in recovered["events"]) == 1
    assert app.get(project.id).model_dump(mode="json") == expected
    for action in ("get", "approve", "run", "cancel"):
        assert recover_request(client, path, action, reference).json()["revision"] == recovered["revision"]
    assert client.post(path + "/approve", json={"preview_ref": "another-preview"}).status_code == 409
    assert app.get(project.id).model_dump(mode="json") == expected


@pytest.mark.parametrize("operation", ["place", "edit", "delete", "zones"])
def test_http_unknown_receipt_blocks_every_lifecycle_mutation_then_recovers(persistent_runtime, monkeypatch, operation):
    app, project, store, client = persistent_runtime()
    path, reference, _preview = prepared((app, project, store, client), operation, monkeypatch)
    fail_zone_completion_once(monkeypatch, store)
    with pytest.raises(OSError):
        client.post(path + "/approve", json={"preview_ref": reference})
    expected = app.get(project.id).model_dump(mode="json")
    name = "get_zone_change_receipt" if operation == "zones" else "get_applied_change_set_receipt"
    lookup = getattr(app, name)
    monkeypatch.setattr(app, name, lambda *_a: None)
    revision = None
    for endpoint in ("get", "approve", "resume", "cancel", "answer", "run"):
        response = recover_request(client, path, endpoint, reference)
        assert response.status_code == 200, response.text
        unknown = response.json()
        assert unknown["state"]["failure"]["code"] == "APPROVAL_OUTCOME_UNKNOWN"
        assert unknown["state"]["failure"]["retryable"] is False
        assert unknown["state"]["pending_approval"] is None
        revision = revision or unknown["revision"]
        assert unknown["revision"] == revision
        assert app.get(project.id).model_dump(mode="json") == expected
    monkeypatch.setattr(app, name, lookup)
    recovered = client.get(path).json()
    assert recovered["state"]["status"] == "finished"
    assert sum(event["kind"] == "commit_applied" for event in recovered["events"]) == 1
    assert app.get(project.id).model_dump(mode="json") == expected


@pytest.mark.parametrize("operation", ["edit", "zones"])
def test_attempt_checkpoint_failure_prevents_any_project_write(persistent_runtime, monkeypatch, operation):
    app, project, store, client = persistent_runtime()
    path, reference, _preview = prepared((app, project, store, client), operation, monkeypatch)
    before = app.get(project.id).model_dump(mode="json")
    checkpoint = store.checkpoint
    def unavailable(*args, **kwargs):
        if kwargs.get("kind") == "commit_started":
            raise OSError("Cannot persist authorized attempt")
        return checkpoint(*args, **kwargs)
    monkeypatch.setattr(store, "checkpoint", unavailable)
    with pytest.raises(OSError):
        client.post(path + "/approve", json={"preview_ref": reference})
    assert app.get(project.id).model_dump(mode="json") == before


@pytest.mark.parametrize("action", ["undo", "redo"])
def test_zone_history_maintenance_failure_is_repaired_before_plan_history_after_restart(persistent_runtime, monkeypatch, action):
    app, project, store, client = persistent_runtime()
    original_objects = [item.model_dump(mode="json") for item in project.plan.objects]
    edit_path, edit_reference, _preview = prepared((app, project, store, client), "edit", monkeypatch)
    assert client.post(edit_path + "/approve", json={"preview_ref": edit_reference}).json()["state"]["status"] == "finished"
    edited_objects = [item.model_dump(mode="json") for item in app.get(project.id).plan.objects]
    if action == "redo":
        app.undo_plan_change(project.id)
    current = app.get(project.id)
    path, reference, preview = prepared((app, current, store, client), "zones", monkeypatch)

    def maintenance_unavailable(*_args):
        raise OSError("Interrupted after project and receipt commit, before history maintenance")

    monkeypatch.setattr(app.history, "rebase_planting_zones", maintenance_unavailable)
    approved = client.post(path + "/approve", json={"preview_ref": reference})
    assert approved.status_code == 200 and approved.json()["state"]["status"] == "finished"
    expected = app.get(project.id).model_dump(mode="json")
    assert expected["planting_zones"] == preview["after_zones"]
    store.close()
    app.repository._connection.close()
    app, _current, store, client = persistent_runtime()
    history_before = app.history.state(project.id)
    # Even direct history use cannot restore an old zone snapshot or consume
    # a history entry. This guard must work independently of the HTTP runtime.
    with pytest.raises(ValueError, match="прежним участкам"):
        getattr(app.history, action)(app.get(project.id))
    assert app.history.state(project.id) == history_before
    assert app.get(project.id).model_dump(mode="json") == expected
    rebase = app.history.rebase_planting_zones
    monkeypatch.setattr(app.history, "rebase_planting_zones", maintenance_unavailable)
    with pytest.raises(OSError):
        getattr(app, action + "_plan_change")(project.id)
    assert app.history.state(project.id) == history_before
    assert app.get(project.id).model_dump(mode="json") == expected
    monkeypatch.setattr(app.history, "rebase_planting_zones", rebase)
    restored = getattr(app, action + "_plan_change")(project.id)
    assert [item.model_dump(mode="json") for item in restored.planting_zones] == expected["planting_zones"]
    assert restored.geometry.model_dump(mode="json") == expected["geometry"]
    assert restored.geometry_version == expected["geometry_version"]
    assert [item.model_dump(mode="json") for item in restored.plan.objects] == (original_objects if action == "undo" else edited_objects)
    assert restored.state_version == expected["state_version"] + 1


def legacy_applied_checkpoint(runtime, operation, monkeypatch, *, finished=False):
    app, project, store, client = runtime
    path, reference, preview = prepared(runtime, operation, monkeypatch)
    # Historical approval wrote through the domain before persisting any
    # commit_started event. Reproduce that actual write and old checkpoint.
    app.apply_change_set(project.id, PlanChangeSetApplyRequest(preview_id=preview["id"],
        digest=preview["digest"], base_plan_version=preview["base_plan_version"]))
    record = store.get(project.id, path.rsplit("/", 1)[1])
    state = record.state.model_copy(update={"execution_attempt_id": None,
        **({"status": "finished", "pending_approval": None} if finished else {})})
    store.checkpoint(project.id, state.run_id, expected_revision=record.revision, state=state,
        kind="commit_applied" if finished else "legacy_checkpoint", payload={"change_set_id": preview["id"]})
    assert not any(event.kind == "commit_started" for event in store.get(project.id, state.run_id).events)
    return path, reference


@pytest.mark.parametrize("operation", ["place", "edit", "delete"])
@pytest.mark.parametrize("endpoint", ["get", "approve", "resume", "cancel", "answer", "run"])
def test_legacy_applied_without_attempt_is_quarantined_before_any_lifecycle_action(persistent_runtime, monkeypatch, operation, endpoint):
    runtime = persistent_runtime()
    app, project, store, _client = runtime
    path, reference = legacy_applied_checkpoint(runtime, operation, monkeypatch)
    expected = app.get(project.id).model_dump(mode="json")
    store.close()
    app.repository._connection.close()
    app, _current, store, client = persistent_runtime()
    response = recover_request(client, path, endpoint, reference)
    assert response.status_code == 200, response.text
    quarantined = response.json()
    assert quarantined["state"]["status"] == "failed"
    assert quarantined["state"]["failure"]["code"] == "APPROVAL_OUTCOME_UNKNOWN"
    assert quarantined["state"]["failure"]["retryable"] is False
    assert quarantined["state"]["execution_attempt_id"] is None
    assert quarantined["state"]["pending_approval"] is None
    assert quarantined["events"][-1]["kind"] == "legacy_commit_quarantined"
    assert quarantined["events"][-1]["payload"]["receipt_state_version"] == expected["state_version"]
    assert app.get(project.id).model_dump(mode="json") == expected
    # Quarantine is durable, not a transient receipt lookup result. Its
    # barrier survives another restart and later receipt unavailability.
    store.close()
    app.repository._connection.close()
    app, _current, store, client = persistent_runtime()
    monkeypatch.setattr(app, "get_applied_change_set_receipt", lambda *_args: None)
    for action in ("get", "approve", "resume", "cancel", "answer", "run"):
        repeated = recover_request(client, path, action, reference)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["revision"] == quarantined["revision"]
        assert repeated.json()["state"]["failure"]["code"] == "APPROVAL_OUTCOME_UNKNOWN"
        assert app.get(project.id).model_dump(mode="json") == expected


def test_finished_legacy_receipt_remains_historical_without_quarantine(persistent_runtime, monkeypatch):
    runtime = persistent_runtime()
    app, project, store, client = runtime
    path, reference = legacy_applied_checkpoint(runtime, "place", monkeypatch, finished=True)
    record = store.get(project.id, path.rsplit("/", 1)[1])
    expected = app.get(project.id).model_dump(mode="json")
    for endpoint in ("get", "approve", "cancel", "run"):
        response = recover_request(client, path, endpoint, reference)
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == record.revision
        assert response.json()["state"]["status"] == "finished"
    assert app.get(project.id).model_dump(mode="json") == expected
