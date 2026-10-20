"""Скалярный отбор после вытеснения упрощённых координат."""

import gc
import weakref
from unittest.mock import Mock

import pytest
from shapely.geometry import box
from test_viewport_compact_features import feature, project

from app.geometry.viewport.cancellation import (
    ViewportRequestCancelled,
    viewport_cancellation,
)
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.geometry.viewport.viewport_simplification import ViewportSimplifier


@pytest.mark.parametrize("byte_budget", [2, 4000])
def test_warm_selection_does_not_rebuild_rejected_geometry(monkeypatch, byte_budget):
    current = project([feature(i) for i in range(24)])
    original = current.model_dump_json()
    query = IndexedGeometryQuery(
        max_features=12,
        max_cached_features=1,
        max_cached_simplified_bytes=1200,
        max_feature_bytes=byte_budget,
    )
    expected = query.query(current, (-1, -1, 12, 12), 3)
    index = query._indexes[current.id]
    index.simplified_geometries.clear()
    index.simplified_sizes.clear()
    index.simplified_bytes = 0
    decode = Mock(wraps=index.features.feature)
    simplify = Mock(wraps=index.simplifier.simplify)
    monkeypatch.setattr(index.features, "feature", decode)
    monkeypatch.setattr(index.simplifier, "simplify", simplify)
    actual = query.query(current, (-1, -1, 12, 12), 3)
    assert actual.model_dump_json() == expected.model_dump_json()
    returned = len(actual.feature_collection["features"])
    assert decode.call_count == returned
    assert simplify.call_count == returned
    assert index.simplified_bytes <= 1200
    assert len(index.display_sizes) <= 24
    assert current.model_dump_json() == original


def test_recovered_selected_geometry_survives_rejected_candidates(monkeypatch):
    current = project([feature(i) for i in range(24)])
    query = IndexedGeometryQuery(
        max_features=12,
        max_cached_features=1,
        max_cached_simplified_bytes=4000,
        max_feature_bytes=4000,
    )
    expected = query.query(current, (-1, -1, 12, 12), 3)
    index = query._indexes[current.id]
    assert len(index.simplified_geometries) < 24
    index.simplified_geometries.clear()
    index.simplified_sizes.clear()
    index.simplified_bytes = 0
    recovered = query.query(current, (-1, -1, 12, 12), 3)
    assert recovered.model_dump_json() == expected.model_dump_json()
    assert len(recovered.feature_collection["features"]) > 0
    simplify = Mock(wraps=index.simplifier.simplify)
    monkeypatch.setattr(index.simplifier, "simplify", simplify)
    repeated = query.query(current, (-1, -1, 12, 12), 3)
    assert repeated.model_dump_json() == expected.model_dump_json()
    assert simplify.call_count == 0
    assert index.simplified_bytes <= 4000
    assert len(index.display_sizes) == 24


@pytest.mark.parametrize("resolution", [0.1, 3])
@pytest.mark.parametrize("release", ["discard", "eviction", "cancel"])
def test_finished_query_releases_index_without_cyclic_gc(resolution, release):
    current = project([feature(i) for i in range(24)])
    query = IndexedGeometryQuery(
        max_features=12,
        max_cached_projects=1,
        max_cached_features=1,
        max_cached_simplified_bytes=4000,
        max_feature_bytes=4000,
    )
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        # Холодный запрос, восстановление выбранного набора и полностью тёплый ответ.
        for _ in range(3):
            response = query.query(current, (-1, -1, 12, 12), resolution)
        previous_index = weakref.ref(query._indexes[current.id])
        previous_geometry = weakref.ref(query._indexes[current.id].geometries[0])
        if release == "cancel":
            checks = 0

            def cancel_materialization():
                nonlocal checks
                checks += 1
                if checks == 100:
                    raise ViewportRequestCancelled

            try:
                with viewport_cancellation(cancel_materialization):
                    query.query(current, (-1, -1, 12, 12), resolution)
            except ViewportRequestCancelled:
                pass
            else:
                pytest.fail("Отмена не достигла этапа материализации")
            query.discard(current.id)
        elif release == "discard":
            query.discard(current.id)
        else:
            next_project = current.model_copy(update={"id": "next"})
            query.query(next_project, (-1, -1, 12, 12), resolution)
        # Даже ещё используемый ответ не удерживает пространственный индекс.
        assert response.feature_collection["features"]
        assert previous_index() is None
        assert previous_geometry() is None
    finally:
        if was_enabled:
            gc.enable()


@pytest.mark.parametrize("failure", ["fallback", "exception"])
def test_changed_materialization_repeats_admission_with_actual_budget(
    monkeypatch, failure
):
    current = project([feature(i) for i in range(24)])
    options = dict(
        max_features=12,
        max_cached_features=1,
        max_cached_simplified_bytes=1200,
        max_feature_bytes=4000,
        max_coordinates=40,
    )
    monkeypatch.setattr(
        ViewportSimplifier,
        "simplify",
        lambda *args, **kwargs: (box(0, 0, 1, 1), None),
    )
    query = IndexedGeometryQuery(**options)
    first = query.query(current, (-1, -1, 12, 12), 3)
    index = query._indexes[current.id]
    index.simplified_geometries.clear()
    index.simplified_sizes.clear()
    index.simplified_bytes = 0

    def failed(self, index, geometry, tolerance, **options):
        if failure == "exception":
            raise ValueError("Transient GEOS failure")
        return geometry, "coverage_simplification_failed"

    probe = Mock(side_effect=failed)
    monkeypatch.setattr(
        ViewportSimplifier, "simplify", lambda *a, **kw: probe(*a, **kw)
    )
    actual = query.query(current, (-1, -1, 12, 12), 3)
    assert (
        probe.call_count <= 25
    )  # Один неверный прогноз и один проход с полной подготовкой.
    expected = IndexedGeometryQuery(**options).query(current, (-1, -1, 12, 12), 3)
    assert actual.model_dump_json() == expected.model_dump_json()
    metadata = actual.feature_collection["metadata"]
    assert metadata["returned_bytes"] <= 4000
    assert metadata["returned_coordinates"] <= 40
    assert len(actual.feature_collection["features"]) < len(
        first.feature_collection["features"]
    )
    if failure == "fallback":
        assert metadata["simplification_fallbacks"] == {
            "coverage_simplification_failed": 24
        }
    assert index.simplified_bytes <= 1200
