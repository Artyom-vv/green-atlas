"""Компактные точные ключи и границы удержания больших окон."""

from math import nextafter
from unittest.mock import Mock

import pytest
from shapely import get_num_coordinates
from shapely.geometry import GeometryCollection, LineString, box

from app.native_query.search import constraint_cache
from app.native_query.search.constraint_cache import (
    BatchConstraints,
    ConstraintUnionCache,
)


def _batch(geometry=None):
    return BatchConstraints(
        geometry if geometry is not None else box(0, 0, 1, 1), [], [], {}, []
    )


def _key():
    return (
        "revision",
        "basis",
        (
            (("AA", "BB"), "face", True, False, False, True, 0.9),
            (("CC",), None, False, True, False, True, 5.0),
        ),
    )


@pytest.mark.parametrize(
    "change",
    [
        "revision",
        "basis",
        "route_order",
        "object_order",
        "face",
        "closure",
        "linear",
        "site",
        "confirmed",
        "distance_ulp",
    ],
)
def test_full_ordered_key_changes_invalidate_compact_lookup(change):
    original = _key()
    revision, basis, items = original
    item = list(items[0])
    if change == "revision":
        revision += "-changed"
    elif change == "basis":
        basis += "-changed"
    elif change == "object_order":
        items = tuple(reversed(items))
    elif change == "route_order":
        item[0] = tuple(reversed(item[0]))
    elif change == "face":
        item[1] = "other-face"
    elif change == "distance_ulp":
        item[6] = nextafter(item[6], float("inf"))
    else:
        index = {"closure": 2, "linear": 3, "site": 4, "confirmed": 5}[change]
        item[index] = not item[index]
    if change not in ("revision", "basis", "object_order"):
        items = (tuple(item), items[1])
    cache = ConstraintUnionCache()
    cache.remember(original, 2, _batch())
    assert cache.get((revision, basis, items), 2) is None
    assert cache.get(original, 2) is not None


def test_forced_hash_collision_never_restores_other_key_or_corrupts_counters(
    monkeypatch,
):
    monkeypatch.setattr(
        constraint_cache, "sha256", lambda _: Mock(digest=lambda: b"x" * 32)
    )
    cache = ConstraintUnionCache()
    first = _batch(box(0, 0, 1, 1))
    second = _batch(box(10, 10, 11, 11))
    cache.remember(("first",), 2, first)
    assert cache.get(("second",), 2) is None
    assert cache.get(("first",), 2).blocked.wkb == first.blocked.wkb
    cache.remember(("second",), 2, second)
    assert cache.get(("first",), 2) is None
    assert cache.get(("second",), 2).blocked.wkb == second.blocked.wkb
    assert len(cache.entries) == 1
    stored = next(iter(cache.entries.values()))
    assert cache.bytes == stored.weight
    assert cache.coordinates == stored.coordinates
    assert cache.issues == cache.routes == 0


def test_review_horizon_keeps_exact_float_separate_from_key_witness():
    value = _batch()
    value.issues.append(
        {
            "routes": ["AA"],
            "layer": "слой",
            "reason": "source_object",
            "detail": "xref_unavailable",
            "localized": False,
        }
    )
    cache = ConstraintUnionCache()
    cache.remember(_key(), 2.0, value)
    assert cache.get(_key(), nextafter(2.0, float("inf"))) is None
    assert cache.get(_key(), 2.0).issues == value.issues
    assert cache.get(_key(), 2.0).reviews == {}


def test_key_witness_is_included_in_unchanged_byte_budget(monkeypatch):
    monkeypatch.setattr(constraint_cache, "MAX_CACHED_CONSTRAINT_BYTES", 2048)
    cache = ConstraintUnionCache()
    cache.remember(("x" * 3000,), 2, _batch(GeometryCollection()))
    assert not cache.entries
    assert cache.bytes == cache.coordinates == cache.issues == cache.routes == 0


def _large_key(index):
    return (
        "revision",
        "basis",
        tuple(
            ((f"route-{index:03}-{i:03}",), None, False, True, False, True, 0.9)
            for i in range(256)
        ),
    )


def test_147_batches_keep_complete_large_window_in_bounded_wkb_cache():
    # 735 тыс. координат превышают прежний общий предел, отдельная порция мала.
    value = _batch(LineString((float(i), float(i % 2)) for i in range(5000)))
    expected = value.blocked.wkb
    cache = ConstraintUnionCache()
    for index in range(147):
        cache.remember(_large_key(index), 2, value)
    assert len(cache.entries) == 147
    assert cache.coordinates == 735_000
    assert cache.bytes <= constraint_cache.MAX_CACHED_CONSTRAINT_BYTES
    for key, stored in cache.entries.items():
        assert isinstance(key[0], bytes) and len(key[0]) == 32
        assert isinstance(stored.key_witness, bytes)
    for index in range(147):
        restored = cache.get(_large_key(index), 2)
        assert restored is not None and restored.blocked.wkb == expected
    assert len(cache.entries) == 147


def test_per_entry_coordinate_cap_remains_independent_from_total_cap(monkeypatch):
    monkeypatch.setattr(constraint_cache, "MAX_CACHED_CONSTRAINT_COORDINATES", 20)
    monkeypatch.setattr(constraint_cache, "MAX_CACHED_CONSTRAINT_TOTAL_COORDINATES", 50)
    cache = ConstraintUnionCache()
    small = _batch(LineString((i, i % 2) for i in range(19)))
    large = _batch(LineString((i, i % 2) for i in range(21)))
    cache.remember(("one",), 2, small)
    cache.remember(("two",), 2, small)
    cache.remember(("oversized",), 2, large)
    assert cache.get(("oversized",), 2) is None
    assert len(cache.entries) == 2 and cache.coordinates == 38
    cache.remember(("three",), 2, small)
    assert cache.get(("one",), 2) is None
    assert cache.get(("two",), 2) is not None
    assert cache.get(("three",), 2) is not None
    assert cache.coordinates == 2 * get_num_coordinates(small.blocked)
