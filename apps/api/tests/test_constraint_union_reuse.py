"""Повторное использование ограничений без подмены результата другого участка."""

from contextlib import contextmanager
from copy import deepcopy
from unittest.mock import patch

import pytest
from shapely.geometry import box, mapping
from test_hybrid_search import populate
from test_native_live_provider import rectangle
from test_native_live_provider import setup as native_setup

from app.dxf_import.layers.contracts import LayerKind
from app.native_query.live.live_inventory import InventoryObject
from app.native_query.search import constraint_cache
from app.native_query.search.constraint_cache import (
    BatchConstraints,
    ConstraintUnionCache,
)
from app.native_query.search.hybrid_domain import CONFIG, HybridSearch

setup = native_setup


def _search(setup):
    engine, _, project = setup
    populate(project)
    engine.assert_current(project)
    return engine, project, HybridSearch(engine, project)


def _item(engine, route):
    return next(item for item in engine._objects if item.routes == (route,))


def _content(result):
    result = deepcopy(result)
    result["ga_search_domain"].pop("elapsed_s")
    result["ga_search_domain"].pop("cache_hits")
    return result


def test_valid_mask_reuses_actual_offset_across_radii_and_review_horizons(setup):
    engine, _, search = _search(setup)
    item = _item(engine, "BB")
    with patch.object(search.projection, "get", wraps=search.projection.get) as read:
        first, hit = search.mask(engine, item, "tree", 0.5, 0, 0)
        second, reused = search.mask(engine, item, "tree", 3, 1, 4)
    assert not hit and reused
    assert first.geometry.wkb == second.geometry.wkb
    assert read.call_count == 1


def test_successful_site_mask_ignores_plant_radii_but_keeps_sampling_inset(setup):
    engine, _, search = _search(setup)
    item = _item(engine, "AA")
    first, _ = search.mask(engine, item, "shrub", 0.5, 0, 0)
    second, reused = search.mask(engine, item, "tree", 10, 20, 30)
    assert reused and second.participation == "site"
    assert first.geometry.wkb == second.geometry.wkb


def test_effective_utility_root_offset_changes_mask_and_canopy_does_not(setup):
    engine, project, _ = _search(setup)
    project.layers[1].mapped_kind = LayerKind.UTILITY
    engine.assert_current(project)
    search = HybridSearch(engine, project)
    item = _item(engine, "BB")
    first, _ = search.mask(engine, item, "tree", 0.5, 0, 3)
    same, reused = search.mask(engine, item, "shrub", 0.8, 1, 3)
    changed, missed = search.mask(engine, item, "tree", 0.5, 0, 4)
    assert reused and not missed
    assert first.geometry.wkb == same.geometry.wkb
    assert changed.geometry.wkb != first.geometry.wkb


def test_unknown_mask_reuses_only_equal_review_horizon_and_keeps_issue(setup):
    engine, project, _ = _search(setup)
    project.layers[1].mapping_confirmed = False
    engine.assert_current(project)
    search = HybridSearch(engine, project)
    item = _item(engine, "BB")
    first, _ = search.mask(engine, item, "tree", 0.5, 0, 0)
    same, reused = search.mask(engine, item, "shrub", 0.9, 1, 0)
    changed, missed = search.mask(engine, item, "tree", 3, 0, 0)
    assert reused and not missed
    assert first.geometry.wkb == same.geometry.wkb
    assert changed.geometry.wkb != first.geometry.wkb
    assert (changed.participation, changed.reason, changed.detail) == (
        "review",
        "source_object",
        "Назначение слоя не подтверждено",
    )


@pytest.mark.parametrize("unknown", [False, True])
def test_new_zone_reuses_uncut_batch_with_exact_result_and_issues(setup, unknown):
    engine, project, _ = _search(setup)
    if unknown:
        project.layers[1].mapping_confirmed = False
    first_work = rectangle(-12, -4, 40, 4)
    next_work = rectangle(-11, -3, 39, 3)
    engine.automatic_safe_geometry(project, first_work, 0.5, "shrub")
    cached_search = engine._hybrid
    assert len(cached_search.constraint_unions.entries) == 1
    with patch.object(
        cached_search, "mask", side_effect=AssertionError("mask recomputed")
    ):
        reused = engine.automatic_safe_geometry(project, next_work, 0.5, "shrub")
    assert reused["ga_search_domain"]["cache_hits"] == 3
    engine._hybrid = None
    reference = engine.automatic_safe_geometry(project, next_work, 0.5, "shrub")
    assert _content(reused) == _content(reference)


