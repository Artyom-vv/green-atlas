"""Компактный отбор читает метаданные один раз и сохраняет прежнее ранжирование."""

import random
import weakref
from array import array
from copy import deepcopy
from unittest.mock import Mock

import pytest

from app.geometry.viewport.cancellation import (
    ViewportRequestCancelled,
    viewport_cancellation,
)
from app.geometry.viewport.persistent_index import (
    PersistentSelectionCache,
    _CachedSelection,
)
from app.geometry.viewport.viewport_selection import (
    SelectionWork,
    feature_group,
    select_compact_fair_candidates,
    select_fair_candidates,
)


def signature(selection):
    return (
        [(index, feature_group(feature)) for index, feature in selection.candidates],
        selection.total_by_kind,
        selection.total_matches,
    )


def corpus(seed=0, count=180):
    randomizer = random.Random(seed)
    kinds = [
        "road",
        "building",
        "allowed",
        "utility",
        "forbidden",
        "source",
        "ignore",
        "",
        None,
        False,
        0,
        {"network": 7},
    ]
    layers = ["Слой A", "Слой B", "", None, 0, False, ["native", 1]]
    identities = [
        None,
        False,
        True,
        0,
        1,
        1.5,
        -0.0,
        2**100,
        "",
        "A",
        "0",
        {"route": [False, 8]},
        [None, "source"],
    ]
    rows = []
    for ordinal in range(count):
        properties = {
            "kind": randomizer.choice(kinds),
            "source_layer": randomizer.choice(layers),
            "unused": {"large": "X" * 1000},
        }
        if ordinal % 9 == 0:
            properties.pop("kind")
        if ordinal % 11 == 0:
            properties.pop("source_layer")
        if ordinal % 7:
            properties["source_handle"] = randomizer.choice(identities)
        feature = {"properties": properties}
        if ordinal % 5:
            feature["id"] = randomizer.choice(identities)
        rows.append((ordinal * 3 + 5, feature))
    randomizer.shuffle(rows)
    return rows


@pytest.mark.parametrize("seed", range(12))
@pytest.mark.parametrize("limit", [0, 1, 8, 25, 80, 300])
@pytest.mark.parametrize("budget", [0, 4096, 16 * 1024 * 1024])
def test_compact_selection_preserves_fair_quotas_typed_identity_and_order(
    seed, limit, budget
):
    rows = corpus(seed)
    before = deepcopy(rows)
    expected = select_fair_candidates(lambda: iter(rows), limit)
    work = SelectionWork()
    calls = 0

    def candidates():
        nonlocal calls
        calls += 1
        yield from rows

    actual = select_compact_fair_candidates(
        candidates, limit, max_spool_bytes=budget, work=work
    )
    assert signature(actual) == signature(expected)
    assert rows == before
    assert calls == work.metadata_passes == (1 if budget >= 16 * 1024 * 1024 else 2)
    assert work.fallback_reads == (0 if calls == 1 else 1)
    assert work.spool_peak_bytes <= budget
    assert all(
        set(feature) == {"properties"}
        and set(feature["properties"]) == {"kind", "source_layer"}
        for _, feature in actual.candidates
    )


@pytest.mark.parametrize("properties", [None, False, [], "invalid"])
def test_malformed_properties_keep_previous_exception(properties):
    rows = [(0, {"id": "bad", "properties": properties})]
    with pytest.raises(AttributeError):
        select_fair_candidates(lambda: iter(rows), 3)
    with pytest.raises(AttributeError):
        select_compact_fair_candidates(lambda: iter(rows), 3)


@pytest.mark.parametrize("rows", [[], [(2, {})]])
def test_empty_and_missing_metadata_match_previous_selection(rows):
    expected = select_fair_candidates(lambda: iter(rows), 12)
    actual = select_compact_fair_candidates(lambda: iter(rows), 12)
    assert signature(actual) == signature(expected)


def test_exact_window_hit_never_reads_metadata_and_restores_fresh_group_dicts():
    rows = corpus()
    cache = PersistentSelectionCache()
    key = array("q", [index for index, _ in rows]).tobytes()
    expected = select_fair_candidates(lambda: iter(rows), 24)
    cold = cache.select(lambda: iter(rows), 24, key, show_forbidden=True)
    assert signature(cold) == signature(expected)
    never = Mock(side_effect=AssertionError("metadata was read on a window hit"))
    hit = cache.select(never, 24, key, show_forbidden=True)
    assert signature(hit) == signature(expected)
    hit.candidates[0][1]["properties"]["kind"] = "mutated"
    assert signature(cache.select(never, 24, key, show_forbidden=True)) == signature(
        expected
    )
    assert cache.hits == 2 and cache.misses == cache.metadata_passes == 1
    assert cache.entry_count == 1 and cache.retained_bytes <= cache.max_bytes


