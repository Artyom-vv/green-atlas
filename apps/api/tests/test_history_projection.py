"""Prepared history commands retain checked CAD without hydrating the map."""

import json
from threading import RLock
from unittest.mock import Mock

import pytest
from test_native_point_check_projection import prepared_runtime as prepared_runtime
from test_native_point_check_projection import setup as setup
from test_plan_geometry_retention import files, graph, row, saved_project

from app.history.adapters import SqliteProjectHistory
from app.history.application import PlanHistoryApplication, history_basis
from app.planning.contracts import PlanObject
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.geometry_storage import GEOMETRY_STORAGE_KEY
from app.validation.contracts import PlanValidationBasis


def application(repository, history, *, supports_projection=lambda _: True):
    return PlanHistoryApplication(
        repository,
        history,
        RLock(),
        Mock(),
        supports_projection=supports_projection,
    )


def commit_edit(repository, history):
    project = saved_project(repository)
    before = history_basis(project)
    project.plan.objects.append(
        PlanObject(id="third", kind="tree", x=30, y=30, radius=1)
    )
    project.plan.version += 1
    saved = application(repository, history).commit(
        project, before, "Add", "edit", receipt={"added_ids": ["third"]}
    )
    return before, saved


def forbid_full_read(repository, monkeypatch):
    original_get = repository.get

    def metadata_only(project_id, *, lightweight=False):
        assert lightweight, "History must not call full repository.get"
        return original_get(project_id, lightweight=True)

    monkeypatch.setattr(repository, "get", metadata_only)
    monkeypatch.setattr(
        repository,
        "_hydrate_geometry_row",
        Mock(side_effect=AssertionError("History cannot hydrate full CAD")),
    )
    monkeypatch.setattr(
        repository._geometry_store,
        "_stage_collection",
        Mock(side_effect=AssertionError("History cannot serialize CAD")),
    )


def test_actual_undo_redo_retains_revisions_receipts_refs_and_restart(
    tmp_path, monkeypatch
):
    repository = SqliteProjectRepository(tmp_path / "history.sqlite3")
    history = SqliteProjectHistory(repository)
    before, saved = commit_edit(repository, history)
    expected_files = files(repository, saved)
    expected_refs = json.loads(row(repository, saved.id)[0])[GEOMETRY_STORAGE_KEY]
    commands = application(repository, history)
    with monkeypatch.context() as patch:
        forbid_full_read(repository, patch)
        undone = commands.undo_plan_change(saved.id)
        assert undone.plan.objects == before.plan.objects
        assert undone.plan.version == saved.plan.version + 1
        assert undone.state_version == saved.state_version + 1
        assert history.receipt(saved.id, "edit")["status"] == "undone"
        redone = commands.redo_plan_change(saved.id)
        assert redone.plan.objects == saved.plan.objects
        assert redone.plan.version == undone.plan.version + 1
        assert redone.state_version == undone.state_version + 1
        assert history.receipt(saved.id, "edit")["status"] == "applied"
        assert history.state(saved.id).can_undo
        assert not history.state(saved.id).can_redo
        assert files(repository, saved) == expected_files
        assert (
            json.loads(row(repository, saved.id)[0])[GEOMETRY_STORAGE_KEY]
            == expected_refs
        )
    repository.close()
    reopened = SqliteProjectRepository(tmp_path / "history.sqlite3")
    try:
        restored = reopened.get(saved.id)
        assert restored.plan == redone.plan
        assert restored.geometry == graph("mapped")
        assert restored.source_geometry == graph("source")
        assert files(reopened, saved) == expected_files
    finally:
        reopened.close()


