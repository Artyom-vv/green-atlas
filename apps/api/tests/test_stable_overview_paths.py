"""SVG strings keyed by immutable ordinals: exact output and bounded ownership."""

import gc
import weakref
from array import array
from unittest.mock import Mock

import pytest
from shapely import from_wkb, to_wkb
from shapely.geometry import GeometryCollection, LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

import app.geometry.viewport.source_overview as module
from app.operations.progress import OperationCancelled


def ids(*values):
    return array("q", values).tobytes()


def metadata(index=0, *, layer="Слой", kind="building"):
    return {"id": index, "properties": {"source_layer": layer, "kind": kind}}


def lazy_cache(**options):
    return module.SourceOverviewCache(cache_paths=False, stable_paths=True, **options)


@pytest.mark.parametrize(
    "geometry,shared",
    [
        (LineString([(1.2345678, -9.876543), (10, 20)]), True),
        (Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), True),
        (Polygon([(0, 0), (10, 0), (5, 10)]), True),
        (Point(2, 3), False),
        (LineString([(0, 0), (1, 0.03), (2, 0)]), False),
        (
            Polygon(
                [(0, 0), (10, 0), (10, 10), (0, 10)],
                holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]],
            ),
            False,
        ),
        (Polygon([(0, 0), (5, 0.1), (10, 0), (10, 10), (0, 10)]), False),
        (GeometryCollection([Point(1, 1), LineString([(0, 0), (1, 1)])]), False),
    ],
)
def test_exact_paths_across_resolutions_decode_only_required_geometry(geometry, shared):
    extent = (-1, -11, 21, 21)
    rows = [(7, metadata(7))]
    resolutions = [0.25, 0.225, 2.5]
    expected = [
        module.source_overview([(rows[0][1], geometry)], extent, scale)
        for scale in resolutions
    ]
    encoded = to_wkb(geometry)
    load = Mock(side_effect=lambda index: from_wkb(encoded))
    cache = lazy_cache(max_entries=0)
    actual = [
        cache.render_lazy(rows, load, extent, scale, ids(7)) for scale in resolutions
    ]
    assert actual == expected
    assert load.call_count == (1 if shared else 3)
    assert cache.stable_path_stats["hits"] == (2 if shared else 0)
    assert cache.stable_path_stats["misses"] == (1 if shared else 3)
    assert (
        cache.stable_path_stats["accounted_bytes"]
        <= cache.stable_path_stats["max_bytes"]
    )


def test_overlapping_pan_uses_stable_ordinal_and_preserves_exact_svg_order():
    shapes = {index: LineString([(index, 0), (index, 10)]) for index in range(4)}
    rows = {index: metadata(index, layer="Сети", kind="utility") for index in shapes}
    load = Mock(side_effect=lambda index: from_wkb(to_wkb(shapes[index])))
    cache = lazy_cache()
    cache.render_lazy(
        [(0, rows[0]), (1, rows[1])], load, (-1, -1, 2, 11), 0.2, ids(0, 1)
    )
    actual = cache.render_lazy(
        [(1, rows[1]), (2, rows[2])], load, (0, -1, 3, 11), 0.2, ids(1, 2)
    )
    expected = module.source_overview(
        [(rows[1], shapes[1]), (rows[2], shapes[2])], (0, -1, 3, 11), 0.2
    )
    assert actual == expected
    assert [call.args[0] for call in load.call_args_list] == [0, 1, 2]
    assert cache.stable_path_stats["hits"] == 1
    assert cache.stable_path_stats["evicted"] == 1


def test_complete_drawing_hit_never_consumes_metadata_or_geometry():
    shape = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]]
    )
    feature = metadata()
    cache = lazy_cache()
    cache.render_lazy(
        [(0, feature)], lambda index: shape, (-1, -1, 11, 11), 0.2, ids(0)
    )

    def forbidden_rows():
        raise AssertionError("A complete drawing hit must not reread metadata")
        yield

    load = Mock(
        side_effect=AssertionError("A complete drawing hit must not decode WKB")
    )
    result = cache.render_lazy(forbidden_rows(), load, (-2, -2, 12, 12), 0.2, ids(0))
    assert result == module.source_overview([(feature, shape)], (-2, -2, 12, 12), 0.2)
    assert load.call_count == 0


