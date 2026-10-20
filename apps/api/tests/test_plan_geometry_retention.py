"""Plan transactions retain checked CAD files without serializing their geometry."""

import json
import sqlite3
from hashlib import sha256
from pathlib import Path
from threading import RLock

import pytest

from app.geometry.contracts import GeometrySnapshot
from app.history.adapters import SqliteProjectHistory
from app.history.application import PlanHistoryApplication, history_basis
from app.planning.contracts import Plan, PlanObject
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project, ProjectStatus
from app.projects.geometry_storage import GEOMETRY_STORAGE_KEY


def graph(marker: str) -> GeometrySnapshot:
    return GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": index,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [[index, 0], [index, 40]],
                    },
                    "properties": {"source_layer": "CAD", "marker": marker},
                }
                for index in range(32)
            ],
        }
    )


def saved_project(repository: SqliteProjectRepository) -> Project:
    return repository.create(
        Project(
            name="Retained CAD",
            status=ProjectStatus.EDITING,
            geometry=graph("mapped"),
            source_geometry=graph("source"),
            geometry_version=7,
            plan=Plan(
                objects=[
                    PlanObject(id="first", kind="tree", x=10, y=10, radius=1),
                    PlanObject(id="second", kind="tree", x=20, y=20, radius=1),
                ]
            ),
        )
    )


def row(repository: SqliteProjectRepository, project_id: str) -> tuple:
    return tuple(
        repository._connection.execute(
            "SELECT payload, projection, state_version, updated_at FROM projects WHERE id=?",
            (project_id,),
        ).fetchone()
    )


def files(repository: SqliteProjectRepository, project: Project) -> dict:
    assert repository._geometry_store is not None
    return {
        path.name: (sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns)
        for path in repository._geometry_store.directory(project.id).glob("*.json.gz")
    }


def plan_application(
    repository: SqliteProjectRepository, history: SqliteProjectHistory
) -> PlanHistoryApplication:
    return PlanHistoryApplication(repository, history, RLock(), lambda _: None)


@pytest.mark.parametrize("operation", ["add", "move", "delete"])
def test_plan_commit_undo_redo_restart_retains_both_checked_geometry_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    database = tmp_path / "plan.sqlite3"
    repository = SqliteProjectRepository(database)
    history = SqliteProjectHistory(repository)
    project = saved_project(repository)
    expected_files = files(repository, project)
    expected_refs = json.loads(row(repository, project.id)[0])[GEOMETRY_STORAGE_KEY]
    assert len(expected_files) == 2

    def no_geometry_staging(*args: object, **kwargs: object) -> None:
        raise AssertionError("A plan operation must not serialize CAD geometry")

    assert repository._geometry_store is not None
    monkeypatch.setattr(
        repository._geometry_store, "_stage_collection", no_geometry_staging
    )
    try:
        project = repository.get(project.id)
        before = history_basis(project)
        assert project.plan is not None
        if operation == "add":
            project.plan.objects.append(
                PlanObject(id="third", kind="tree", x=30, y=30, radius=1)
            )
        elif operation == "move":
            project.plan.objects[0].x = 12
        else:
            project.plan.objects.pop()
        project.plan.version += 1
        expected_plan = project.plan.model_copy(deep=True)
        saved = plan_application(repository, history).commit(
            project,
            before,
            operation,
            "change",
            receipt={"operation": operation},
        )
        assert saved.state_version == 2
        assert history.receipt(project.id, "change")["status"] == "applied"
        assert (
            json.loads(row(repository, project.id)[0])[GEOMETRY_STORAGE_KEY]
            == expected_refs
        )
        undone = history.undo(repository.get(project.id))
        assert undone.plan is not None and before.plan is not None
        assert undone.plan.objects == before.plan.objects
        assert undone.plan.version == expected_plan.version + 1
        assert history.receipt(project.id, "change")["status"] == "undone"
        redone = history.redo(repository.get(project.id))
        assert redone.plan is not None
        assert redone.plan.objects == expected_plan.objects
        assert redone.plan.version == undone.plan.version + 1
        assert redone.state_version == 4
        assert history.receipt(project.id, "change")["status"] == "applied"
        assert files(repository, project) == expected_files
        assert (
            json.loads(row(repository, project.id)[0])[GEOMETRY_STORAGE_KEY]
            == expected_refs
        )
    finally:
        repository.close()
    reopened = SqliteProjectRepository(database)
    try:
        restored = reopened.get(project.id)
        assert restored.plan is not None
        assert restored.plan.objects == expected_plan.objects
        assert restored.geometry == graph("mapped")
        assert restored.source_geometry == graph("source")
        assert files(reopened, restored) == expected_files
        assert SqliteProjectHistory(reopened).state(project.id).can_undo
    finally:
        reopened.close()