def test_batch_reuses_same_effective_offsets_between_species_inputs(setup):
    engine, project, _ = _search(setup)
    for layer in project.layers[1:]:
        layer.mapped_kind = LayerKind.UTILITY
    work = rectangle(-12, -4, 40, 4)
    first = engine.automatic_safe_geometry(project, work, 0.5, "tree", 0.7, 0.8)
    search = engine._hybrid
    with patch.object(search, "mask", side_effect=AssertionError("mask recomputed")):
        second = engine.automatic_safe_geometry(project, work, 0.9, "shrub", 1, 1.1)
    assert _content(second) == _content(first)
    assert second["ga_search_domain"]["cache_hits"] == 3


def test_changed_ordered_candidate_batch_never_reuses_other_selection(setup):
    engine, project, _ = _search(setup)
    engine.automatic_safe_geometry(project, rectangle(-12, -4, 40, 4), 0.5, "tree")
    search = engine._hybrid
    with patch.object(search, "mask", wraps=search.mask) as masks:
        engine.automatic_safe_geometry(project, rectangle(-50, -4, -40, 4), 0.5, "tree")
    assert masks.call_count == 1  # Остаётся только всегда учитываемая территория.
    assert len(search.constraint_unions.entries) == 2


def test_role_change_rebuilds_search_instead_of_reusing_old_union(setup):
    engine, project, _ = _search(setup)
    work = rectangle(-12, -4, 40, 4)
    before = engine.automatic_safe_geometry(project, work, 0.5, "tree")
    previous = engine._hybrid
    project.layers[1].mapped_kind = LayerKind.LAWN
    after = engine.automatic_safe_geometry(project, work, 0.5, "tree")
    assert engine._hybrid is not previous
    assert after["ga_search_domain"]["cache_hits"] == 0
    assert (
        after["ga_search_domain"]["available_area_m2"]
        > before["ga_search_domain"]["available_area_m2"]
    )


def test_batch_hit_preserves_pending_progress_and_resume(setup):
    engine, project, _ = _search(setup)
    config = CONFIG.model_copy(update={"batch_objects": 1, "yield_seconds": 1e-12})
    first_work = rectangle(-12, -4, 40, 4)
    next_work = rectangle(-11, -3, 39, 3)
    with patch("app.native_query.search.hybrid_domain.CONFIG", config):
        for _ in range(3):
            engine.automatic_safe_geometry(project, first_work, 0.5, "tree")
        with patch.object(
            engine._hybrid, "mask", side_effect=AssertionError("mask recomputed")
        ):
            results = [
                engine.automatic_safe_geometry(project, next_work, 0.5, "tree")
                for _ in range(3)
            ]
    assert [r["ga_search_domain"]["processed_objects"] for r in results] == [1, 2, 3]
    assert [r["ga_search_domain"]["stop_reason"] for r in results] == [
        "time_limit",
        "time_limit",
        "resolution",
    ]
    assert results[0]["ga_search_domain"]["pending_area_m2"] == 300
    assert results[0]["ga_search_domain"]["available_area_m2"] == 0


def test_cached_batch_keeps_unlocated_source_issue_without_inventing_area(setup):
    engine, _, project = setup
    populate(project)
    engine.inventory = engine.inventory.model_copy(
        update={
            "objects": engine.inventory.objects
            + (
                InventoryObject(
                    route="DD",
                    layer="Здания",
                    entity_type="AcDbBlockReference",
                    bounds=None,
                    curve=False,
                    context=False,
                    error="xref_unavailable",
                ),
            )
        }
    )
    first = engine.automatic_safe_geometry(
        project, rectangle(-12, -4, 40, 4), 0.5, "tree"
    )
    search = engine._hybrid
    with patch.object(search, "mask", side_effect=AssertionError("mask recomputed")):
        second = engine.automatic_safe_geometry(
            project, rectangle(-11, -3, 39, 3), 0.5, "tree"
        )
    expected = next(
        issue
        for issue in first["ga_search_domain"]["source_issues"]
        if issue["routes"] == ["DD"]
    )
    assert not expected["localized"]
    assert expected in second["ga_search_domain"]["source_issues"]
    assert second["ga_search_domain"]["unresolved_area_m2"] == 0


def test_batch_hit_does_not_bypass_late_scope_integrity_failure(setup):
    engine, project, _ = _search(setup)
    project.layers[1].mapping_confirmed = False
    engine.automatic_safe_geometry(project, rectangle(-12, -4, 40, 4), 0.5, "tree")
    search = engine._hybrid

    @contextmanager
    def changed_snapshot(_project):
        yield
        raise ValueError("Сохранённая геометрия изменилась при чтении")

    with (
        patch.object(engine, "calculation_scope", changed_snapshot),
        patch.object(search, "mask", side_effect=AssertionError("mask recomputed")),
        patch.object(
            search.constraint_unions, "get", wraps=search.constraint_unions.get
        ) as read,
        pytest.raises(ValueError, match="геометрия изменилась"),
    ):
        engine.automatic_safe_geometry(project, rectangle(-11, -3, 39, 3), 0.5, "tree")
    assert read.call_count == 1
    assert engine._hybrid is None


