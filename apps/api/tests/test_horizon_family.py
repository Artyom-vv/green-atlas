"""Cold numeric family invariants, independent of CAD, files and warm caches."""

from collections import Counter
from dataclasses import replace
from itertools import combinations
from math import hypot

import pytest

from app.operations.progress import OperationCancelled
from app.planning.contracts import PlanObject
from app.planning.domain import required_spacing
from app.planning.search.horizon_family import (
    HorizonCandidate,
    HorizonFamilyTimeout,
    select_horizon_family,
)
from app.species.catalog import get_species, growth_forecasts


def _template(kind="tree"):
    return PlanObject(
        id=kind, kind=kind, x=0, y=0, radius=1.6 if kind == "tree" else 0.6
    )


def _zero_spacing(first, second, year):
    return 0.0


def _pool():
    return [
        HorizonCandidate(3 * x, 3 * y, (index * 7) % 41, "a" if x < 2 else "b")
        for index, (x, y) in enumerate((x, y) for y in range(4) for x in range(4))
    ]


def _select(points, year, **options):
    arguments = {
        "templates": [_template()],
        "minimum_spacing_by_year": [1.4 + year / 10 for year in range(41)],
        "target_count": 8,
        "spacing_rule": _zero_spacing,
    }
    arguments.update(options)
    return select_horizon_family(points, requested_year=year, **arguments)


def _assert_valid(points, result, year, minimum, templates, rule, quotas=None):
    selected = [points[index] for index in result.selected_indices]
    assert len(set(result.selected_indices)) == len(selected)
    assert all(point.valid_through >= year for point in selected)
    for first, second in combinations(selected, 2):
        required = max(
            minimum,
            rule(
                templates[first.template_index], templates[second.template_index], year
            ),
            rule(
                templates[second.template_index], templates[first.template_index], year
            ),
        )
        assert hypot(first.x - second.x, first.y - second.y) >= required
    if quotas:
        assert all(
            count <= quotas.get(zone, 0)
            for zone, count in Counter(point.zone_id for point in selected).items()
        )


def test_all_41_cold_years_are_monotone_and_pass_strict_spacing():
    points = _pool()
    results = [_select(points, year) for year in range(41)]
    counts = [len(result.selected_indices) for result in results]
    assert all(younger >= older for younger, older in zip(counts, counts[1:]))
    assert counts[0] > counts[40]
    for year, result in enumerate(results):
        _assert_valid(
            points, result, year, 1.4 + year / 10, [_template()], _zero_spacing
        )


def test_reversed_request_order_and_repeated_cold_calls_are_identical():
    points = _pool()
    forward = {year: _select(points, year).selected_indices for year in range(41)}
    reverse = {
        year: _select(points, year).selected_indices for year in reversed(range(41))
    }
    assert forward == reverse


@pytest.mark.parametrize("maximum_checks", [0, 1, 9, 20, 80, 500])
def test_fixed_work_budget_preserves_the_shared_prefix_for_all_years(maximum_checks):
    points = _pool()
    results = [
        _select(points, year, maximum_checks=maximum_checks)
        for year in (0, 1, 5, 7, 20, 40)
    ]
    counts = [len(result.selected_indices) for result in results]
    assert all(younger >= older for younger, older in zip(counts, counts[1:]))
    assert all(result.stats.work_units <= maximum_checks for result in results)
    for year, result in zip((0, 1, 5, 7, 20, 40), results, strict=True):
        _assert_valid(
            points, result, year, 1.4 + year / 10, [_template()], _zero_spacing
        )


def test_alternative_order_escapes_the_central_greedy_trap():
    points = [
        HorizonCandidate(3, 0, 40, "zone"),
        HorizonCandidate(0, 0, 40, "zone"),
        HorizonCandidate(6, 0, 40, "zone"),
    ]
    result = _select(points, 7, minimum_spacing_by_year=[3.5] * 41, target_count=2)
    assert set(result.selected_indices) == {1, 2}
    assert result.stats.last_year == 40
    assert result.stats.orders_started >= 2


@pytest.mark.parametrize("year", [0, 7, 40])
def test_quotas_exclude_zero_unknown_and_none_zones_without_redistribution(year):
    points = [
        HorizonCandidate(10 * index, 0, 40, zone)
        for index, zone in enumerate(("a", "a", "a", "b", "b", "zero", None, "unknown"))
    ]
    quotas = {"a": 2, "b": 1, "zero": 0}
    result = _select(points, year, target_count=8, zone_targets=quotas)
    assert len(result.selected_indices) == 3
    _assert_valid(
        points, result, year, 1.4 + year / 10, [_template()], _zero_spacing, quotas
    )


def test_existing_plant_constraints_are_carried_by_static_lifetimes():
    points = [
        HorizonCandidate(0, 0, -1, "zone"),
        HorizonCandidate(10, 0, 5, "zone"),
        HorizonCandidate(20, 0, 40, "zone"),
    ]
    older = _select(points, 7)
    younger = _select(points, 5)
    assert older.selected_indices == (2,)
    assert set(younger.selected_indices) == {1, 2}