def test_cache_key_distinguishes_matched_set_representation_and_candidate_limit():
    rows = corpus()
    cache = PersistentSelectionCache()
    matched = array("q", [index for index, _ in rows]).tobytes()
    visible = [row for row in rows if feature_group(row[1])[0] != "forbidden"]
    for selected_rows, limit, match, show in [
        (rows, 24, matched, True),
        (visible, 24, matched, False),
        (rows, 12, matched, True),
        (rows[:-1], 24, matched[:-8], True),
    ]:
        actual = cache.select(
            lambda: iter(selected_rows), limit, match, show_forbidden=show
        )
        assert signature(actual) == signature(
            select_fair_candidates(lambda: iter(selected_rows), limit)
        )
    assert cache.misses == cache.entry_count == 4


def test_scope_replacement_and_clear_do_not_reuse_old_roles_or_resource():
    old_rows = [
        (0, {"id": "same", "properties": {"kind": "road", "source_layer": "native"}})
    ]
    new_rows = [
        (0, {"id": "same", "properties": {"kind": "utility", "source_layer": "native"}})
    ]
    key = array("q", [0]).tobytes()
    old, new = PersistentSelectionCache(), PersistentSelectionCache()
    old.select(lambda: iter(old_rows), 8, key, show_forbidden=True)
    expected = select_fair_candidates(lambda: iter(new_rows), 8)
    assert signature(
        new.select(lambda: iter(new_rows), 8, key, show_forbidden=True)
    ) == signature(expected)
    old.clear()
    assert old.entry_count == old.retained_bytes == 0
    assert signature(
        old.select(lambda: iter(new_rows), 8, key, show_forbidden=True)
    ) == signature(expected)


@pytest.mark.parametrize("budget", [6000, 100_000])
def test_selection_cache_entry_and_byte_limits_evict_without_losing_results(budget):
    rows = corpus(count=30)
    cache = PersistentSelectionCache(max_entries=2, max_bytes=budget)
    for ordinal in range(15):
        key = array("q", [ordinal]).tobytes()
        actual = cache.select(lambda: iter(rows), 15, key, show_forbidden=True)
        assert signature(actual) == signature(
            select_fair_candidates(lambda: iter(rows), 15)
        )
        assert cache.entry_count <= 2 and cache.retained_bytes <= budget
    assert cache.entry_count > 0
    assert cache.retained_bytes == sum(
        item.retained_bytes for item in cache.entries.values()
    )


def test_tiny_cache_budget_and_spool_overflow_remain_exact_without_retention():
    rows = corpus()
    cache = PersistentSelectionCache(max_bytes=1, max_spool_bytes=4096)
    expected = select_fair_candidates(lambda: iter(rows), 25)
    for _ in range(2):
        assert signature(
            cache.select(lambda: iter(rows), 25, b"window", show_forbidden=True)
        ) == signature(expected)
    assert cache.entry_count == cache.retained_bytes == cache.hits == 0
    assert cache.misses == cache.fallback_reads == 2 and cache.metadata_passes == 4
    assert cache.spool_peak_bytes <= 4096


@pytest.mark.parametrize("phase", ["spool", "freeze"])
def test_cancellation_never_publishes_partial_selection(monkeypatch, phase):
    rows = corpus()
    cache = PersistentSelectionCache()
    cancelled = False

    def check():
        if cancelled:
            raise ViewportRequestCancelled()

    def candidates():
        nonlocal cancelled
        yield from rows
        if phase == "spool":
            cancelled = True

    original = _CachedSelection.freeze

    def freeze(selection):
        nonlocal cancelled
        result = original(selection)
        if phase == "freeze":
            cancelled = True
        return result

    monkeypatch.setattr(_CachedSelection, "freeze", freeze)
    with viewport_cancellation(check), pytest.raises(ViewportRequestCancelled):
        cache.select(candidates, 24, b"window", show_forbidden=True)
    assert cache.entry_count == cache.retained_bytes == 0


def test_cache_does_not_retain_any_input_feature_or_property_container():
    class Tracked(dict):
        pass

    references = []

    def candidates():
        for ordinal in range(100):
            properties = Tracked(
                kind="road", source_layer="native", unused={"large": "X" * 5000}
            )
            feature = Tracked(id={"route": ordinal}, properties=properties)
            references.extend([weakref.ref(feature), weakref.ref(properties)])
            yield ordinal, feature

    cache = PersistentSelectionCache()
    selected = cache.select(candidates, 24, b"window", show_forbidden=True)
    assert len(selected.candidates) == 24
    assert all(reference() is None for reference in references)
    assert cache.entry_count == 1


@pytest.mark.parametrize(
    "kwargs", [{"max_entries": -1}, {"max_bytes": -1}, {"max_spool_bytes": -1}]
)
def test_negative_cache_budgets_are_rejected(kwargs):
    with pytest.raises(ValueError):
        PersistentSelectionCache(**kwargs)
