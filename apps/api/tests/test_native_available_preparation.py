"""Явная подготовка актуального архива после изменения назначений слоёв."""

from unittest.mock import Mock

import pytest
from test_native_live_provider import setup as native_provider_setup
from test_operations import NativeOperationSetup, native_available_setup

from app.dxf_import.layers.contracts import LayerKind
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    preparation_key,
)
from app.operations.contracts import OperationKind, OperationStatus
from app.operations.progress import OperationCancelled
from app.planting_zones.domain import attach_planting_zone_features
from app.projects.concurrency import (
    reset_expected_project_version,
    set_expected_project_version,
)
from app.shared.failures import DomainFailure, FailureReason

native_setup = native_provider_setup
available_setup = native_available_setup


@pytest.fixture
def archived_setup(available_setup, native_setup, monkeypatch):
    app, repository, operations, router, store, compatibility, _, project = (
        available_setup
    )
    live, client, _ = native_setup
    live.session = live.session.model_copy(update={"plugin_version": "0.1.48"})
    project.source_file.native_session = live.session
    repository.save(project)
    archive = Mock(return_value=live)
    monkeypatch.setattr(router, "live_engine", archive)
    return (
        NativeOperationSetup(
            app, repository, operations, router, store, compatibility, archive, project
        ),
        client,
    )


def changed_roles(setup):
    project = setup.repository.get(setup.project.id)
    project.layers[2].mapped_kind = LayerKind.WATER
    setup.repository.save(project)
    assert setup.router.prepared_state(project) == "stale"
    return project


def corrupt_snapshot(setup, key, state):
    path = setup.store.directory / f"{key}.json"
    if state == "corrupt":
        path.write_text("{}")
    else:
        setup.store.files.save(key, {"schema_version": "future/9"})
    with pytest.raises(PreparedGeometryUnavailable) as error:
        setup.store.load(key)
    assert error.value.state == state
    return path


@pytest.mark.parametrize("state", ["stale", "missing"])
def test_explicit_operation_prepares_archive_and_opens_same_request(
    archived_setup, state
):
    setup, _ = archived_setup
    project = changed_roles(setup) if state == "stale" else setup.project
    if state == "missing":
        reference = setup.store.files.load(setup.store.reference_key(project.id))
        (setup.store.directory / f"{reference['geometry_key']}.json").unlink()
        (
            setup.store.directory / f"{setup.store.reference_key(project.id)}.json"
        ).unlink()
        assert setup.router.prepared_state(project) == "missing"
    before = project.model_copy(deep=True)
    operation = setup.application.start_geometry_operation(project.id)
    setup.application.run_geometry_operation(operation.id)

    result = setup.operations.get(operation.id)
    assert result.status == OperationStatus.COMPLETED, result.error
    restored = setup.repository.get(project.id)
    assert restored.map_ready and restored.source_review is None
    assert restored.geometry.calculation_scope == "available_data"
    assert restored.layers == before.layers
    assert restored.layers[1].mapping_confirmed is False
    assert restored.source_geometry == before.source_geometry
    assert restored.source_file.content_sha256 == before.source_file.content_sha256
    assert restored.source_file.native_session == before.source_file.native_session
    assert restored.source_file.preparation_recipe is not None
    assert setup.router.prepared_state(restored) == "available"
    assert restored.state_version == before.state_version + 1
    assert restored.geometry_version == before.geometry_version + 1
    assert setup.cad.call_count == 1
    assert (
        setup.operations.get_latest(project.id, OperationKind.PREPARE_CAD_CAPTURE)
        is None
    )
    assert not setup.compatibility.mock_calls

    # Повторное явное вычисление переиспользует тот же проверенный снимок.
    again = setup.router.prepare_available_data(restored)
    recalculated = restored.model_copy(update={"geometry": again})
    attach_planting_zone_features(recalculated)
    assert recalculated.geometry == restored.geometry
    assert setup.cad.call_count == 1


def test_ready_snapshot_never_opens_archive(archived_setup):
    setup, _ = archived_setup
    operation = setup.application.start_geometry_operation(setup.project.id)
    setup.application.run_geometry_operation(operation.id)
    assert setup.operations.get(operation.id).status == OperationStatus.COMPLETED
    setup.cad.assert_not_called()