def test_stable_entries_hold_only_primitive_values_no_geos_loader_or_metadata():
    borrowed = []

    class Loader:
        def __call__(self, index):
            geometry = LineString([(index, 0), (index, 10)])
            borrowed.append(weakref.ref(geometry))
            return geometry

    class Feature(dict):
        pass

    loader = Loader()
    feature = Feature(metadata())
    loader_ref, feature_ref = weakref.ref(loader), weakref.ref(feature)
    cache = lazy_cache()
    cache.render_lazy([(0, feature)], loader, (-1, -1, 11, 11), 0.2, ids(0))
    del loader, feature
    gc.collect()
    assert loader_ref() is None and feature_ref() is None
    assert borrowed and all(reference() is None for reference in borrowed)
    assert cache._paths is None

    def check(value):
        assert not isinstance(value, BaseGeometry)
        assert not callable(value)
        if isinstance(value, dict):
            for key, item in value.items():
                check(key)
                check(item)
        elif isinstance(value, tuple):
            for item in value:
                check(item)
        else:
            assert isinstance(value, (str, int, float, type(None)))

    check(cache._stable_paths.entries)


def test_oversized_scan_does_not_flush_existing_viewport_working_set():
    cache = module.StableSourcePathCache(max_entries=2)
    load = Mock(side_effect=lambda index: LineString([(index, 0), (index, 10)]))
    cache.prepare(ids(0, 1), 0.2)
    for index in [0, 1]:
        cache.paths(index, 0.2, load)
    prior_entries, prior_bytes = dict(cache.entries), cache.bytes
    cache.prepare(ids(0, 1, 2, 3, 4), 0.2)
    for index in range(5):
        cache.paths(index, 0.2, load)
    assert cache.entries == prior_entries and cache.bytes == prior_bytes
    assert cache.stats["oversized_requests"] == 1
    assert cache.stats["hits"] == 2
    assert cache.stats["rejected"] == 3
    cache.prepare(ids(3, 4), 0.2)
    for index in [3, 4]:
        cache.paths(index, 0.2, load)
    assert {key[1] for key in cache.entries} == {3, 4}
    assert len(cache.entries) <= 2


def test_byte_budget_scan_keeps_protected_entries_instead_of_thrashing():
    shape = LineString([(0, 0), (0, 10)])
    cost = module.StableSourcePathCache._size(
        (None, 0), tuple(module._display_paths(shape, 0.2))
    )
    cache = module.StableSourcePathCache(max_bytes=cost * 2, max_entries=10)
    load = Mock(side_effect=lambda index: from_wkb(to_wkb(shape)))
    cache.prepare(ids(0, 1, 2, 3, 4), 0.2)
    for index in range(5):
        cache.paths(index, 0.2, load)
        assert cache.bytes <= cache.max_bytes
    assert len(cache.entries) == 2
    prior = dict(cache.entries)
    cache.prepare(ids(0, 1, 2, 3, 4), 0.2)
    load.reset_mock()
    for index in range(5):
        cache.paths(index, 0.2, load)
        assert cache.bytes <= cache.max_bytes
    assert load.call_count == 3
    assert cache.entries == prior
    assert cache.bytes == sum(entry.size for entry in cache.entries.values())


@pytest.mark.parametrize("budget", [0, 1])
def test_tiny_byte_budget_never_retains_a_path(budget):
    cache = lazy_cache(max_entries=0, max_stable_path_bytes=budget)
    shape = LineString([(0, 0), (10, 10)])
    feature, extent = metadata(), (-1, -1, 11, 11)
    expected = module.source_overview([(feature, shape)], extent, 0.2)
    load = Mock(return_value=shape)
    for _ in range(2):
        assert cache.render_lazy([(0, feature)], load, extent, 0.2, ids(0)) == expected
    assert load.call_count == 2
    assert cache.stable_path_stats["entries"] == 0
    assert cache.stable_path_stats["accounted_bytes"] == 0