def _batch():
    return BatchConstraints(
        box(1, 1, 2, 2),
        [box(0, 0, 10, 10)],
        [box(9, 9, 11, 11)],
        {"source_object": box(3, 3, 4, 4)},
        [
            {
                "routes": ["AA", "BB"],
                "layer": "layer",
                "reason": "source_object",
                "detail": "detail",
                "localized": True,
            }
        ],
    )


def test_cached_payload_and_diagnostic_copies_are_isolated():
    cache = ConstraintUnionCache()
    original = _batch()
    expected = deepcopy(original.issues)
    cache.remember(("same",), 2, original)
    original.issues[0]["routes"].append("mutated-input")
    one = cache.get(("same",), 2)
    assert one.issues == expected
    one.issues[0]["routes"].append("mutated-result")
    one.reviews.clear()
    two = cache.get(("same",), 2)
    assert two.issues == expected and list(two.reviews) == ["source_object"]
    assert two.blocked.wkb == original.blocked.wkb
    assert two.blocked is not one.blocked
    assert cache.get(("same",), 3) is None


@pytest.mark.parametrize(
    "limit",
    [
        "MAX_CACHED_CONSTRAINT_BYTES",
        "MAX_CACHED_CONSTRAINT_COORDINATES",
        "MAX_CACHED_CONSTRAINT_TOTAL_COORDINATES",
        "MAX_CACHED_CONSTRAINT_ISSUES",
        "MAX_CACHED_CONSTRAINT_ROUTES",
    ],
)
def test_oversized_batch_remains_correct_without_being_retained(monkeypatch, limit):
    cache = ConstraintUnionCache()
    value = _batch()
    before = mapping(value.blocked)
    monkeypatch.setattr(constraint_cache, limit, 0)
    cache.remember(("large",), 2, value)
    assert not cache.entries
    assert (cache.bytes, cache.coordinates, cache.issues, cache.routes) == (0, 0, 0, 0)
    assert mapping(value.blocked) == before and len(value.issues) == 1


def test_union_lru_eviction_and_replacement_keep_all_weights_bounded(monkeypatch):
    monkeypatch.setattr(constraint_cache, "MAX_CACHED_CONSTRAINT_BATCHES", 2)
    cache = ConstraintUnionCache()
    value = _batch()
    cache.remember(("001",), 2, value)
    expected = (cache.bytes, cache.coordinates, cache.issues, cache.routes)
    cache.remember(("001",), 2, value)
    assert (cache.bytes, cache.coordinates, cache.issues, cache.routes) == expected
    cache.remember(("002",), 2, value)
    cache.get(("001",), 2)
    cache.remember(("003",), 2, value)
    assert cache.get(("002",), 2) is None
    assert cache.get(("001",), 2) is not None
    assert len(cache.entries) == 2
    assert cache.bytes == 2 * expected[0]
    assert cache.coordinates == 2 * expected[1]
    assert cache.issues == 2 * expected[2]
    assert cache.routes == 2 * expected[3]


@pytest.mark.parametrize(
    ("limit", "counter"),
    [
        ("MAX_CACHED_CONSTRAINT_BYTES", "bytes"),
        ("MAX_CACHED_CONSTRAINT_TOTAL_COORDINATES", "coordinates"),
        ("MAX_CACHED_CONSTRAINT_ISSUES", "issues"),
        ("MAX_CACHED_CONSTRAINT_ROUTES", "routes"),
    ],
)
def test_each_weight_limit_evicts_old_batch_without_changing_payload(
    monkeypatch, limit, counter
):
    cache = ConstraintUnionCache()
    value = _batch()
    cache.remember(("001",), 2, value)
    maximum = getattr(cache, counter)
    monkeypatch.setattr(constraint_cache, limit, maximum)
    cache.remember(("002",), 2, value)
    assert cache.get(("001",), 2) is None
    restored = cache.get(("002",), 2)
    assert restored.blocked.wkb == value.blocked.wkb
    assert restored.issues == value.issues
    assert getattr(cache, counter) <= maximum


def test_cancelled_encoding_never_publishes_batch(monkeypatch):
    cache = ConstraintUnionCache()
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("cancelled")

    monkeypatch.setattr(constraint_cache, "check_operation_cancelled", cancel)
    with pytest.raises(RuntimeError, match="cancelled"):
        cache.remember(("cancelled",), 2, _batch())
    assert not cache.entries
    assert cache.bytes == cache.coordinates == cache.issues == cache.routes == 0
