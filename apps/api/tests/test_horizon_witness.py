"""Bounded coordinate hints never retain projects or authorize old placements."""

import gc
import weakref
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.planning.changes.contracts import ChangeSetPreview
from app.planning.contracts import Plan, PlanObject
from app.planning.patterns.contracts import (
    FillPatternRequest,
    PatternPreview,
    PlacementMaskRequest,
    RowPatternRequest,
)
from app.planning.patterns.horizon_witness import HorizonWitnesses
from app.projects.contracts import Project


def _project(year=7, **changes):
    return Project(
        **{
            "id": "witness-project",
            "name": "Isolated coordinate witness",
            "state_version": 4,
            "geometry_version": 3,
            "plan": Plan(version=2, planning_horizon_year=year),
            **changes,
        }
    )


def _request(year=7, **changes):
    return FillPatternRequest(
        **{
            "scenario_horizon_year": year,
            "base_plan_version": 2,
            "zone_ids": ["west"],
            "composition": "trees",
            "layout": "regular",
            "placement_mode": "count",
            "target_count": 8,
            "species_revision_id": "tree@1",
            **changes,
        }
    )


def _preview(xs=(10.25, 20.5), *, can_apply=True, accepted_count=None):
    additions = [PlanObject(kind="tree", x=x, y=-x / 2, radius=1) for x in xs]
    return PatternPreview(
        type="fill",
        planning_horizon_year=7,
        pattern_id="original-pattern",
        requested_count=8,
        accepted_count=len(additions) if accepted_count is None else accepted_count,
        change_set=ChangeSetPreview(
            digest="a" * 64,
            base_plan_version=2,
            source="pattern",
            label="Previously checked layout",
            can_apply=can_apply,
            additions=additions,
            expires_at="2030-01-01T00:00:00+00:00",
        ),
    )


def _positions(cache, year=5, accepted_count=0, **request_changes):
    return [
        (point.x, point.y)
        for point in cache.better_positions(
            _project(year), _request(year, **request_changes), accepted_count
        )
    ]


def test_uses_only_a_strictly_better_layout_from_a_greater_actual_horizon():
    cache = HorizonWitnesses()
    cache.remember(_project(), _request(), _preview())

    assert _positions(cache) == [(10.25, -5.125), (20.5, -10.25)]
    assert _positions(cache, accepted_count=1)
    assert _positions(cache, accepted_count=2) == []
    assert _positions(cache, accepted_count=3) == []
    assert _positions(cache, year=7) == []
    assert _positions(cache, year=8) == []


def test_keeps_separate_horizon_witnesses_for_a_nonsequential_comparison():
    cache = HorizonWitnesses()
    cache.remember(_project(7), _request(7), _preview((70, 71)))
    cache.remember(_project(5), _request(5), _preview((50, 51, 52)))

    assert _positions(cache, year=6, accepted_count=1) == [
        (70, -35),
        (71, -35.5),
    ]
    assert _positions(cache, year=4, accepted_count=2) == [
        (50, -25),
        (51, -25.5),
        (52, -26),
    ]
    assert _positions(cache, year=5, accepted_count=2) == []


@pytest.mark.parametrize("xs", [(30,), (30, 31)])
def test_same_horizon_does_not_replace_an_at_least_as_good_witness(xs):
    cache = HorizonWitnesses()
    cache.remember(_project(), _request(), _preview((10, 20)))
    cache.remember(_project(), _request(), _preview(xs))

    assert _positions(cache) == [(10, -5), (20, -10)]


def test_same_horizon_replaces_the_witness_when_more_final_additions_fit():
    cache = HorizonWitnesses()
    cache.remember(_project(), _request(), _preview((10,)))
    cache.remember(_project(), _request(), _preview((20, 30)))

    assert _positions(cache) == [(20, -10), (30, -15)]


