"""Владение исходным графом при подготовке CAD и неизменность сохраняемых объектов."""

import weakref
from functools import partial
from threading import RLock
from unittest.mock import Mock

import pytest
from test_hybrid_search import populate
from test_native_live_provider import StubNative
from test_native_live_provider import setup as setup

from app.dxf_import.contracts import ImportMode, ImportStatus
from app.dxf_import.layers.contracts import LayerKind
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.preparation.prepared_snapshot import (
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.adapters import SqliteOperationRepository
from app.operations.capture_preparation import (
    CapturePreparationApplication,
    OwnedCapturePreparation,
)
from app.operations.contracts import OperationStatus
from app.operations.lifecycle import OperationLifecycle
from app.operations.progress import OperationCancelled, operation_cancellation
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.shared.identity import random_id, utc_now


def source_input(setup, *, source_geometry: bool = False):
    live, _, full = setup
    populate(full)
    full.import_status = ImportStatus(mode=ImportMode.AUTOCAD_LIVE)
    live.session = live.session.model_copy(update={"plugin_version": "0.1.48"})
    full.source_file.native_session = live.session
    if source_geometry:
        full.source_geometry = full.geometry.model_copy(deep=True)
    return (
        live,
        full,
        full.model_copy(update={"geometry": None, "source_geometry": None}),
    )


class TrackedFeature(dict):
    """Проверять жизнь самих GeoJSON-записей, а не только оболочки снимка."""


@pytest.mark.parametrize("source_geometry", [False, True])
@pytest.mark.parametrize("reference_layer", [False, True])
def test_owned_source_is_released_before_checkpoint_and_preserves_every_record(
    setup, tmp_path, monkeypatch, source_geometry, reference_layer
):
    live, full, metadata = source_input(setup, source_geometry=source_geometry)
    if reference_layer:
        full.layers[2].mapped_kind = LayerKind.IGNORE
    persisted_json = full.model_dump_json()
    expected = prepare_snapshot(
        live, ProjectLiveGeometry._capture_candidate(full)
    ).model_dump(mode="json")
    store = PreparedStore(tmp_path / "prepared")
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    source_refs, display_refs, feature_refs, live_refs = [], [], [], []

    def load_source(project_id):
        assert project_id == metadata.id
        project = Project.model_validate_json(persisted_json)
        source_refs.append(weakref.ref(project))
        for graph in (project.geometry, project.source_geometry):
            if graph is not None:
                display_refs.append(weakref.ref(graph))
                tracked = [
                    TrackedFeature(feature)
                    for feature in graph.feature_collection["features"]
                ]
                graph.feature_collection["features"] = tracked
                feature_refs.extend(weakref.ref(feature) for feature in tracked)
        return project

    def live_engine(project):
        assert project.geometry is not None
        engine = LiveNativeGeometryEngine(
            live.session, StubNative(), inventory=live.inventory
        )
        live_refs.append(weakref.ref(engine))
        router._engines[project.id] = ("owned-test", engine)
        return engine

    save = store.save

    def checkpoint(snapshot):
        assert all(reference() is None for reference in source_refs)
        assert all(reference() is None for reference in display_refs)
        assert feature_refs and all(reference() is None for reference in feature_refs)
        assert all(reference() is None for reference in live_refs)
        assert snapshot.model_dump(mode="json") == expected
        assert [record.item.routes for record in snapshot.records] == [
            tuple(record["item"]["routes"]) for record in expected["records"]
        ]
        save(snapshot)

    monkeypatch.setattr(router, "live_engine", live_engine)
    monkeypatch.setattr(store, "save", checkpoint)
    router.prepare_capture_from_repository(load_source, metadata)
    assert metadata.geometry is metadata.source_geometry is None
    assert full.model_dump_json() == persisted_json
    assert router.load_local(metadata).snapshot.model_dump(mode="json") == expected
    # Чтение готового основания не гидратирует исходный граф повторно.
    router.prepare_capture_from_repository(
        Mock(side_effect=AssertionError("Нельзя читать полный исходник повторно")),
        metadata,
    )


def test_regular_capture_keeps_callers_geometry(setup, tmp_path, monkeypatch):
    live, full, _ = source_input(setup, source_geometry=True)
    original_geometry, original_source = full.geometry, full.source_geometry
    store = PreparedStore(tmp_path / "prepared")
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    monkeypatch.setattr(router, "live_engine", lambda _: live)
    router.prepare_capture(full)
    assert full.geometry is original_geometry
    assert full.source_geometry is original_source
    assert router.load_local(full).snapshot.records


def test_owned_source_rejects_full_external_project_before_reading(setup, tmp_path):
    _, full, _ = source_input(setup)
    router = ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(tmp_path))
    loader = Mock(side_effect=AssertionError("Чтение не должно начаться"))
    with pytest.raises(ValueError, match="только метаданные"):
        router.prepare_capture_from_repository(loader, full)
    loader.assert_not_called()


