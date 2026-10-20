"""Найденные позиции старшего прогноза не теряются без повторной проверки.

Контуры здесь синтетические: это воспроизведения генераторов, а не измерение CAD.
"""

from dataclasses import replace
from math import hypot

import pytest
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, box, mapping
from test_geometric_search_acceptance import _application

from app.operations.progress import OperationCancelled, operation_cancellation
from app.planning.changes.evaluation import CandidateIssue, CandidateRejected
from app.planning.contracts import PlanObject, PlanObjectCreate
from app.planning.patterns import application as patterns
from app.planning.patterns.contracts import FillPatternRequest
from app.planning.scenario import scenario_project
from app.planning.search import horizon_search
from app.planning.search.contracts import SearchDomain
from app.shared.failures import DomainFailure, FailureReason
from app.species.contracts import GrowthEnvelopeForecast

SPRUCE = "picea-pungens@2026-09-17.1"
LINDEN = "tilia-cordata@2026-08-28.1"


def _spruce_request(**changes):
    return FillPatternRequest(
        base_plan_version=1,
        zone_ids=["outer"],
        layout="natural",
        placement_mode="spacing",
        species_revision_id=SPRUCE,
        edge_offset_m=0,
        seed=911,
    ).model_copy(update=changes)


def test_spruce_7_then_4_years_keeps_at_least_the_verified_count():
    app, project = _application(box(0, 0, 30, 15))
    before = app.get(project.id).model_dump()
    request = _spruce_request()
    older = app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 7})
    )
    younger = app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 4})
    )
    assert older.accepted_count == 23
    assert younger.accepted_count >= older.accepted_count
    assert younger.change_set is not None and younger.change_set.can_apply
    assert older.change_set is not None
    assert not app.patterns._horizon_witnesses._entries
    assert younger.planning_horizon_year == 4
    assert younger.effective_spacing_m == 3.5
    assert younger.rejected_count == younger.capacity_shortfall == 0
    assert younger.zone_allocations[0].accepted_count == younger.accepted_count
    assert not younger.skipped
    current = scenario_project(project, 4)
    working = current.plan.model_copy(deep=True)
    for addition in younger.change_set.additions:
        payload = PlanObjectCreate.model_validate(
            addition.model_dump(exclude={"id", "status", "planting_zone_id"})
        )
        checked, issue = app.evaluation.preview_addition(current, working, payload)
        assert issue is None
        working.objects.append(checked)
    points = younger.change_set.additions
    assert all(
        hypot(a.x - b.x, a.y - b.y) >= 3.5
        for i, a in enumerate(points)
        for b in points[i + 1 :]
    )
    assert app.get(project.id).model_dump() == before


@pytest.mark.parametrize(
    "bounds,target,young_year",
    [
        ((78.6, 89.4, 89.0, 136.1), 5, 5),
        ((354.53058254, 299.45855393, 382.40537986, 318.69271053), 6, 5),
    ],
)
def test_regular_grid_does_not_lose_feasible_count_even_without_a_witness(
    bounds, target, young_year
):
    # Независимые приложения исключают помощь кеша предыдущего горизонта.
    for year in (7, young_year):
        app, project = _application(box(*bounds))
        result = app.preview_pattern(
            project.id,
            FillPatternRequest(
                base_plan_version=1,
                zone_ids=["outer"],
                layout="regular",
                placement_mode="count",
                target_count=target,
                species_revision_id=LINDEN,
                scenario_horizon_year=year,
                edge_offset_m=0,
                seed=47,
            ),
        )
        assert result.accepted_count == target
        assert result.change_set is not None and result.change_set.can_apply