@pytest.mark.parametrize(
    "changes",
    [
        {"id": "other-project"},
        {"state_version": 5},
        {"geometry_version": 4},
    ],
)
def test_project_revision_and_identity_must_match(changes):
    cache = HorizonWitnesses()
    cache.remember(_project(), _request(), _preview())

    assert cache.better_positions(_project(5, **changes), _request(5), 0) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"base_plan_version": 3},
        {"zone_ids": ["east"]},
        {"zone_ids": ["west", "east"]},
        {"target_count": 9},
        {"zone_distribution": "equal"},
        {"species_revision_id": "tree@2"},
        {"composition": "shrubs"},
        {"spacing_policy": "open"},
        {"spacing_m": 7},
        {"edge_offset_m": 2},
        {"angle_deg": 10},
        {"seed": 47},
        {"layout_radius_m": 2},
        {"size_class": "large"},
        {"placement_mode": "spacing"},
        {"layout": "staggered"},
    ],
)
def test_other_placement_parameters_do_not_share_coordinates(changes):
    cache = HorizonWitnesses()
    cache.remember(_project(), _request(), _preview())

    assert _positions(cache, **changes) == []


@pytest.mark.parametrize(
    ("layout", "mode"),
    [
        ("regular", "count"),
        ("staggered", "count"),
    ],
)
def test_supported_whole_layouts_can_supply_a_recheck_hint(layout, mode):
    cache = HorizonWitnesses()
    cache.remember(
        _project(),
        _request(layout=layout, placement_mode=mode),
        _preview(),
    )

    assert len(_positions(cache, layout=layout, placement_mode=mode)) == 2


@pytest.mark.parametrize(
    "pattern_request",
    [
        _request(composition="mixed"),
        _request(layout="natural", placement_mode="count"),
        _request(layout="natural", placement_mode="spacing"),
        _request(layout="regular", placement_mode="spacing"),
        _request(layout="staggered", placement_mode="spacing"),
        RowPatternRequest(
            base_plan_version=2,
            zone_ids=["west"],
            axis={"type": "LineString", "coordinates": [[0, 0], [20, 0]]},
        ),
        PlacementMaskRequest(
            base_plan_version=2,
            zone_ids=["west"],
            mask_id="regular_grid",
        ),
    ],
)
def test_unsupported_requests_neither_store_nor_retrieve_a_witness(pattern_request):
    cache = HorizonWitnesses()
    cache.remember(_project(), pattern_request, _preview())
    assert cache.better_positions(_project(5), pattern_request, 0) == []
    assert cache._entries == {}


@pytest.mark.parametrize("case", ["absent", "refused", "empty", "oversized"])
def test_unusable_final_previews_do_not_replace_a_usable_hint(case):
    cache = HorizonWitnesses(max_positions=2)
    cache.remember(_project(), _request(), _preview())
    invalid = _preview((30, 40, 50)) if case == "oversized" else _preview()
    if case == "absent":
        invalid.change_set = None
    elif case == "refused":
        invalid.change_set.can_apply = False
    elif case == "empty":
        invalid.change_set.additions = []
    cache.remember(_project(), _request(), invalid)

    assert _positions(cache) == [(10.25, -5.125), (20.5, -10.25)]


def test_hints_use_final_additions_instead_of_the_preview_summary_count():
    cache = HorizonWitnesses()
    cache.remember(_project(), _request(), _preview(accepted_count=99))

    assert len(_positions(cache, accepted_count=1)) == 2
    assert _positions(cache, accepted_count=2) == []


def test_coordinates_are_detached_from_preview_and_returned_candidates():
    cache = HorizonWitnesses()
    preview = _preview()
    cache.remember(_project(), _request(), preview)
    preview.change_set.additions[0].x = 999
    returned = cache.better_positions(_project(5), _request(5), 0)
    returned.clear()

    assert _positions(cache) == [(10.25, -5.125), (20.5, -10.25)]