def test_native_runtime_wires_projection_and_preserves_prepared_validation_basis(
    prepared_runtime, monkeypatch
):
    runtime, router, _, created, _, _ = prepared_runtime
    repository = runtime.project_repository
    history = runtime.application.history
    history.set_basis_reader(router.validation_input_basis)
    project = repository.get(created.id)
    basis = router.validation_input_basis(project)
    project.plan.validation_basis = PlanValidationBasis(
        plan_version=project.plan.version,
        geometry_version=project.geometry_version,
        validator_revision="prepared-test",
        objects_digest="a" * 64,
        **basis.model_dump(),
    )
    project = repository.save(project)
    before = history_basis(project)
    project.plan.objects.append(
        PlanObject(id="new", kind="tree", x=60, y=50, radius=0.5)
    )
    project.plan.version += 1
    project.plan.validation_basis = project.plan.validation_basis.model_copy(
        update={"plan_version": project.plan.version, "objects_digest": "b" * 64}
    )
    saved = runtime.application.history_application.commit(
        project, before, "Prepared change", "prepared"
    )
    assert runtime.application.history_application.supports_projection(project)
    forbid_full_read(repository, monkeypatch)
    for operation, expected in (
        (runtime.application.undo_plan_change, before),
        (runtime.application.redo_plan_change, saved),
    ):
        router.discard(project.id)
        restored = operation(project.id)
        assert restored.plan.objects == expected.plan.objects
        assert (
            restored.plan.validation_basis
            == expected.plan.validation_basis.model_copy(
                update={"plan_version": restored.plan.version}
            )
        )
    expected_row = row(repository, project.id)
    expected_history = history.state(project.id)

    def unavailable(_):
        raise RuntimeError("Prepared basis unavailable")

    history.set_basis_reader(unavailable)
    with pytest.raises(RuntimeError, match="Prepared basis unavailable"):
        runtime.application.undo_plan_change(project.id)
    assert row(repository, project.id) == expected_row
    assert history.state(project.id) == expected_history


@pytest.mark.parametrize("operation", ["undo_plan_change", "redo_plan_change"])
def test_conflict_is_checked_before_history_rebase(tmp_path, monkeypatch, operation):
    repository = SqliteProjectRepository(tmp_path / "conflict.sqlite3")
    history = SqliteProjectHistory(repository)
    _, saved = commit_edit(repository, history)
    commands = application(repository, history)
    if operation == "redo_plan_change":
        commands.undo_plan_change(saved.id)
        commands.invalidate_spacing.reset_mock()
    expected_row = row(repository, saved.id)
    expected_history = history.state(saved.id)
    expected_receipt = history.receipt(saved.id, "edit")
    rebase = Mock(wraps=history.rebase_planting_zones)
    monkeypatch.setattr(history, "rebase_planting_zones", rebase)
    token = set_expected_project_version("1")
    try:
        with pytest.raises(ProjectVersionConflict):
            getattr(commands, operation)(saved.id)
        rebase.assert_not_called()
        assert row(repository, saved.id) == expected_row
        assert history.state(saved.id) == expected_history
        assert history.receipt(saved.id, "edit") == expected_receipt
        commands.invalidate_spacing.assert_not_called()
    finally:
        reset_expected_project_version(token)
        repository.close()


@pytest.mark.parametrize("fault", ["corrupt", "missing"])
def test_projection_keeps_integrity_failure_before_history_rebase(
    tmp_path, monkeypatch, fault
):
    repository = SqliteProjectRepository(tmp_path / "integrity.sqlite3")
    history = SqliteProjectHistory(repository)
    _, saved = commit_edit(repository, history)
    expected_row = row(repository, saved.id)
    expected_history = history.state(saved.id)
    expected_receipt = history.receipt(saved.id, "edit")
    path = next(repository._geometry_store.directory(saved.id).glob("*.json.gz"))
    if fault == "missing":
        path.unlink()
    else:
        content = bytearray(path.read_bytes())
        content[-1] ^= 1
        path.write_bytes(content)
    rebase = Mock(wraps=history.rebase_planting_zones)
    monkeypatch.setattr(history, "rebase_planting_zones", rebase)
    commands = application(repository, history)
    forbid_full_read(repository, monkeypatch)
    try:
        with pytest.raises((ValueError, FileNotFoundError)):
            commands.undo_plan_change(saved.id)
        rebase.assert_not_called()
        assert row(repository, saved.id) == expected_row
        assert history.state(saved.id) == expected_history
        assert history.receipt(saved.id, "edit") == expected_receipt
        commands.invalidate_spacing.assert_not_called()
    finally:
        repository.close()