@pytest.mark.parametrize("changed", ["version", "source", "session"])
def test_owned_source_checks_revision_and_capture_before_native_preparation(
    setup, tmp_path, monkeypatch, changed
):
    _, full, metadata = source_input(setup)
    if changed == "version":
        full.state_version += 1
    elif changed == "source":
        full.source_file.content_sha256 = "d" * 64
    else:
        full.source_file.native_session = full.source_file.native_session.model_copy(
            update={"snapshot_sha256": "d" * 64}
        )
    router = ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(tmp_path))
    native = Mock(side_effect=AssertionError("Core не должен запускаться"))
    monkeypatch.setattr(router, "live_engine", native)
    with pytest.raises(ProjectVersionConflict if changed == "version" else ValueError):
        router.prepare_capture_from_repository(lambda _: full, metadata)
    native.assert_not_called()
    assert not list(tmp_path.glob("*.json"))


def test_owned_source_checks_cancellation_after_hydration(setup, tmp_path, monkeypatch):
    _, full, metadata = source_input(setup)
    loaded = False

    def load_source(_):
        nonlocal loaded
        loaded = True
        return full

    def cancelled():
        if loaded:
            raise OperationCancelled()

    router = ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(tmp_path))
    native = Mock(side_effect=AssertionError("Core не должен запускаться"))
    monkeypatch.setattr(router, "live_engine", native)
    with operation_cancellation(cancelled), pytest.raises(OperationCancelled):
        router.prepare_capture_from_repository(load_source, metadata)
    native.assert_not_called()
    assert not list(tmp_path.glob("*.json"))


def test_capture_operation_owns_only_metadata_and_keeps_stored_source(
    setup, tmp_path, monkeypatch
):
    live, full, _ = source_input(setup, source_geometry=True)
    repository = SqliteProjectRepository(":memory:")
    operations = SqliteOperationRepository(":memory:")
    try:
        repository.create(full)
        original_get = repository.get
        before = original_get(full.id).model_dump(mode="json")
        lifecycle = OperationLifecycle(
            repository, operations, RLock(), utc_now, random_id
        )
        store = PreparedStore(tmp_path / "prepared")
        router = ProjectLiveGeometry(Mock(), prepared_store=store)
        discard_display = Mock()
        refs, reads = [], []

        def metadata_read(project_id, *, lightweight=False):
            assert lightweight, "Операция не должна владеть полным исходником"
            return original_get(project_id, lightweight=True)

        def load_source(project_id):
            discard_display.assert_called_once_with(project_id)
            project = original_get(project_id)
            reads.append(project_id)
            refs.extend(
                [weakref.ref(project.geometry), weakref.ref(project.source_geometry)]
            )
            return project

        def live_engine(project):
            assert project.geometry is not None
            engine = LiveNativeGeometryEngine(
                live.session, StubNative(), inventory=live.inventory
            )
            router._engines[project.id] = ("operation-test", engine)
            return engine

        save = store.save

        def checkpoint(snapshot):
            assert refs and all(reference() is None for reference in refs)
            assert len(snapshot.records) == len(live.inventory.objects)
            save(snapshot)

        monkeypatch.setattr(repository, "get", metadata_read)
        monkeypatch.setattr(router, "live_engine", live_engine)
        monkeypatch.setattr(store, "save", checkpoint)
        application = CapturePreparationApplication(
            repository,
            lifecycle,
            OwnedCapturePreparation(
                partial(router.prepare_capture_from_repository, load_source)
            ),
            discard_display_projection=discard_display,
        )
        operation = application.start(full.id)
        application.run(operation.id)
        assert operations.get(operation.id).status == OperationStatus.COMPLETED
        assert reads == [full.id]
        saved = original_get(full.id).model_dump(mode="json")
        assert saved["source_file"]["preparation_recipe"] is not None
        assert saved["state_version"] == before["state_version"] + 1
        for value in (before, saved):
            value["source_file"].pop("preparation_recipe")
            value.pop("state_version")
            value.pop("updated_at")
        assert saved == before
    finally:
        operations.close()
        repository.close()


@pytest.mark.parametrize("stop", ["cancel", "conflict"])
def test_owned_capture_metadata_does_not_publish_recipe_after_stop(
    setup, tmp_path, stop
):
    _, full, _ = source_input(setup, source_geometry=True)
    repository = SqliteProjectRepository(":memory:")
    operations = SqliteOperationRepository(":memory:")
    try:
        repository.create(full)
        before_geometry = repository.get(full.id).geometry.model_dump(mode="json")
        lifecycle = OperationLifecycle(
            repository, operations, RLock(), utc_now, random_id
        )

        def prepare(metadata, _):
            assert metadata.geometry is metadata.source_geometry is None
            metadata.source_file = ProjectLiveGeometry._capture_candidate(
                metadata
            ).source_file
            if stop == "cancel":
                lifecycle.cancel(full.id, operation.id)
            else:
                current = repository.get(full.id, lightweight=True)
                repository.save_preparation_recipe(current)

        application = CapturePreparationApplication(
            repository, lifecycle, OwnedCapturePreparation(prepare)
        )
        operation = application.start(full.id)
        application.run(operation.id)
        result = operations.get(operation.id)
        assert result.status == (
            OperationStatus.CANCELLED if stop == "cancel" else OperationStatus.FAILED
        )
        if stop == "conflict":
            assert result.error.code == "PROJECT_VERSION_CONFLICT"
        stored = repository.get(full.id)
        assert stored.source_file.preparation_recipe is None
        assert stored.geometry.model_dump(mode="json") == before_geometry
    finally:
        operations.close()
        repository.close()
