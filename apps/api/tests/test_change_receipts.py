
from application_factory import recompose_application
import pytest

from app.contracts import PlanChangeSetApplyRequest, PlanChangeSetDraft
from app.history.adapters import SqliteProjectHistory
from app.projects.adapters import SqliteProjectRepository
from test_agent_planning import existing_application


def durable_application(path, project=None):
    domain, seed = existing_application()
    repository = SqliteProjectRepository(path)
    if project is None:
        project = repository.create(seed)
    domain = recompose_application(domain, repository=repository)
    domain = recompose_application(domain, history=SqliteProjectHistory(repository))
    return domain, project


def deletion(domain, project):
    preview = domain.preview_change_set(project.id, PlanChangeSetDraft(
        base_plan_version=project.plan.version, label="Удаление выбранного",
        operations=[{"type": "delete", "object_id": "one"}]))
    return PlanChangeSetApplyRequest(preview_id=preview.id, digest=preview.digest, base_plan_version=preview.base_plan_version)


def test_receipt_survives_restart_and_tracks_undo_redo(tmp_path):
    path = tmp_path / "receipt.sqlite3"
    domain, project = durable_application(path)
    request = deletion(domain, project)
    result = domain.apply_change_set(project.id, request)
    receipt = domain.history.receipt(project.id, request.preview_id)
    assert receipt["deleted_ids"] == ["one"]
    assert receipt["state_version"] == result.state_version
    assert receipt["status"] == "applied"
    domain.repository._connection.close()
    restored, _ = durable_application(path, project)
    assert restored.change_set_status(project.id, request.preview_id, request.digest) == "applied"
    assert restored.change_set_status(project.id, request.preview_id, "bad") == "unavailable"
    before = restored.get(project.id).model_dump_json()
    with pytest.raises(ValueError, match="уже применены"):
        restored.apply_change_set(project.id, request)
    assert restored.get(project.id).model_dump_json() == before
    restored.undo_plan_change(project.id)
    assert restored.change_set_status(project.id, request.preview_id, request.digest) == "undone"
    assert any(obj.id == "one" for obj in restored.get(project.id).plan.objects)
    with pytest.raises(ValueError, match="отменены"):
        restored.apply_change_set(project.id, request)
    restored.redo_plan_change(project.id)
    assert restored.change_set_status(project.id, request.preview_id, request.digest) == "applied"
    assert not any(obj.id == "one" for obj in restored.get(project.id).plan.objects)
    restored.repository._connection.close()


def test_receipt_and_plan_roll_back_together_on_history_failure(tmp_path, monkeypatch):
    domain, project = durable_application(tmp_path / "atomic.sqlite3")
    request = deletion(domain, project)
    before = domain.get(project.id).model_dump_json()
    def fail(*args):
        raise RuntimeError("simulated history write failure")
    monkeypatch.setattr(domain.history, "_snapshot_payload", fail)
    with pytest.raises(RuntimeError):
        domain.apply_change_set(project.id, request)
    assert domain.get(project.id).model_dump_json() == before
    assert domain.history.receipt(project.id, request.preview_id) is None
    domain.repository._connection.close()


def test_receipt_outlives_bounded_undo_history(tmp_path):
    domain, project = durable_application(tmp_path / "bounded.sqlite3")
    domain.history.limit = 1
    first = deletion(domain, project)
    domain.apply_change_set(project.id, first)
    current = domain.get(project.id)
    second = domain.preview_change_set(project.id, PlanChangeSetDraft(
        base_plan_version=current.plan.version, label="Второе изменение",
        operations=[{"type": "delete", "object_id": "two"}]))
    domain.apply_change_set(project.id, PlanChangeSetApplyRequest(
        preview_id=second.id, digest=second.digest, base_plan_version=second.base_plan_version))
    assert first.preview_id not in {entry.id for entry in domain.history.state(project.id).entries}
    assert domain.history.receipt(project.id, first.preview_id)["status"] == "applied"
    domain.changes._applied.clear()
    assert domain.change_set_status(project.id, first.preview_id, first.digest) == "applied"
    domain.repository._connection.close()