def test_mixed_templates_keep_original_indices_and_exact_pair_rules():
    templates = [_template("tree"), _template("shrub")]
    points = [
        HorizonCandidate(index * 1.5, row * 2.0, 40, "a", (index + row) % 2)
        for row in range(3)
        for index in range(6)
    ]

    def rule(first, second, year):
        base = 1.0 if first.kind == second.kind == "shrub" else 3.5
        return base + year / 10

    results = [
        _select(points, year, templates=templates, spacing_rule=rule)
        for year in (0, 5, 7, 40)
    ]
    counts = [len(result.selected_indices) for result in results]
    assert all(younger >= older for younger, older in zip(counts, counts[1:]))
    for year, result in zip((0, 5, 7, 40), results, strict=True):
        _assert_valid(points, result, year, 1.4 + year / 10, templates, rule)


def test_production_growth_spacing_is_monotone_with_real_species_templates():
    templates = []
    for species in ("tilia-cordata@2026-08-28.1", "spiraea-japonica@2026-08-28.1"):
        revision = get_species(species)
        template = _template(revision.kind)
        template.canopy_forecast, template.root_forecast = growth_forecasts(
            revision, "unspecified"
        )
        template.species_revision_id = revision.id
        templates.append(template)
    points = [HorizonCandidate(5 * index, 0, 40, "a", index % 2) for index in range(10)]
    for year in (0, 5, 7, 40):
        result = _select(
            points, year, templates=templates, spacing_rule=required_spacing
        )
        _assert_valid(
            points, result, year, 1.4 + year / 10, templates, required_spacing
        )


def test_exact_boundary_is_allowed_but_rounded_short_distance_is_not():
    points = [
        HorizonCandidate(0, 0, 40, "a"),
        HorizonCandidate(3.5, 0, 40, "a"),
        HorizonCandidate(7, 0, 40, "a"),
    ]
    result = _select(points, 7, minimum_spacing_by_year=[3.5] * 41, target_count=3)
    assert len(result.selected_indices) == 3
    short = [points[0], replace(points[1], x=3.499999577)]
    result = _select(short, 7, minimum_spacing_by_year=[3.5] * 41, target_count=2)
    assert len(result.selected_indices) == 1


def test_bucket_reach_includes_large_species_clearance():
    points = [HorizonCandidate(0, 0, 40, "a"), HorizonCandidate(18, 0, 40, "a")]
    result = _select(points, 7, spacing_rule=lambda first, second, year: 20.0)
    assert len(result.selected_indices) == 1


def test_templates_coordinates_and_inputs_are_not_changed_or_used_as_positions():
    points = _pool()
    before = list(points)
    template = _template()
    saved = template.model_dump()
    result = _select(points, 7, templates=[template])
    moved = template.model_copy(update={"x": 1e9, "y": -1e9})
    assert (
        result.selected_indices
        == _select(points, 7, templates=[moved]).selected_indices
    )
    assert points == before
    assert template.model_dump() == saved


def test_timeout_raises_instead_of_returning_a_partial_final_answer():
    times = iter([0.0, 0.0, 2.0])
    with pytest.raises(HorizonFamilyTimeout):
        _select(_pool(), 7, time_limit_s=1, clock=lambda: next(times, 2.0))


def test_cancellation_propagates_without_returning_a_partial_answer():
    calls = 0

    def cancel():
        nonlocal calls
        calls += 1
        if calls > 45:
            raise OperationCancelled()

    with pytest.raises(OperationCancelled):
        _select(_pool(), 7, check_cancelled=cancel)


@pytest.mark.parametrize("field", ["x", "y"])
def test_nonfinite_points_are_rejected(field):
    with pytest.raises(ValueError, match="Координаты"):
        _select([replace(_pool()[0], **{field: float("nan")})], 7)


@pytest.mark.parametrize(
    "options",
    [
        {"requested_year": -1},
        {"requested_year": 41},
        {"requested_year": True},
        {"target_count": 0},
        {"target_count": 5001},
        {"maximum_checks": -1},
        {"templates": []},
        {"templates": [_template()] * 3},
        {"minimum_spacing_by_year": [1] * 40},
        {"minimum_spacing_by_year": [1] * 40 + [-1]},
        {"minimum_spacing_by_year": [1] * 40 + [float("inf")]},
        {"zone_targets": {"a": -1}},
        {"time_limit_s": -1},
    ],
)
def test_invalid_family_parameters_are_rejected(options):
    arguments = {
        "candidates": _pool(),
        "templates": [_template()],
        "requested_year": 7,
        "minimum_spacing_by_year": [1.0] * 41,
    }
    arguments.update(options)
    with pytest.raises(ValueError):
        select_horizon_family(**arguments)


@pytest.mark.parametrize(
    "changes", [{"valid_through": 41}, {"valid_through": -2}, {"template_index": 1}]
)
def test_invalid_candidate_metadata_is_rejected(changes):
    with pytest.raises(ValueError):
        _select([replace(_pool()[0], **changes)], 7)


def test_nonmonotone_effective_spacing_is_rejected_instead_of_assumed():
    with pytest.raises(ValueError, match="не убывать"):
        _select(_pool(), 7, minimum_spacing_by_year=[5] * 20 + [4] * 21)


def test_point_pool_is_bounded_without_clipping_a_horizon_dependent_prefix():
    with pytest.raises(ValueError, match="20000"):
        _select([_pool()[0]] * 20_001, 7)


def test_empty_pool_has_a_deterministic_empty_result():
    result = _select([], 7)
    assert result.selected_indices == ()
    assert result.stats.work_units == 0
    assert not result.stats.work_limit_reached
