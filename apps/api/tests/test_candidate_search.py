"""Search regressions with explicit obstacle oracles, not a CAD accuracy claim."""

from unittest.mock import patch

from test_placement_allocation import application

from app.geometry.domain import PositionAdvisory, PositionViolation
from app.planning.candidate_search import search_candidates
from app.planning.change_contracts import PlanObjectAddOperation
from app.planning.pattern_contracts import FillPatternRequest
from app.planning.patterns import generate_fill


def operation(x, y=50):
    return PlanObjectAddOperation.model_validate(
        {
            "type": "add",
            "object": {
                "kind": "tree",
                "x": x,
                "y": y,
                "group_ids": ["test"],
                "spacing_policy": "balanced",
            },
        }
    )


def test_rejected_and_unknown_probes_do_not_reserve_neighbour_space():
    app, project = application()
    geometry = app.manual.evaluation.geometry
    with (
        patch.object(
            geometry,
            "position_violation",
            side_effect=lambda p, x, *a: (
                PositionViolation(
                    "BLOCKED", "Здание", "В здании", "building", None, None, None
                )
                if x == 30
                else None
            ),
        ),
        patch.object(
            geometry,
            "placement_advisory_detail",
            side_effect=lambda p, x, *a: (
                PositionAdvisory(
                    "NATIVE_LOCAL_UNKNOWN", "Проверка", "Объект неизвестен", None
                )
                if x == 40
                else None
            ),
        ),
    ):
        result = search_candidates(
            project,
            [operation(x) for x in [30, 31, 40, 41, 42]],
            app.manual.evaluation,
            3,
            {},
            6,
        )
    accepted = [r.operation_index for r in result.candidate_results if r.status == 'allowed']
    assert set(accepted) == {1, 4}  # 31 and 42, not neighbouring rejected probes
    assert result.spacing_pruned == 2
    assert app.get(project.id).plan.objects == []


def test_alternative_sampler_does_not_pack_unchecked_candidates():
    _, project = application()
    request = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["east"],
        layout="natural",
        placement_mode="count",
        target_count=500,
        spacing_m=80,
        edge_offset_m=2,
    )
    assert len(generate_fill(request, project.planting_zones)) < 10
    alternatives = generate_fill(request, project.planting_zones, alternatives=True)
    assert len(alternatives) == 500
    assert alternatives == generate_fill(
        request, project.planting_zones, alternatives=True
    )


def test_search_continues_after_120_rejections_and_returns_partial_result():
    app, project = application()
    evaluation = app.manual.evaluation
    points = [operation(30)] * 130 + [operation(40), operation(50)]
    with patch.object(
        evaluation.geometry,
        "position_violation",
        side_effect=lambda p, x, *a: (
            PositionViolation(
                "BLOCKED", "Здание", "В здании", "building", None, None, None
            )
            if x == 30
            else None
        ),
    ):
        result = search_candidates(project, points, evaluation, 15, {}, 6)
    assert len(result.candidate_results) == 132
    assert sum(r.status == "allowed" for r in result.candidate_results) == 2
    assert result.stop_reason == "candidate_limit"


def test_time_limit_returns_already_accepted_positions_without_claiming_capacity():
    app, project = application()
    with patch("app.planning.candidate_search.SEARCH_BATCH_SIZE", 1):
        result = search_candidates(
            project,
            [operation(40), operation(50)],
            app.manual.evaluation,
            2,
            {},
            6,
            time_limit_s=0,
        )
    assert len(result.candidate_results) == 1
    assert result.candidate_results[0].status == "allowed"
    assert result.stop_reason == "time_limit"


def test_provider_failure_is_not_a_rejected_candidate_or_applicable_preview():
    import pytest

    app, project = application()
    with patch(
        "app.planning.candidate_search.prepare_positions",
        side_effect=RuntimeError("AutoCAD offline"),
    ):
        with pytest.raises(RuntimeError, match="AutoCAD offline"):
            search_candidates(project, [operation(40)], app.manual.evaluation, 1, {}, 6)


def test_spacing_rejections_do_not_query_cad_again_or_skip_later_free_candidate():
    app, project = application()
    points = [operation(40)] * 200 + [operation(60)]
    with (
        patch("app.planning.candidate_search.SEARCH_BATCH_SIZE", 1),
        patch("app.planning.candidate_search.prepare_positions") as prepare,
    ):
        result = search_candidates(project, points, app.manual.evaluation, 2, {}, 6)
    assert result.stop_reason == "target_reached"
    assert sum(r.status == "allowed" for r in result.candidate_results) == 2
    assert prepare.call_count == 2
    assert len(result.candidate_results) == 2
    assert result.spacing_pruned == 199
    assert app.get(project.id).plan.objects == []


def test_first_cad_batch_is_proportional_to_missing_plants():
    app, project = application()
    with patch("app.planning.candidate_search.prepare_positions") as prepare:
        search_candidates(project, [operation(30 + i * 10) for i in range(100)],
                          app.manual.evaluation, 10, {}, 6)
    assert len(prepare.call_args_list[0].args[2]) == 20


def test_unknown_probe_leaves_its_neighbour_available_for_the_next_batch():
    app, project = application()
    with patch.object(app.geometry, 'placement_advisory_detail', side_effect=lambda p, x, *a:
                      PositionAdvisory('NATIVE_LOCAL_UNKNOWN', 'Неизвестно', 'Неизвестно', None)
                      if x == 40 else None):
        result = search_candidates(project, [operation(40), operation(41)], app.evaluation, 1, {}, 6)
    assert [r.status for r in result.candidate_results] == ['unknown', 'allowed']
    assert result.candidate_results[-1].operation_index == 1


def test_disconnected_tiny_pocket_gets_a_candidate_before_random_sampling():
    from shapely.geometry import MultiPolygon, Point, box

    from app.planning.patterns import _poisson_candidates
    pockets = [box(0, 0, 100, 100), box(200, 200, 200.01, 200.01)]
    candidates = _poisson_candidates(MultiPolygon(pockets), 5, 20, 47, alternatives=True)
    assert all(any(pocket.covers(Point(c.x, c.y)) for c in candidates[:2]) for pocket in pockets)
