"""Native phase diagnostics preserve geometry, progress and failure semantics."""

from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from test_binary_prepared_engine import saved
from test_hybrid_search import domain, populate
from test_native_live_provider import setup as setup

from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.prepared_storage import SqlitePreparedResource
from app.native_query.project_runtime import ProjectLiveGeometry
from app.native_query.search import hybrid_domain


def recorded_stages(monkeypatch):
    events = []
    clock = [0.0]

    @contextmanager
    def record(name, **metadata):
        started = clock[0]
        value = {"stage": name, "metadata": metadata}
        try:
            yield
        except BaseException:
            value["failed"] = True
            raise
        finally:
            value["elapsed"] = clock[0] - started
            events.append(value)

    monkeypatch.setattr(hybrid_domain, "performance_stage", record)
    monkeypatch.setattr(hybrid_domain, "monotonic", lambda: 100.0)
    return events, clock


def test_hybrid_stage_recording_preserves_exact_result_without_extra_cad_queries(
    setup, monkeypatch
):
    engine, client, project = setup
    populate(project)
    # Fixed algorithm clock removes only the expected elapsed field variation.
    monkeypatch.setattr(hybrid_domain, "monotonic", lambda: 100.0)
    expected = domain(engine, project)
    before = project.model_dump_json()
    engine._hybrid = None
    events, clock = recorded_stages(monkeypatch)
    advance = hybrid_domain.HybridSearch._advance_batch

    def measured_batch(*args, **kwargs):
        clock[0] += 2.5
        return advance(*args, **kwargs)

    monkeypatch.setattr(hybrid_domain.HybridSearch, "_advance_batch", measured_batch)
    actual = domain(engine, project)
    assert actual == expected
    assert project.model_dump_json() == before
    assert client.calls == []
    stages = {event["stage"]: event for event in events}
    assert stages["domain.processing"]["elapsed"] == 2.5
    assert stages["domain.finalize"]["metadata"] == {
        "processed": 3,
        "total": 3,
        "cache_hits": 0,
    }
    assert {
        "domain.items.select",
        "domain.batch.reusable_lookup",
        "domain.batch.masks",
        "domain.batch.union",
        "domain.batch.cache_store",
        "domain.batch.clip",
        "domain.checkpoint.load",
    } <= set(stages)
    assert all(
        key in {"processed", "total", "cache_hits", "objects"}
        and type(value) in {int, float}
        for event in events
        for key, value in event["metadata"].items()
    )


def test_hybrid_phase_failure_is_propagated_and_does_not_publish_cached_domain(
    setup, monkeypatch
):
    engine, _, project = setup
    populate(project)
    before = project.model_dump_json()
    events, _ = recorded_stages(monkeypatch)
    failure = RuntimeError("Synthetic geometry failure")
    monkeypatch.setattr(
        hybrid_domain.HybridSearch, "_advance_batch", Mock(side_effect=failure)
    )
    with pytest.raises(RuntimeError) as caught:
        domain(engine, project)
    assert caught.value is failure
    assert engine._hybrid is None
    assert project.model_dump_json() == before
    assert any(
        event["stage"] == "domain.processing" and event.get("failed")
        for event in events
    )
    assert not any(event["stage"] == "domain.finalize" for event in events)


def test_checkpoint_failure_is_measured_without_publishing_success(setup, monkeypatch):
    engine, _, project = setup
    populate(project)
    events, _ = recorded_stages(monkeypatch)
    failure = OSError("Synthetic checkpoint failure")
    checkpoints = Mock()
    checkpoints.load.return_value = None
    checkpoints.save.side_effect = failure
    engine._domain_checkpoints = checkpoints
    with pytest.raises(OSError) as caught:
        domain(engine, project)
    assert caught.value is failure
    assert engine._hybrid is None
    assert checkpoints.load.call_count == checkpoints.save.call_count == 1
    assert any(
        event["stage"] == "domain.checkpoint.save_result"
        and event.get("failed")
        and event["metadata"]["processed"] == 3
        for event in events
    )


def test_native_load_stages_do_not_materialize_or_scan_all_records(
    setup, tmp_path, monkeypatch
):
    project, snapshot, store, client = saved(setup, tmp_path)
    expected = PreparedGeometryEngine(snapshot, project).position_violation(
        project, 5, 0, 0.5, "shrub"
    )
    forbidden = Mock(
        side_effect=AssertionError("Instrumentation cannot hydrate full CAD")
    )
    monkeypatch.setattr(SqlitePreparedResource, "materialize", forbidden)
    monkeypatch.setattr(SqlitePreparedResource, "iter_records", forbidden)
    monkeypatch.setattr(store, "load_project", forbidden)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    assert isinstance(engine.snapshot, SqlitePreparedResource)
    assert router.load_local(project) is engine
    assert engine.position_violation(project, 5, 0, 0.5, "shrub") == expected
    forbidden.assert_not_called()
    assert client.calls == []