def test_local_calculation_still_rejects_stale_without_preparation(archived_setup):
    setup, _ = archived_setup
    project = changed_roles(setup)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        setup.router.calculate_available_data(project)
    assert error.value.state == "stale"
    setup.cad.assert_not_called()


@pytest.mark.parametrize("state", ["corrupt", "unsupported"])
def test_invalid_snapshot_is_not_silently_replaced(archived_setup, state):
    setup, _ = archived_setup
    reference = setup.store.files.load(setup.store.reference_key(setup.project.id))
    corrupt_snapshot(setup, reference["geometry_key"], state)
    before = setup.repository.get(setup.project.id).model_dump_json()
    operation = setup.application.start_geometry_operation(setup.project.id)
    setup.application.run_geometry_operation(operation.id)
    assert setup.operations.get(operation.id).status == OperationStatus.FAILED
    assert setup.repository.get(setup.project.id).model_dump_json() == before
    setup.cad.assert_not_called()


@pytest.mark.parametrize("state", ["corrupt", "unsupported"])
def test_invalid_snapshot_for_new_recipe_is_not_rebuilt(archived_setup, state):
    setup, _ = archived_setup
    project = changed_roles(setup)
    candidate = setup.router._capture_candidate(project)
    key = preparation_key(candidate, candidate.source_file.native_session)
    assert key != preparation_key(project, project.source_file.native_session)
    path = corrupt_snapshot(setup, key, state)
    payload = path.read_text()
    before = project.model_dump_json()
    operation = setup.application.start_geometry_operation(project.id)
    setup.application.run_geometry_operation(operation.id)
    assert setup.operations.get(operation.id).status == OperationStatus.FAILED
    assert setup.repository.get(project.id).model_dump_json() == before
    assert path.read_text() == payload
    setup.cad.assert_not_called()


def test_cancellation_after_preparation_does_not_publish_recipe(
    archived_setup, monkeypatch
):
    setup, _ = archived_setup
    project = changed_roles(setup)
    before = project.model_dump_json()
    original = setup.router.calculate_available_data
    operation = setup.application.start_geometry_operation(project.id)

    def cancel_after_preparation(current, report=None):
        result = original(current, report)
        setup.application.cancel_operation(current.id, operation.id)
        return result

    monkeypatch.setattr(
        setup.router, "calculate_available_data", cancel_after_preparation
    )
    setup.application.run_geometry_operation(operation.id)
    assert setup.operations.get(operation.id).status == OperationStatus.CANCELLED
    assert setup.repository.get(project.id).model_dump_json() == before
    assert setup.cad.call_count == 1


@pytest.mark.parametrize("failure", ["cancel", "integrity", "cas"])
def test_preparation_failure_does_not_publish_project(archived_setup, failure):
    setup, client = archived_setup
    project = changed_roles(setup)
    before = project.model_dump_json()
    live = setup.cad.return_value

    def archive(current):
        if failure == "cancel":
            raise OperationCancelled()
        if failure == "integrity":
            client.stale = True
        if failure == "cas":
            token = set_expected_project_version(None)
            try:
                updated = setup.repository.get(current.id)
                updated.name = "Внешнее изменение"
                setup.repository.save(updated)
            finally:
                reset_expected_project_version(token)
        return live

    setup.cad.side_effect = archive
    operation = setup.application.start_geometry_operation(project.id)
    setup.application.run_geometry_operation(operation.id)
    result = setup.operations.get(operation.id)
    restored = setup.repository.get(project.id)
    assert not restored.map_ready and restored.geometry is None
    assert restored.source_review == project.source_review
    assert restored.source_file == project.source_file
    assert restored.layers == project.layers
    if failure == "cancel":
        assert result.status == OperationStatus.CANCELLED
    else:
        assert result.status == OperationStatus.FAILED
    if failure == "cas":
        assert result.error.code == "PROJECT_VERSION_CONFLICT"
        assert restored.name == "Внешнее изменение"
    else:
        assert restored.model_dump_json() == before


@pytest.mark.parametrize("confirmed", [False, None])
def test_boundary_gate_precedes_archive_work(archived_setup, confirmed):
    setup, _ = archived_setup
    project = changed_roles(setup)
    project.layers[0].mapping_confirmed = confirmed
    with pytest.raises(DomainFailure) as error:
        setup.router.prepare_available_data(project)
    assert error.value.reason is FailureReason.MAPPING_CONFIRMATION_REQUIRED
    setup.cad.assert_not_called()