def test_cache_does_not_retain_projects_or_previews():
    cache = HorizonWitnesses()
    project, preview = _project(), _preview()
    project_reference, preview_reference = weakref.ref(project), weakref.ref(preview)
    cache.remember(project, _request(), preview)
    del project, preview
    gc.collect()

    assert project_reference() is None
    assert preview_reference() is None
    assert len(_positions(cache)) == 2


def test_coordinate_budget_is_global_and_evicts_whole_old_layouts():
    cache = HorizonWitnesses(max_positions=5, max_entries=8)
    for seed in range(3):
        cache.remember(_project(), _request(seed=seed), _preview((seed, seed + 1)))

    assert _positions(cache, seed=0) == []
    assert len(_positions(cache, seed=1)) == 2
    assert len(_positions(cache, seed=2)) == 2
    assert sum(len(value.coordinates) for value in cache._entries.values()) == 4 * 16
    assert cache._position_count == 4


def test_entry_budget_counts_horizons_and_requests_together():
    cache = HorizonWitnesses(max_positions=20, max_entries=2)
    cache.remember(_project(7), _request(7), _preview((70,)))
    cache.remember(_project(5), _request(5), _preview((50, 51)))
    cache.remember(_project(9), _request(9, seed=2), _preview((90,)))

    assert _positions(cache, year=6) == []
    assert len(_positions(cache, year=4)) == 2
    assert len(_positions(cache, year=4, seed=2)) == 1
    assert len(cache._entries) == 2


def test_successful_lookup_refreshes_lru_without_replacing_coordinates():
    cache = HorizonWitnesses(max_positions=20, max_entries=2)
    cache.remember(_project(), _request(seed=1), _preview((10,)))
    cache.remember(_project(), _request(seed=2), _preview((20,)))
    assert len(_positions(cache, seed=1)) == 1
    cache.remember(_project(), _request(seed=3), _preview((30,)))

    assert _positions(cache, seed=2) == []
    assert _positions(cache, seed=1) == [(10, -5)]
    assert _positions(cache, seed=3) == [(30, -15)]


def test_new_project_revision_releases_all_old_horizons_for_that_project():
    cache = HorizonWitnesses(max_positions=20)
    cache.remember(_project(7), _request(7), _preview((70,)))
    cache.remember(_project(5), _request(5), _preview((50,)))
    cache.remember(_project(7, state_version=5), _request(), _preview((90,)))

    assert _positions(cache) == []
    assert len(cache._entries) == 1
    assert cache._position_count == 1
    assert (
        len(cache.better_positions(_project(5, state_version=5), _request(5), 0)) == 1
    )


def test_parallel_writers_keep_global_coordinate_and_entry_budgets():
    cache = HorizonWitnesses(max_positions=7, max_entries=3)

    def remember(seed):
        cache.remember(_project(), _request(seed=seed), _preview((seed, seed + 1)))
        cache.better_positions(_project(5), _request(5, seed=seed), 0)

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(remember, range(50)))

    assert len(cache._entries) <= 3
    assert cache._position_count <= 7
    assert cache._position_count == sum(
        value.count for value in cache._entries.values()
    )
    assert sum(len(value.coordinates) for value in cache._entries.values()) <= 7 * 16


def test_another_application_process_starts_without_historical_coordinates():
    warmed = HorizonWitnesses()
    warmed.remember(_project(), _request(), _preview())

    assert _positions(warmed)
    assert _positions(HorizonWitnesses()) == []


def test_missing_plan_neither_records_nor_returns_coordinates():
    cache = HorizonWitnesses()
    cache.remember(_project(plan=None), _request(), _preview())
    assert _positions(cache) == []
    cache.remember(_project(), _request(), _preview())
    assert cache.better_positions(_project(5, plan=None), _request(5), 0) == []


@pytest.mark.parametrize(
    "limits", [{"max_positions": 0}, {"max_entries": 0}, {"max_entries": -1}]
)
def test_nonpositive_cache_limits_fail_explicitly(limits):
    with pytest.raises(ValueError):
        HorizonWitnesses(**limits)
