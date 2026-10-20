"""Границы расчётного чтения и консервативная сводка сетевых объяснений поиска."""

from threading import Event, Thread, current_thread
from unittest.mock import Mock

import pytest
from test_native_live_provider import setup as setup
from test_preparation_identity import network

from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.networks.utility_contracts import UtilityContext
from app.geometry.preparation import calculation_scope
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.prepared_snapshot import (
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry


@pytest.mark.parametrize("state", ["known", "unknown", "unconfirmed"])
@pytest.mark.parametrize("kind", ["tree", "shrub"])
def test_native_summary_is_an_upper_bound_and_never_a_spatial_observation(
    setup, tmp_path, state, kind
):
    live, _, project = network(setup)
    if state == "unknown":
        project.layers[2].utility_context = UtilityContext()
    elif state == "unconfirmed":
        project.layers[2].mapping_confirmed = False
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path / "prepared")
    store.save(snapshot)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    baseline = PreparedGeometryEngine(snapshot, project)
    with calculation_scope(router, project):
        codes = router.search_trace_assumption_codes(project, frozenset({kind}))
        assert codes == baseline.search_trace_assumption_codes(
            project, frozenset({kind})
        )
        assert codes <= {"GEOMETRY_RULE_ASSUMPTION"}
        if state == "unconfirmed":
            assert not codes
        if state == "unknown":
            assert codes
        for x in (-90, 20, 50):
            trace = engine.position_rule_trace(project, x, 0, kind)
            actual_codes = {
                entry.code
                for entry in trace.entries
                if entry.obstacle_kind == "utility" and entry.status == "not_checked"
            }
            assert actual_codes <= codes
            assert trace == baseline.position_rule_trace(project, x, 0, kind)
        # Далёкая неизвестная сеть остаётся лишь возможной причиной, не замечанием позиции.
        remote = engine.position_rule_trace(project, -90, 0, kind)
        assert not any(entry.obstacle_kind == "utility" for entry in remote.entries)


def test_legacy_provider_keeps_optional_scope_and_summary_unsupported(setup):
    _, _, project = setup
    legacy = ShapelyGeometryEngine()
    router = ProjectLiveGeometry(legacy)
    with calculation_scope(legacy, project), calculation_scope(router, project):
        assert (
            router.search_trace_assumption_codes(project, frozenset({"tree"})) is None
        )


def test_search_scope_and_parallel_project_read_do_not_invert_locks(
    setup, tmp_path, monkeypatch
):
    live, _, project = network(setup)
    store = PreparedStore(tmp_path / "prepared")
    store.save(prepare_snapshot(live, project))
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    search_entered, map_inside_router, search_needs_router = Event(), Event(), Event()
    actual_assert = engine.assert_current
    errors = []

    def assert_current(value):
        if current_thread().name == "parallel-map":
            # load_local удерживает блокировку роутера перед этой проверкой.
            map_inside_router.set()
            assert search_needs_router.wait(2)
        actual_assert(value)

    monkeypatch.setattr(engine, "assert_current", assert_current)

    def search():
        try:
            with router.calculation_scope(project):
                search_entered.set()
                assert map_inside_router.wait(2)
                search_needs_router.set()
                router.prepare_positions(project, [(50, 0)])
        except BaseException as error:
            errors.append(error)

    def map_read():
        try:
            assert search_entered.wait(2)
            assert router.load_local(project) is engine
        except BaseException as error:
            errors.append(error)

    threads = (
        Thread(target=search, name="planting-search", daemon=True),
        Thread(target=map_read, name="parallel-map", daemon=True),
    )
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3)
    assert not any(thread.is_alive() for thread in threads), (
        "Взаимная блокировка поиска и карты"
    )
    assert not errors
