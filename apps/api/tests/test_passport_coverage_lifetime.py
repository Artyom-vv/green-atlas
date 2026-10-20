"""Паспорт сохраняет адресное покрытие без удержания временного CAD-графа."""

import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import Mock

import pytest
from test_hybrid_search import populate
from test_native_live_provider import setup as setup

import app.native_query.project_runtime as runtime
from app.geometry.networks.utility_contracts import UtilityContext
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.progress import OperationCancelled, operation_cancellation


@pytest.fixture
def prepared(setup, tmp_path):
    live, client, project = setup
    populate(project)
    project.source_file.native_session = live.session
    snapshot = prepare_snapshot(live, project)
    # Адресная потеря должна пережить компактный кеш так же, как полный обход.
    records = list(snapshot.records)
    records[-1] = replace(
        records[-1],
        geometries=(),
        reason="projection_missing",
        detail="Не подготовлен отдельный объект",
    )
    snapshot = snapshot.model_copy(update={"records": tuple(records)})
    store = PreparedStore(tmp_path / "prepared")
    store.save(snapshot)
    expected = PreparedGeometryEngine(snapshot, project).source_coverage(project)
    client.calls.clear()
    return project, snapshot, PreparedStore(store.directory), expected, client, live


def observe_engines(monkeypatch):
    engines, snapshots = [], []

    def create(snapshot, project, **kwargs):
        result = PreparedGeometryEngine(snapshot, project, **kwargs)
        engines.append(weakref.ref(result))
        snapshots.append(weakref.ref(snapshot))
        return result

    monkeypatch.setattr(runtime, "PreparedGeometryEngine", create)
    return engines, snapshots


def observe_snapshot_load(monkeypatch, router):
    """Оба хранилища проходят общую границу допуска проверенного снимка."""
    load = Mock(wraps=router._load_snapshot)
    monkeypatch.setattr(router, "_load_snapshot", load)
    return load


def test_cold_passport_releases_snapshot_and_engine_but_reuses_exact_coverage(
    prepared, monkeypatch
):
    project, _, store, expected, client, _ = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    load = observe_snapshot_load(monkeypatch, router)
    engines, snapshots = observe_engines(monkeypatch)

    first = router.source_coverage(project)
    gc.collect()
    assert first == expected
    assert any(value.unresolved for value in first.layers.values())
    assert not router._prepared
    assert engines[0]() is None and snapshots[0]() is None
    first.layers.clear()
    second = router.source_coverage(project)
    assert second == expected
    second.layers.clear()
    assert router.metadata_source_coverage(project) == expected
    assert load.call_count == 1 and len(engines) == 1
    assert not client.calls


@pytest.mark.parametrize("same_project", [False, True])
def test_passport_preserves_existing_calculation_engine(
    prepared, monkeypatch, same_project
):
    project, _, store, expected, _, live = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    existing = router.load_local(project)
    read_project = project
    if not same_project:
        read_project = project.model_copy(update={"id": "another-project"})
        store.save(prepare_snapshot(live, read_project))
    load = observe_snapshot_load(monkeypatch, router)
    result = router.source_coverage(read_project)
    assert router._prepared == {project.id: existing}
    assert result is not None
    if same_project:
        assert result == expected
        load.assert_not_called()
    else:
        assert load.call_count == 1


@pytest.mark.parametrize("problem", ["corrupt", "missing", "roles"])
def test_cached_passport_does_not_hide_changed_prepared_input(prepared, problem):
    project, snapshot, store, _, _, _ = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    router.source_coverage(project)
    if problem == "corrupt":
        (store.directory / f"{snapshot.key}.json").write_bytes(b'{"broken":')
        expected = "corrupt"
    elif problem == "missing":
        (store.directory / f"{snapshot.key}.json").unlink()
        (store.directory / f"{store.reference_key(project.id)}.json").unlink()
        expected = "missing"
    else:
        project.layers[1].mapping_confirmed = False
        expected = "stale"
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.source_coverage(project)
    assert error.value.state == expected
    assert not router._prepared


def test_cached_passport_revalidates_changed_valid_bytes_and_calculation_basis(
    prepared, monkeypatch
):
    project, snapshot, store, expected, _, _ = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    load = observe_snapshot_load(monkeypatch, router)
    router.source_coverage(project)
    first_token = store.verified_fingerprint(snapshot.key)
    path = store.directory / f"{snapshot.key}.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.metadata_source_coverage(project)
    assert error.value.state == "stale" and load.call_count == 1
    assert router.source_coverage(project) == expected
    assert store.verified_fingerprint(snapshot.key) != first_token
    assert load.call_count == 2
    project.layers[1].utility_context = UtilityContext(network_type="water")
    assert router.metadata_source_coverage(project) is None
    assert router.source_coverage(project) == expected
    assert load.call_count == 3
    assert not router._prepared


def test_coverage_read_cancel_does_not_publish_a_summary_or_engine(
    prepared, monkeypatch
):
    project, _, store, _, _, _ = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    method = PreparedGeometryEngine.source_coverage
    complete = False

    def compute(engine, candidate):
        nonlocal complete
        result = method(engine, candidate)
        complete = True
        return result

    def cancel():
        if complete:
            raise OperationCancelled()

    monkeypatch.setattr(PreparedGeometryEngine, "source_coverage", compute)
    with operation_cancellation(cancel), pytest.raises(OperationCancelled):
        router.source_coverage(project)
    assert not router._prepared and not router._coverage_reads