@pytest.mark.parametrize("angle", [44, 51])
def test_rotated_spruce_row_keeps_minimum_spacing_after_coordinate_rounding(angle):
    area = translate(
        rotate(box(-5.336, -1.811, 5.336, 1.811), angle, origin=(0, 0)),
        100,
        100,
    )
    app, project = _application(area)
    result = app.preview_pattern(
        project.id,
        _spruce_request(
            layout="regular",
            placement_mode="count",
            target_count=3,
            angle_deg=angle,
            scenario_horizon_year=7,
        ),
    )
    assert result.accepted_count == 3
    assert result.change_set is not None and result.change_set.can_apply
    assert result.rejected_count == 0
    points = result.change_set.additions
    assert all(
        hypot(a.x - b.x, a.y - b.y) >= result.effective_spacing_m
        for i, a in enumerate(points)
        for b in points[i + 1 :]
    )


def test_previous_positions_still_obey_the_current_checker(monkeypatch):
    app, project = _application(box(0, 0, 30, 15))
    request = _spruce_request()
    older = app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 7})
    )
    previous = {(p.x, p.y) for p in older.change_set.additions}
    checked_previous = set()
    evaluate = app.evaluation.preview_addition

    def reject_old_positions(project, plan, payload, *args, **kwargs):
        point = (payload.x, payload.y)
        if point in previous:
            checked_previous.add(point)
            raise CandidateRejected(
                CandidateIssue(
                    status="blocked",
                    code="CURRENT_RESTRICTION",
                    category="constraint",
                    message="Позиция недопустима по текущему основанию",
                    zone_id="outer",
                )
            )
        return evaluate(project, plan, payload, *args, **kwargs)

    monkeypatch.setattr(app.evaluation, "preview_addition", reject_old_positions)
    younger = app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 4})
    )
    assert checked_previous == previous
    assert younger.accepted_count > 0
    assert younger.change_set is not None and younger.change_set.can_apply
    assert not previous.intersection((p.x, p.y) for p in younger.change_set.additions)


def test_requested_count_reports_canonical_pool_and_all_site_checks(monkeypatch):
    app, project = _application(box(0, 0, 30, 15))
    request = _spruce_request(placement_mode="count", target_count=5)
    older = app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 7})
    )
    assert older.accepted_count == 5

    def old_search(*args, **kwargs):
        raise AssertionError("Растущая порода использует согласованную семью")

    monkeypatch.setattr(patterns, "search_geometric_placement", old_search)
    younger = app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 4})
    )
    assert younger.accepted_count == 5
    assert younger.change_set is not None and younger.change_set.can_apply
    assert younger.search_stop_reason == "target_reached"
    assert younger.generated_count == older.generated_count
    assert younger.generated_count > younger.accepted_count
    assert younger.search_full_checked_count >= younger.generated_count
    assert younger.search_elapsed_s > 0
    assert not app.patterns._horizon_witnesses._entries
    assert not younger.skipped
    assert not younger.reason_summary


def test_cancellation_during_lifetime_checks_does_not_publish_a_preview(monkeypatch):
    app, project = _application(box(0, 0, 30, 15))
    request = _spruce_request(placement_mode="count", target_count=5)
    app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 7})
    )
    previews_before = set(app.changes._previews)
    evaluate = app.evaluation.preview_addition
    cancel_requested = False

    def first_check(*args, **kwargs):
        nonlocal cancel_requested
        result = evaluate(*args, **kwargs)
        cancel_requested = True
        return result

    def cancel():
        if cancel_requested:
            raise OperationCancelled()

    monkeypatch.setattr(app.evaluation, "preview_addition", first_check)
    with operation_cancellation(cancel), pytest.raises(OperationCancelled):
        app.preview_pattern(
            project.id, request.model_copy(update={"scenario_horizon_year": 4})
        )
    assert set(app.changes._previews) == previews_before
    assert app.get(project.id).plan == project.plan


def test_time_limit_has_no_partial_numeric_or_applicable_result(monkeypatch):
    app, project = _application(box(0, 0, 30, 15))
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(horizon_search, "monotonic", lambda: next(clock, 100.0))
    with pytest.raises(DomainFailure) as error:
        app.preview_pattern(project.id, _spruce_request(scenario_horizon_year=4))
    assert error.value.reason == FailureReason.PATTERN_SEARCH_TIMEOUT
    assert not app.changes._previews
    assert app.get(project.id).plan == project.plan