@pytest.mark.parametrize("replacement", [False, True])
def test_generic_or_geometry_replacing_history_uses_full_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement: bool
) -> None:
    repository = SqliteProjectRepository(tmp_path / "full.sqlite3")
    history = SqliteProjectHistory(repository)
    project = saved_project(repository)
    before = history_basis(project)
    if replacement:
        project.geometry = graph("new")
    else:
        assert project.geometry is not None
        project.geometry.feature_collection["features"][0]["properties"]["marker"] = (
            "new"
        )
    assert repository._geometry_store is not None
    calls = []
    original = repository._geometry_store._stage_collection

    def observe(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(repository._geometry_store, "_stage_collection", observe)
    try:
        history.commit(project, before, "Geometry change", plan_only=replacement)
        assert calls
        restored = repository.get(project.id)
        assert restored.geometry is not None
        assert (
            restored.geometry.feature_collection["features"][0]["properties"]["marker"]
            == "new"
        )
    finally:
        repository.close()


@pytest.mark.parametrize("field,value", [("name", "Renamed"), ("geometry_version", 8)])
def test_unexpected_nonplan_change_keeps_full_save_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    repository = SqliteProjectRepository(tmp_path / "metadata.sqlite3")
    project = saved_project(repository)
    before = history_basis(project)
    setattr(project, field, value)
    assert repository._geometry_store is not None
    calls = []
    original = repository._geometry_store._stage_collection

    def observe(*args: object, **kwargs: object) -> object:
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(repository._geometry_store, "_stage_collection", observe)
    try:
        SqliteProjectHistory(repository).commit(
            project, before, "Metadata", plan_only=True
        )
        assert calls
        assert getattr(repository.get(project.id), field) == value
    finally:
        repository.close()


def test_plan_receipt_failure_rolls_back_revision_history_and_retained_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = SqliteProjectRepository(tmp_path / "rollback.sqlite3")
    history = SqliteProjectHistory(repository)
    project = saved_project(repository)
    application = plan_application(repository, history)
    before = history_basis(project)
    assert project.plan is not None
    project.plan.version += 1
    application.commit(project, before, "First", "same", receipt={"step": 1})
    expected_row = row(repository, project.id)
    expected_history = history.state(project.id)
    expected_receipt = history.receipt(project.id, "same")
    expected_files = files(repository, project)
    project = repository.get(project.id)
    before = history_basis(project)
    assert project.plan is not None
    project.plan.version += 1
    original_version, original_date = project.state_version, project.updated_at

    def no_geometry_staging(*args: object, **kwargs: object) -> None:
        raise AssertionError("Failed receipt must not stage geometry")

    assert repository._geometry_store is not None
    monkeypatch.setattr(
        repository._geometry_store, "_stage_collection", no_geometry_staging
    )
    try:
        with pytest.raises(sqlite3.IntegrityError):
            application.commit(
                project, before, "Duplicate", "same", receipt={"step": 2}
            )
        assert row(repository, project.id) == expected_row
        assert history.state(project.id) == expected_history
        assert history.receipt(project.id, "same") == expected_receipt
        assert files(repository, project) == expected_files
        assert (project.state_version, project.updated_at) == (
            original_version,
            original_date,
        )
    finally:
        repository.close()


def test_stale_plan_cannot_overwrite_new_source_or_history(tmp_path: Path) -> None:
    repository = SqliteProjectRepository(tmp_path / "stale.sqlite3")
    other = SqliteProjectRepository(repository.path)
    history = SqliteProjectHistory(repository)
    project = saved_project(repository)
    before = history_basis(project)
    changed = other.get(project.id)
    changed.geometry = graph("new source revision")
    changed.geometry_version += 1
    other.save(changed)
    expected_row = row(repository, project.id)
    expected_files = files(repository, project)
    assert project.plan is not None
    project.plan.version += 1
    try:
        with pytest.raises(ProjectVersionConflict):
            plan_application(repository, history).commit(
                project, before, "Stale", "stale", receipt={}
            )
        assert row(repository, project.id) == expected_row
        assert history.state(project.id).cursor_ordinal == 0
        assert history.receipt(project.id, "stale") is None
        assert files(repository, project) == expected_files
        assert project.state_version == 1
    finally:
        other.close()
        repository.close()


@pytest.mark.parametrize("fault", ["corrupt", "missing"])
def test_corrupted_retained_geometry_refuses_plan_commit_after_full_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    repository = SqliteProjectRepository(tmp_path / "corrupt.sqlite3")
    history = SqliteProjectHistory(repository)
    created = saved_project(repository)
    project = repository.get(created.id)
    before = history_basis(project)
    expected_row = row(repository, project.id)
    assert repository._geometry_store is not None
    path = next(repository._geometry_store.directory(project.id).glob("*.json.gz"))
    if fault == "corrupt":
        content = bytearray(path.read_bytes())
        content[-1] ^= 1
        path.write_bytes(content)
    else:
        path.unlink()

    def no_geometry_staging(*args: object, **kwargs: object) -> None:
        raise AssertionError("Corruption must not be repaired by rewriting CAD")

    monkeypatch.setattr(
        repository._geometry_store, "_stage_collection", no_geometry_staging
    )
    try:
        with pytest.raises((ValueError, FileNotFoundError)):
            plan_application(repository, history).commit(
                project, before, "Refuse corrupted CAD", "bad", receipt={}
            )
        assert row(repository, project.id) == expected_row
        assert history.state(project.id).cursor_ordinal == 0
        assert history.receipt(project.id, "bad") is None
    finally:
        repository.close()


def test_memory_plan_operations_keep_inline_geometry() -> None:
    repository = SqliteProjectRepository(":memory:")
    history = SqliteProjectHistory(repository)
    project = saved_project(repository)
    before = history_basis(project)
    assert project.plan is not None
    project.plan.objects[0].x = 11
    project.plan.version += 1
    try:
        plan_application(repository, history).commit(project, before, "Inline")
        restored = repository.get(project.id)
        assert restored.geometry == graph("mapped")
        assert restored.source_geometry == graph("source")
        history.undo(restored)
        restored = repository.get(project.id)
        assert restored.plan is not None and restored.plan.objects[0].x == 10
        assert restored.geometry == graph("mapped")
    finally:
        repository.close()