def test_coverage_read_refuses_file_change_during_computation(prepared, monkeypatch):
    project, snapshot, store, _, _, _ = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    method = PreparedGeometryEngine.source_coverage

    def compute(engine, candidate):
        result = method(engine, candidate)
        path = store.directory / f"{snapshot.key}.json"
        path.write_bytes(path.read_bytes() + b" ")
        return result

    monkeypatch.setattr(PreparedGeometryEngine, "source_coverage", compute)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.source_coverage(project)
    assert error.value.state == "stale"
    assert not router._prepared and not router._coverage_reads


def test_concurrent_passport_reads_share_only_the_compact_summary(
    prepared, monkeypatch
):
    project, _, store, expected, _, _ = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    load = observe_snapshot_load(monkeypatch, router)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: router.source_coverage(project), range(2)))
    assert results == [expected, expected]
    assert results[0] is not results[1]
    assert load.call_count == 1 and not router._prepared
    router.discard(project.id)
    assert not router._coverage_reads


def test_summary_cache_is_bounded_and_eviction_keeps_prepared_files(
    prepared, monkeypatch
):
    project, snapshot, store, _, _, live = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    load = observe_snapshot_load(monkeypatch, router)
    router.source_coverage(project)
    first_path = store.directory / f"{snapshot.key}.json"
    original = first_path.read_bytes()
    others = []
    for index in range(runtime.MAX_CACHED_COVERAGE_PROJECTS):
        candidate = project.model_copy(update={"id": f"project-{index}"})
        store.save(prepare_snapshot(live, candidate))
        others.append(candidate)
        router.source_coverage(candidate)
    assert len(router._coverage_reads) == runtime.MAX_CACHED_COVERAGE_PROJECTS
    assert len(router._coverage_cache._verified) <= runtime.MAX_CACHED_COVERAGE_PROJECTS
    assert project.id not in router._coverage_reads
    assert not router._prepared and first_path.read_bytes() == original
    router.source_coverage(others[-1])
    assert load.call_count == runtime.MAX_CACHED_COVERAGE_PROJECTS + 1
    router.source_coverage(project)
    # Вытеснение памяти сохраняет производный кеш на диске.
    assert load.call_count == runtime.MAX_CACHED_COVERAGE_PROJECTS + 1


def test_changed_prepared_file_does_not_revalidate_the_old_cached_engine(
    prepared, monkeypatch
):
    project, snapshot, store, expected, _client, _live = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    previous = router.load_local(project)
    path = store.directory / f"{snapshot.key}.json"
    path.write_bytes(path.read_bytes() + b" ")
    # Старый движок не должен стать допустимым после проверки нового отпечатка.
    monkeypatch.setattr(
        previous,
        "source_coverage",
        Mock(side_effect=AssertionError("Устаревший движок не используется")),
    )
    assert router.source_coverage(project) == expected
    assert not router._prepared
    assert router.load_local(project) is not previous


@pytest.mark.parametrize("with_summary", [False, True])
def test_external_revalidation_does_not_revive_old_engine_for_passport(
    prepared, monkeypatch, with_summary
):
    project, snapshot, store, expected, _client, _live = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    previous = router.load_local(project)
    if with_summary:
        router.source_coverage(project)
    path = store.directory / f"{snapshot.key}.json"
    path.write_bytes(path.read_bytes() + b" ")
    store.load_project(project, project.source_file.native_session)
    assert store.verified_fingerprint(snapshot.key) != previous._storage_fingerprint
    monkeypatch.setattr(
        previous,
        "source_coverage",
        Mock(
            side_effect=AssertionError("Чужая проверка не подтверждает старый движок")
        ),
    )
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.metadata_source_coverage(project)
    assert error.value.state == "stale"
    assert router.source_coverage(project) == expected
    assert not router._prepared


def test_external_revalidation_does_not_revive_old_calculation_engine(prepared):
    project, snapshot, store, expected, _client, _live = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    previous = router.load_local(project)
    path = store.directory / f"{snapshot.key}.json"
    path.write_bytes(path.read_bytes() + b" ")
    store.load_project(project, project.source_file.native_session)
    current = router.load_local(project)
    assert current is not previous
    assert current.source_coverage(project) == expected
    assert current._storage_fingerprint == store.verified_fingerprint(snapshot.key)


@pytest.mark.parametrize("with_summary", [False, True])
def test_failed_external_read_keeps_corrupt_copy_visible(prepared, with_summary):
    project, snapshot, store, _expected, _client, _live = prepared
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    router.load_local(project)
    if with_summary:
        router.source_coverage(project)
    (store.directory / f"{snapshot.key}.json").write_bytes(b'{"broken":')
    with pytest.raises(PreparedGeometryUnavailable) as external:
        store.load_project(project, project.source_file.native_session)
    assert external.value.state == "corrupt"
    with pytest.raises(PreparedGeometryUnavailable) as metadata:
        router.metadata_source_coverage(project)
    assert metadata.value.state == "corrupt"
    with pytest.raises(PreparedGeometryUnavailable) as full:
        router.source_coverage(project)
    assert full.value.state == "corrupt"
    assert not router._prepared and not router._coverage_reads