def test_revision_race_during_projection_does_not_rebase_or_restore(
    tmp_path, monkeypatch
):
    repository = SqliteProjectRepository(tmp_path / "race.sqlite3")
    other = SqliteProjectRepository(repository.path)
    history = SqliteProjectHistory(repository)
    _, saved = commit_edit(repository, history)
    original_projection = repository.get_placement_projection

    def race(project_id, *, expected_version):
        latest = other.get(project_id)
        latest.name = "Concurrent metadata"
        other.save(latest)
        return original_projection(project_id, expected_version=expected_version)

    monkeypatch.setattr(repository, "get_placement_projection", race)
    rebase = Mock(wraps=history.rebase_planting_zones)
    monkeypatch.setattr(history, "rebase_planting_zones", rebase)
    expected_history = history.state(saved.id)
    expected_receipt = history.receipt(saved.id, "edit")
    commands = application(repository, history)
    try:
        with pytest.raises(ProjectVersionConflict):
            commands.undo_plan_change(saved.id)
        rebase.assert_not_called()
        assert repository.get(saved.id).name == "Concurrent metadata"
        assert repository.get(saved.id).plan == saved.plan
        assert history.state(saved.id) == expected_history
        assert history.receipt(saved.id, "edit") == expected_receipt
        commands.invalidate_spacing.assert_not_called()
    finally:
        other.close()
        repository.close()


@pytest.mark.parametrize("layout", ["inline", "mixed", "unsupported"])
def test_legacy_and_unsupported_provider_keep_full_geometry(
    tmp_path, monkeypatch, layout
):
    repository = SqliteProjectRepository(
        ":memory:" if layout == "inline" else tmp_path / "compatibility.sqlite3"
    )
    history = SqliteProjectHistory(repository)
    before, saved = commit_edit(repository, history)
    if layout == "mixed":
        payload = json.loads(row(repository, saved.id)[0])
        payload[GEOMETRY_STORAGE_KEY].pop("source_geometry")
        payload["source_geometry"] = graph("source").model_dump(mode="json")
        with repository._connection:
            repository._connection.execute(
                "UPDATE projects SET payload=? WHERE id=?",
                (json.dumps(payload), saved.id),
            )
    get = Mock(wraps=repository.get)
    monkeypatch.setattr(repository, "get", get)
    commands = application(
        repository, history, supports_projection=lambda _: layout != "unsupported"
    )
    try:
        restored = commands.undo_plan_change(saved.id)
        assert restored.plan.objects == before.plan.objects
        assert restored.geometry == graph("mapped")
        assert restored.source_geometry == graph("source")
        assert any(
            not call.kwargs.get("lightweight", False) for call in get.call_args_list
        )
        assert repository.get(saved.id).geometry == graph("mapped")
        assert repository.get(saved.id).source_geometry == graph("source")
    finally:
        repository.close()


@pytest.mark.parametrize("operation", ["undo_plan_change", "redo_plan_change"])
def test_no_history_keeps_project_and_reports_original_error(
    tmp_path, monkeypatch, operation
):
    repository = SqliteProjectRepository(tmp_path / "empty.sqlite3")
    history = SqliteProjectHistory(repository)
    project = saved_project(repository)
    expected_row = row(repository, project.id)
    commands = application(repository, history)
    forbid_full_read(repository, monkeypatch)
    try:
        with pytest.raises(ValueError, match="Нет изменений"):
            getattr(commands, operation)(project.id)
        assert row(repository, project.id) == expected_row
        assert history.state(project.id).cursor_ordinal == 0
        commands.invalidate_spacing.assert_not_called()
    finally:
        repository.close()
