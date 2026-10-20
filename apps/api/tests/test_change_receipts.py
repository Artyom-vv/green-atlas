from pathlib import Path

import pytest
from application_factory import recompose_application
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from test_placement_allocation import application

from app.api import router
from app.application import ProjectApplication
from app.composition import get_application
from app.history.adapters import SqliteProjectHistory
from app.main import http_error
from app.planning.changes.contracts import (
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanObjectDeleteOperation,
)
from app.planning.contracts import PlanObject
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


def durable_application(
    path: Path, project: Project | None = None
) -> tuple[ProjectApplication, Project, SqliteProjectRepository, SqliteProjectHistory]:
    domain, seed = application()
    assert seed.plan is not None
    seed.plan.objects = [
        PlanObject(
            id="one",
            kind="tree",
            x=30,
            y=30,
            radius=1.6,
            planting_zone_id="west",
            group_ids=["group"],
        ),
        PlanObject(
            id="two", kind="tree", x=90, y=90, radius=1.6, planting_zone_id="west"
        ),
        PlanObject(
            id="other", kind="tree", x=450, y=50, radius=1.6, planting_zone_id="east"
        ),
    ]
    repository = SqliteProjectRepository(path)
    history = SqliteProjectHistory(repository)
    if project is None:
        project = repository.create(seed)
    domain = recompose_application(domain, repository=repository, history=history)
    return domain, project, repository, history


def deletion(domain: ProjectApplication, project: Project) -> PlanChangeSetApplyRequest:
    assert project.plan is not None
    preview = domain.preview_change_set(
        project.id,
        PlanChangeSetDraft(
            base_plan_version=project.plan.version,
            label="Удаление выбранного",
            operations=[PlanObjectDeleteOperation(object_id="one")],
        ),
    )
    return PlanChangeSetApplyRequest(
        preview_id=preview.id,
        digest=preview.digest,
        base_plan_version=preview.base_plan_version,
    )


def test_receipt_survives_restart_and_tracks_undo_redo(tmp_path: Path) -> None:
    path = tmp_path / "receipt.sqlite3"
    domain, project, repository, history = durable_application(path)
    request = deletion(domain, project)
    result = domain.apply_change_set(project.id, request)
    receipt = history.receipt(project.id, request.preview_id)
    assert receipt is not None
    assert receipt["deleted_ids"] == ["one"]
    assert receipt["state_version"] == result.state_version
    assert receipt["status"] == "applied"
    repository.close()
    restored, _, restored_repository, _ = durable_application(path, project)
    assert (
        restored.change_set_status(project.id, request.preview_id, request.digest)
        == "applied"
    )
    assert (
        restored.change_set_status(project.id, request.preview_id, "bad")
        == "unavailable"
    )
    before = restored.get(project.id).model_dump_json()
    with pytest.raises(DomainFailure) as failure:
        restored.apply_change_set(project.id, request)
    assert failure.value.reason is FailureReason.CHANGE_ALREADY_APPLIED
    assert restored.get(project.id).model_dump_json() == before
    restored.undo_plan_change(project.id)
    assert (
        restored.change_set_status(project.id, request.preview_id, request.digest)
        == "undone"
    )
    restored_plan = restored.get(project.id).plan
    assert restored_plan is not None
    assert any(obj.id == "one" for obj in restored_plan.objects)
    with pytest.raises(DomainFailure) as failure:
        restored.apply_change_set(project.id, request)
    assert failure.value.reason is FailureReason.CHANGE_REVERTED
    restored.redo_plan_change(project.id)
    assert (
        restored.change_set_status(project.id, request.preview_id, request.digest)
        == "applied"
    )
    restored_plan = restored.get(project.id).plan
    assert restored_plan is not None
    assert not any(obj.id == "one" for obj in restored_plan.objects)
    restored_repository.close()


def test_receipt_and_plan_roll_back_together_on_history_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    domain, project, repository, history = durable_application(
        tmp_path / "atomic.sqlite3"
    )
    request = deletion(domain, project)
    before = domain.get(project.id).model_dump_json()

    def fail(*args: object) -> None:
        raise RuntimeError("simulated history write failure")

    monkeypatch.setattr(history, "_snapshot_payload", fail)
    with pytest.raises(RuntimeError):
        domain.apply_change_set(project.id, request)
    assert domain.get(project.id).model_dump_json() == before
    assert history.receipt(project.id, request.preview_id) is None
    repository.close()


def test_receipt_outlives_bounded_undo_history(tmp_path: Path) -> None:
    domain, project, repository, history = durable_application(
        tmp_path / "bounded.sqlite3"
    )
    history.limit = 1
    first = deletion(domain, project)
    domain.apply_change_set(project.id, first)
    current = domain.get(project.id)
    assert current.plan is not None
    second = domain.preview_change_set(
        project.id,
        PlanChangeSetDraft(
            base_plan_version=current.plan.version,
            label="Второе изменение",
            operations=[PlanObjectDeleteOperation(object_id="two")],
        ),
    )
    domain.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=second.id,
            digest=second.digest,
            base_plan_version=second.base_plan_version,
        ),
    )
    assert first.preview_id not in {
        entry.id for entry in history.state(project.id).entries
    }
    receipt = history.receipt(project.id, first.preview_id)
    assert receipt is not None
    assert receipt["status"] == "applied"
    domain.changes._applied.clear()
    assert (
        domain.change_set_status(project.id, first.preview_id, first.digest)
        == "applied"
    )
    repository.close()


def test_postcommit_internal_failure_keeps_receipt_and_prevents_second_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "unknown-result.sqlite3"
    domain, project, repository, history = durable_application(path)
    request = deletion(domain, project)
    api = FastAPI()
    api.exception_handler(HTTPException)(http_error)
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: domain

    def fail_after_commit(_project_id: str) -> None:
        raise RuntimeError("synthetic private detail after durable write")

    monkeypatch.setattr(domain.changes, "invalidate_spacing", fail_after_commit)
    with TestClient(api) as client:
        response = client.post(
            f"/api/projects/{project.id}/plan/change-sets/apply",
            json=request.model_dump(),
        )
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    assert "synthetic private detail" not in response.text
    receipt = history.receipt(project.id, request.preview_id)
    assert receipt is not None and receipt["status"] == "applied"
    repository.close()

    restored, _, restored_repository, restored_history = durable_application(
        path, project
    )
    try:
        api.dependency_overrides[get_application] = lambda: restored
        before = restored.get(project.id).model_dump_json()
        with TestClient(api) as client:
            response = client.get(
                f"/api/projects/{project.id}/plan/change-sets/{request.preview_id}/receipt",
                params={
                    "digest": request.digest,
                    "base_plan_version": request.base_plan_version,
                },
            )
            assert response.status_code == 200
            assert response.json()["status"] == "applied"
            repeated = client.post(
                f"/api/projects/{project.id}/plan/change-sets/apply",
                json=request.model_dump(),
            )
        assert repeated.status_code == 400
        assert repeated.json()["details"]["reason"] == "CHANGE_ALREADY_APPLIED"
        assert restored.get(project.id).model_dump_json() == before
        assert restored_history.receipt(project.id, request.preview_id) == receipt
    finally:
        restored_repository.close()