def test_disagreement_with_final_checker_cannot_publish_a_smaller_result(monkeypatch):
    app, project = _application(box(0, 0, 30, 15))
    evaluate = app.evaluation.preview_addition

    def current_rejection(project, plan, payload, *args, **kwargs):
        if plan.objects:
            raise CandidateRejected(
                CandidateIssue(
                    status="blocked",
                    code="CURRENT_RULE",
                    category="constraint",
                    message="Текущее правило не подтвердило позицию",
                    zone_id="outer",
                )
            )
        return evaluate(project, plan, payload, *args, **kwargs)

    monkeypatch.setattr(app.evaluation, "preview_addition", current_rejection)
    with pytest.raises(DomainFailure) as error:
        app.preview_pattern(
            project.id,
            _spruce_request(
                placement_mode="count",
                target_count=5,
                scenario_horizon_year=4,
            ),
        )
    assert error.value.reason == FailureReason.PATTERN_SEARCH_UNCONFIRMED
    assert not app.changes._previews


def test_legacy_decreasing_saved_crown_cannot_claim_a_monotone_family():
    saved = PlanObject(
        kind="tree",
        x=15,
        y=7.5,
        radius=1.6,
        canopy_forecast=[
            GrowthEnvelopeForecast(
                horizon_year=0,
                radius_min_m=2,
                radius_max_m=3,
                confidence="low",
                basis="Некорректный старый прогноз",
            ),
            GrowthEnvelopeForecast(
                horizon_year=40,
                radius_min_m=1,
                radius_max_m=2,
                confidence="low",
                basis="Некорректный старый прогноз",
            ),
        ],
    )
    app, project = _application(box(0, 0, 30, 15), saved=[saved])
    before = app.get(project.id).model_dump()
    with pytest.raises(DomainFailure) as error:
        app.preview_pattern(project.id, _spruce_request(scenario_horizon_year=4))
    assert error.value.reason == FailureReason.PATTERN_SEARCH_UNCONFIRMED
    assert not app.changes._previews
    assert app.get(project.id).model_dump() == before


def test_unfinished_current_domain_does_not_use_a_previous_witness(monkeypatch):
    app, project = _application(box(0, 0, 30, 15))
    request = _spruce_request()
    app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 7})
    )
    prepare = app.patterns._prepare_generation
    previews_before = set(app.changes._previews)

    def unfinished(*args, **kwargs):
        generation = prepare(*args, **kwargs)
        zone = generation.zones[0]
        domain = SearchDomain(
            zone_id=zone.id,
            revision="unfinished-test",
            geometry=mapping(Polygon()),
            unresolved_geometry=zone.geometry,
            available_area_m2=0,
            excluded_area_m2=0,
            unresolved_area_m2=100,
            measured_cells=0,
            elapsed_s=0.1,
            stop_reason="probe_limit",
        )
        return replace(
            generation,
            zones=[
                zone.model_copy(
                    update={
                        "geometry": {
                            **zone.geometry,
                            "ga_search_domain": domain.model_dump(exclude={"zone_id"}),
                        }
                    }
                )
            ],
        )

    monkeypatch.setattr(app.patterns, "_prepare_generation", unfinished)
    result = app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 4})
    )
    assert result.accepted_count == 0
    assert result.change_set is None
    assert result.search_domains[0].stop_reason == "probe_limit"
    assert set(app.changes._previews) == previews_before


def test_current_geometry_failure_is_not_replaced_with_a_cached_success(monkeypatch):
    app, project = _application(box(0, 0, 30, 15))
    request = _spruce_request()
    app.preview_pattern(
        project.id, request.model_copy(update={"scenario_horizon_year": 7})
    )
    previews_before = set(app.changes._previews)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Текущий расчётный ресурс недоступен")

    monkeypatch.setattr(
        "app.planning.search.candidate_search.prepare_positions", unavailable
    )
    with pytest.raises(RuntimeError, match="Текущий расчётный ресурс недоступен"):
        app.preview_pattern(
            project.id, request.model_copy(update={"scenario_horizon_year": 4})
        )
    assert set(app.changes._previews) == previews_before