def test_path_hit_keeps_current_style_and_holes_without_feature_ownership():
    shape = Polygon(
        [(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]]
    )
    cache, extent = lazy_cache(max_entries=0), (-1, -1, 11, 11)
    load = Mock(return_value=shape)
    first, second = metadata(kind="building"), metadata(layer="Вода", kind="water")
    cache.render_lazy([(0, first)], load, extent, 0.2, ids(0))
    result = cache.render_lazy([(0, second)], load, extent, 0.2, ids(0))
    assert result == module.source_overview([(second, shape)], extent, 0.2)
    assert load.call_count == 1
    assert result["svg"].count("Z") == 2


def test_svg_budget_refusal_matches_uncached_and_is_reused(monkeypatch):
    monkeypatch.setattr(module, "MAX_SVG_BYTES", 100)
    feature = metadata()
    shape = LineString([(0, 0), (10, 10)])
    cache = lazy_cache()
    load = Mock(return_value=shape)
    first = cache.render_lazy([(0, feature)], load, (-1, -1, 11, 11), 0.2, ids(0))
    second = cache.render_lazy([(0, feature)], load, (-2, -2, 12, 12), 0.2, ids(0))
    assert first["svg"] is None and first["error"] == "overview_byte_budget"
    assert second == module.source_overview([(feature, shape)], (-2, -2, 12, 12), 0.2)
    assert load.call_count == 1
    assert next(iter(cache._drawings.values()))["body"] == ""


def test_empty_path_failure_is_cached_without_turning_it_into_complete(monkeypatch):
    monkeypatch.setattr(module, "_display_paths", lambda *args, **kwargs: [])
    feature, shape = metadata(), Point(1, 1)
    cache, extent = lazy_cache(max_entries=0), (-1, -1, 11, 11)
    load = Mock(return_value=shape)
    expected = module.source_overview([(feature, shape)], extent, 0.2)
    for _ in range(2):
        assert cache.render_lazy([(0, feature)], load, extent, 0.2, ids(0)) == expected
    assert expected["complete"] is False
    assert expected["error"] == "unsupported_display_type"
    assert load.call_count == 1


def test_records_without_source_layer_do_not_decode_geometry():
    cache = lazy_cache()
    load = Mock(side_effect=AssertionError("No source layer needs no SVG geometry"))
    actual = cache.render_lazy(
        [(0, {"properties": {}})], load, (0, 0, 10, 10), 0.2, ids(0)
    )
    assert actual == module.source_overview([], (0, 0, 10, 10), 0.2)
    assert load.call_count == 0


@pytest.mark.parametrize(
    "failure", [OperationCancelled(), ValueError("corrupted geometry")]
)
def test_failed_generation_clears_partial_paths_and_drawing(failure):
    cache = lazy_cache()

    def load(index):
        if index == 1:
            raise failure
        return LineString([(index, 0), (index, 10)])

    with pytest.raises(type(failure)):
        cache.render_lazy(
            [(0, metadata(0)), (1, metadata(1))], load, (0, 0, 10, 10), 0.2, ids(0, 1)
        )
    assert not cache._drawings and cache._bytes == 0
    assert cache.stable_path_stats["entries"] == 0
    assert cache.stable_path_stats["accounted_bytes"] == 0


def test_new_index_cache_never_reuses_same_ordinal_from_old_source():
    first, changed = LineString([(0, 0), (10, 10)]), LineString([(0, 0), (20, 10)])
    feature, extent = metadata(), (-1, -1, 21, 11)
    old = lazy_cache()
    before = old.render_lazy([(0, feature)], lambda index: first, extent, 0.2, ids(0))
    new = lazy_cache()
    after = new.render_lazy([(0, feature)], lambda index: changed, extent, 0.2, ids(0))
    assert before != after
    assert after == module.source_overview([(feature, changed)], extent, 0.2)


@pytest.mark.parametrize("kwargs", [{"max_bytes": -1}, {"max_entries": -1}])
def test_invalid_cache_bounds_fail(kwargs):
    with pytest.raises(ValueError):
        module.StableSourcePathCache(**kwargs)


def test_invalid_ordinal_bytes_fail_without_retaining_partial_data():
    cache = lazy_cache()
    with pytest.raises(ValueError, match="int64"):
        cache.render_lazy([], Mock(), (0, 0, 10, 10), 0.2, b"bad")
    assert cache.stable_path_stats["accounted_bytes"] == 0
