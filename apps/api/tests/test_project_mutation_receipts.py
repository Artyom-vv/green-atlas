"""Repository receipt publication has the same atomicity as the project write."""
from contextlib import closing
import sqlite3

import pytest

from app.contracts import Project
from app.projects.adapters import InMemoryProjectRepository, SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict, expected_project_version, reset_expected_project_version,
    set_expected_project_version,
)
from app.history.adapters import InMemoryProjectHistory
from test_zone_workflow import project_with_contour


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        yield InMemoryProjectRepository()
    else:
        value = SqliteProjectRepository(tmp_path / "receipts.sqlite3")
        with closing(value._connection):
            yield value


def test_receipt_is_exact_immutable_and_does_not_follow_later_project_state(repository):
    project = repository.create(Project(name="Before"))
    project.name = "Approved"
    receipt = {"digest": "approved-digest", "state_version": 999, "nested": {"ids": ["approved"]}}
    saved = repository.save_with_receipt(project, "zones", "preview-1", receipt)
    receipt["nested"]["ids"].append("caller-mutation")
    actual = repository.mutation_receipt(project.id, "zones", "preview-1")
    assert actual["state_version"] == saved.state_version == 2
    assert actual["project_id"] == project.id
    assert actual["nested"]["ids"] == ["approved"]
    actual["nested"]["ids"].clear()
    project.name = "Later edit"
    repository.save(project)
    assert repository.mutation_receipt(project.id, "zones", "preview-1")["state_version"] == 2
    assert repository.mutation_receipt(project.id, "zones", "preview-1")["nested"]["ids"] == ["approved"]
    assert repository.mutation_receipt(project.id, "plantings", "preview-1") is None


def test_serialization_failure_and_stale_version_publish_neither_project_nor_receipt(repository):
    project = repository.create(Project(name="Before"))
    initial = project.model_dump(mode="json")
    project.name = "Unserializable receipt"
    with pytest.raises(ValueError):
        repository.save_with_receipt(project, "zones", "bad-receipt", {"value": float("nan")})
    assert repository.get(project.id).model_dump(mode="json") == initial
    assert project.state_version == initial["state_version"]
    assert project.updated_at == initial["updated_at"]
    assert repository.mutation_receipt(project.id, "zones", "bad-receipt") is None
    stale = repository.get(project.id)
    project.name = "Concurrent edit"
    repository.save(project)
    with pytest.raises(ProjectVersionConflict):
        repository.save_with_receipt(stale, "zones", "stale-receipt", {})
    assert repository.get(project.id).name == "Concurrent edit"
    assert repository.mutation_receipt(project.id, "zones", "stale-receipt") is None


def test_receipt_uses_authoritative_cas_revision_when_request_context_supplies_version(repository):
    project = repository.create(Project(name="Initial"))
    old_revision = project.model_copy(deep=True)
    repository.save(project)
    token = set_expected_project_version(str(project.state_version))
    try:
        saved = repository.save_with_receipt(old_revision, "zones", "context-version", {})
        assert saved.state_version == 3
        assert repository.mutation_receipt(project.id, "zones", "context-version")["state_version"] == 3
        assert expected_project_version() == 3
    finally:
        reset_expected_project_version(token)


def test_sqlite_receipt_insert_failure_rolls_back_project_projection_and_request_version(tmp_path):
    database = tmp_path / "atomic.sqlite3"
    repository = SqliteProjectRepository(database)
    with closing(repository._connection):
        project = repository.create(Project(name="Before"))
        initial = project.model_dump(mode="json")
        with repository._connection:
            repository._connection.execute("""CREATE TRIGGER receipt_unavailable
                BEFORE INSERT ON project_mutation_receipts
                BEGIN SELECT RAISE(ABORT, 'receipt storage fault'); END""")
        project.name = "Must roll back"
        token = set_expected_project_version(str(project.state_version))
        try:
            with pytest.raises(sqlite3.IntegrityError, match="receipt storage fault"):
                repository.save_with_receipt(project, "zones", "preview-1", {})
            assert project.state_version == initial["state_version"]
            assert project.updated_at == initial["updated_at"]
            assert expected_project_version() == initial["state_version"]
        finally:
            reset_expected_project_version(token)
        reopened = SqliteProjectRepository(database)
        with closing(reopened._connection):
            assert reopened.get(project.id).model_dump(mode="json") == initial
            assert reopened.get(project.id, lightweight=True).name == "Before"
            assert reopened.mutation_receipt(project.id, "zones", "preview-1") is None


@pytest.mark.parametrize("action", ["undo", "redo"])
def test_memory_history_zone_mismatch_does_not_consume_history_entry(action):
    _app, project, _reference = project_with_contour()
    history = InMemoryProjectHistory()
    history.record(project, "Plant change")
    project.plan.objects[0].locked = not project.plan.objects[0].locked
    if action == "redo":
        history.undo(project)
    project.planting_zones[0].label = "Current authoritative zone name"
    before = project.model_dump(mode="json")
    cursor = history.state(project.id)
    with pytest.raises(ValueError, match="прежним участкам"):
        getattr(history, action)(project)
    assert project.model_dump(mode="json") == before
    assert history.state(project.id) == cursor
    history.rebase_planting_zones(project.id, project.planting_zones)
    assert getattr(history, action)(project).planting_zones[0].label == "Current authoritative zone name"
