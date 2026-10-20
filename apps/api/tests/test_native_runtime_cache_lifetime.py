"""Временные каталоги CAD и готовые основания освобождаются между проектами."""

import gc
import weakref
from unittest.mock import Mock

import pytest
from test_hybrid_search import populate
from test_native_live_provider import setup as setup

from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.preparation.prepared_snapshot import (
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.progress import OperationCancelled


def test_switching_prepared_projects_releases_previous_engine_but_keeps_geometry(
    setup, tmp_path
):
    live, _, project = setup
    populate(project)
    project.source_file.native_session = live.session
    store = PreparedStore(tmp_path / "prepared")
    store.save(prepare_snapshot(live, project))
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    first = router.load_local(project)
    before = [record.geometries for record in first.snapshot.records]
    first_reference = weakref.ref(first)
    del first
    next_project = project.model_copy(update={"id": "next-project"})
    store.save(prepare_snapshot(live, next_project))
    second = router.load_local(next_project)
    gc.collect()

    assert first_reference() is None
    assert [record.geometries for record in second.snapshot.records] == before
    assert second.position_violation(next_project, 5, 0, 0.5, "shrub").code == "NATIVE_OCCUPIED"
    assert second.position_violation(next_project, 35, 0, 0.5, "shrub") is None
    assert router.load_local(next_project) is second
    assert second.projection.get(second.snapshot.records[0].item).geometries
    second_reference = weakref.ref(second)
    del second
    router.discard(next_project.id)
    gc.collect()
    assert second_reference() is None


@pytest.mark.parametrize("failure", [ValueError("Ошибка подготовки"), OperationCancelled()])
def test_failed_preparation_releases_capture_catalog_without_saving_snapshot(
    setup, monkeypatch, failure, tmp_path
):
    import app.native_query.project_runtime as runtime

    source, client, project = setup
    live = LiveNativeGeometryEngine(source.session, client, inventory=source.inventory)
    populate(project)
    project.source_file.native_session = live.session
    live_reference = weakref.ref(live)
    router = ProjectLiveGeometry(Mock(), prepared_store=Mock(directory=tmp_path))
    router._engines[project.id] = ("capture", live)
    monkeypatch.setattr(router, "load_local", Mock(side_effect=runtime.PreparedGeometryUnavailable("missing")))
    monkeypatch.setattr(router, "live_engine", lambda _: router._engines[project.id][1])

    def fail(*args):
        raise failure.with_traceback(None)

    monkeypatch.setattr(runtime, "prepare_snapshot", fail)
    del live
    with pytest.raises(type(failure)):
        router.prepare_current_capture(project)
    # Исключение в тесте не должно само удерживать вызывавший его frame и каталог.
    failure.__traceback__ = None
    gc.collect()
    assert live_reference() is None
    router.prepared_store.save.assert_not_called()


def test_live_engine_cache_releases_previous_inventory_when_switching_projects(
    setup, monkeypatch
):
    import app.native_query.project_runtime as runtime

    source, client, project = setup
    inventory = source.inventory
    project.source_file.native_session = source.session
    monkeypatch.setattr(runtime, "LiveQueryClient", lambda **_: client)
    monkeypatch.setattr(runtime, "LiveNativeGeometryEngine", lambda session, client, **_: LiveNativeGeometryEngine(session, client, inventory=inventory))
    router = ProjectLiveGeometry(Mock())
    first = router.live_engine(project)
    reference = weakref.ref(first)
    del first
    next_project = project.model_copy(update={"id": "second-project"})
    second = router.live_engine(next_project)
    gc.collect()
    assert reference() is None
    assert second.inventory == inventory
    assert router.live_engine(next_project) is second


@pytest.mark.parametrize("prepared", [False, True])
def test_measurement_row_budget_keeps_complete_batch_and_uncached_evidence(
    setup, monkeypatch, prepared
):
    from test_prepared_geometry import local

    from app.native_query.policy import placement_policy

    engine, _, project = local(setup) if prepared else setup
    points = [(5 + index / 10, 0) for index in range(16)]
    expected = engine._point_measurements(project, points)
    evidence = engine.explain_position(project, 5, 0, 0.5, "shrub")
    assert len(expected) == len(points)
    assert all(len(value[1]) > 1 for value in expected.values())
    engine._cache.clear()
    monkeypatch.setattr(placement_policy, "MAX_CACHED_MEASUREMENT_ROWS", 3)
    actual = engine._point_measurements(project, points)
    assert actual == expected
    assert sum(len(value[1]) for value in engine._cache.values()) <= 3
    # Даже целая позиция, превышающая кеш, проверяется по полному временному ответу.
    engine._cache.clear()
    monkeypatch.setattr(placement_policy, "MAX_CACHED_MEASUREMENT_ROWS", 1)
    assert engine.explain_position(project, 5, 0, 0.5, "shrub") == evidence
    assert engine.position_violation(project, 5, 0, 0.5, "shrub").code == "NATIVE_OCCUPIED"
    assert not engine._cache


def test_cell_domain_reads_complete_ephemeral_batch_when_measurement_cache_is_small(
    setup, monkeypatch
):
    from shapely.geometry import shape
    from test_native_live_provider import rectangle

    from app.native_query.policy import placement_policy
    from app.native_query.search.search_domain import build_domain

    engine, _, project = setup
    work = rectangle(11, -2, 18, 2)
    expected = build_domain(engine, project, work, 0.5, "shrub", 0, 0)
    engine._cache.clear()
    monkeypatch.setattr(placement_policy, "MAX_CACHED_MEASUREMENT_ROWS", 1)
    actual = build_domain(engine, project, work, 0.5, "shrub", 0, 0)
    for field in ("geometry", "unresolved_geometry", "pending_geometry"):
        assert shape(actual["ga_search_domain"][field]).wkb == shape(expected["ga_search_domain"][field]).wkb
    assert sum(len(value[1]) for value in engine._cache.values()) <= 1
