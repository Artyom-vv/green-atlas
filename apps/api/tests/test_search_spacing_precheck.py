"""Доказанный отказ по соседям не расходует CAD-проверки и не скрывает замечания."""

from contextlib import contextmanager
from itertools import count
from math import inf, nextafter
from unittest.mock import Mock

import pytest

from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.checking.domain import PositionAdvisory, PositionViolation
from app.planning.changes.contracts import PlanObjectAddOperation
from app.planning.changes.evaluation import PlanEvaluation
from app.planning.contracts import Plan, PlanObject, PlanObjectCreate
from app.planning.domain import required_spacing, respects_plant_spacing
from app.planning.search.candidate_search import SEARCH_TIME_LIMIT_S, search_candidates
from app.planning.search.spacing_precheck import SavedPlanSpacingPrecheck
from app.projects.contracts import Project
from app.regulations.network_summary import network_review_reason
from app.regulations.trace_contracts import (
    PlantingRuleTrace,
    RuleTraceBasis,
    RuleTraceEntry,
)


class SearchGeometry(ShapelyGeometryEngine):
    def __init__(self, bound=frozenset(), traces=(frozenset(),)):
        super().__init__()
        self.bound, self.traces = bound, traces
        self.geometry_checks = self.trace_calls = self.entered = self.exited = 0
        self.exit_error = None
        self.violation = None
        self.unknown_x = None

    def search_trace_assumption_codes(self, project, plant_kinds):
        return self.bound

    @contextmanager
    def calculation_scope(self, project):
        self.entered += 1
        try:
            yield
        finally:
            self.exited += 1
            if self.exit_error:
                raise self.exit_error

    def position_violation(self, project, x, y, radius, plant_kind="tree"):
        self.geometry_checks += 1
        return self.violation

    def placement_advisory_detail(self, project, x, y, radius, plant_kind="tree"):
        if x == self.unknown_x:
            return PositionAdvisory(
                "NATIVE_LOCAL_UNKNOWN", "Проверка", "Неизвестно", None
            )
        return None

    def future_growth_advisory_detail(self, *args):
        return None

    def position_rule_trace(
        self, project, x, y, plant_kind="tree", mature_crown_diameter_m=None
    ):
        codes = self.traces[min(self.trace_calls, len(self.traces) - 1)]
        self.trace_calls += 1
        if codes is None:
            return None
        return PlantingRuleTrace(
            basis=RuleTraceBasis(
                project_id=project.id,
                state_version=project.state_version,
                geometry_version=project.geometry_version,
                plan_version=project.plan.version,
                requirement_profile="test",
                registry_revision="test",
            ),
            x=x,
            y=y,
            plant_kind=plant_kind,
            entries=[
                RuleTraceEntry(
                    obstacle_kind="utility", status="not_checked", code=code, note=code
                )
                for code in sorted(codes)
            ],
        )


def project_with_saved():
    return Project(
        name="Сохранённые посадки",
        plan=Plan(
            objects=[
                PlanObject(
                    id="saved",
                    kind="tree",
                    x=30,
                    y=50,
                    radius=1.6,
                    group_ids=["old"],
                    spacing_policy="balanced",
                )
            ]
        ),
    )


def operation(x, *, kind="tree", species=None, groups=("new",)):
    return PlanObjectAddOperation(
        object=PlanObjectCreate(
            kind=kind,
            x=x,
            y=50,
            species_revision_id=species,
            size_class="standard",
            group_ids=list(groups),
            spacing_policy="balanced",
        )
    )


def evaluate(project, geometry, operations, *, target=40):
    ids = count()
    evaluation = PlanEvaluation(geometry, lambda: f"candidate-{next(ids)}")
    return search_candidates(project, operations, evaluation, target, {}, 3.5)


@pytest.mark.parametrize("horizon", [0, 20, 40])
@pytest.mark.parametrize(
    "kind,species",
    [
        ("tree", "sorbus-aucuparia@2026-08-28.1"),
        ("shrub", "spiraea-japonica@2026-08-28.1"),
    ],
)
@pytest.mark.parametrize("same_group", [False, True])
def test_precheck_matches_canonical_plant_and_exact_spacing_boundary(
    horizon, kind, species, same_group
):
    saved = PlanObject(
        id="saved",
        kind="tree",
        x=0,
        y=0,
        radius=1.6,
        group_ids=["old"],
        spacing_policy="balanced",
    )
    project = Project(
        name="Граница расстояния",
        plan=Plan(objects=[saved], planning_horizon_year=horizon),
    )
    payload = PlanObjectCreate(
        kind=kind,
        x=0,
        y=0,
        species_revision_id=species,
        size_class="standard",
        group_ids=["old" if same_group else "new"],
        spacing_policy="balanced",
    )
    canonical, _ = PlanEvaluation(
        SearchGeometry(), lambda: "canonical"
    ).preview_addition(project, Plan(planning_horizon_year=horizon), payload)
    distance = required_spacing(canonical, saved, horizon)
    check = SavedPlanSpacingPrecheck(project)
    before = project.model_dump(mode="json")
    for x in [nextafter(distance, -inf), distance, nextafter(distance, inf)]:
        candidate = canonical.model_copy(update={"x": x})
        actual = check.violates(payload.model_copy(update={"x": x}))
        assert actual is not respects_plant_spacing(
            candidate, project.plan.objects, planning_horizon_year=horizon
        )
        assert actual is (x < distance)
    assert project.model_dump(mode="json") == before
    assert len(check.templates) == 1


def test_large_radius_and_mixed_kinds_are_not_approximated_by_one_template():
    project = project_with_saved()
    check = SavedPlanSpacingPrecheck(project)
    assert check.violates(operation(34).object)
    assert not check.violates(operation(34, kind="shrub").object)
    assert check.violates(
        operation(50).object.model_copy(update={"layout_radius_m": 25})
    )
    assert len(check.templates) == 3


def test_all_proven_saved_collisions_skip_measurement_trace_and_keep_every_index(
    monkeypatch,
):
    project, geometry = project_with_saved(), SearchGeometry()
    before = project.model_dump(mode="json")
    prepare = Mock(side_effect=AssertionError("Не нужны измерения доказанного отказа"))
    monkeypatch.setattr(
        "app.planning.search.candidate_search.prepare_positions", prepare
    )
    result = evaluate(project, geometry, [operation(30)] * 200)
    assert result.saved_spacing_pruned == len(result.candidate_results) == 200
    assert (
        result.saved_spacing_full_traces
        == geometry.trace_calls
        == geometry.geometry_checks
        == 0
    )
    assert result.stop_reason == "candidate_limit"
    assert [r.operation_index for r in result.candidate_results] == list(range(200))
    assert all(
        r.code == "PLANT_SPACING"
        and r.rule_id == "group-spacing"
        and r.rule_trace is None
        for r in result.candidate_results
    )
    assert geometry.entered == geometry.exited == 1
    assert project.model_dump(mode="json") == before
    assert SEARCH_TIME_LIMIT_S == 45


def test_trace_summary_is_observed_until_every_possible_code_has_been_found(
    monkeypatch,
):
    project = project_with_saved()
    geometry = SearchGeometry(
        frozenset({"A", "B"}), (frozenset({"A"}), frozenset({"A"}), frozenset({"B"}))
    )
    prepare = Mock()
    monkeypatch.setattr(
        "app.planning.search.candidate_search.prepare_positions", prepare
    )
    operations = [operation(30 + i / 10) for i in range(12)]
    result = evaluate(project, geometry, operations)
    assert result.saved_spacing_pruned == 12
    assert result.saved_spacing_full_traces == geometry.trace_calls == 3
    prepare.assert_not_called()
    legacy = SearchGeometry(
        None, (frozenset({"A"}), frozenset({"A"}), frozenset({"B"}))
    )
    previous = evaluate(project, legacy, operations)
    assert network_review_reason(
        r.rule_trace for r in result.candidate_results
    ) == network_review_reason(r.rule_trace for r in previous.candidate_results)
    assert [r.model_dump(exclude={"rule_trace"}) for r in result.candidate_results] == [
        r.model_dump(exclude={"rule_trace"}) for r in previous.candidate_results
    ]


def test_possible_codes_are_not_published_without_an_actual_trace():
    geometry = SearchGeometry(frozenset({"POSSIBLE_ONLY"}), (None,))
    result = evaluate(project_with_saved(), geometry, [operation(30)] * 5)
    assert geometry.trace_calls == result.saved_spacing_full_traces == 5
    assert network_review_reason(r.rule_trace for r in result.candidate_results) is None


def test_supported_and_legacy_providers_keep_accepted_results_and_traces_equal(
    monkeypatch,
):
    project = project_with_saved()
    operations = [operation(30), operation(31), operation(70), operation(90)]
    previous_geometry = SearchGeometry(None)
    previous = evaluate(project, previous_geometry, operations, target=2)
    geometry = SearchGeometry()
    prepare = Mock()
    monkeypatch.setattr(
        "app.planning.search.candidate_search.prepare_positions", prepare
    )
    actual = evaluate(project, geometry, operations, target=2)

    def accepted(value):
        return [
            r.model_dump(mode="json", exclude={"object_id"})
            for r in value.candidate_results
            if r.status == "allowed"
        ]

    assert accepted(actual) == accepted(previous)
    assert geometry.geometry_checks == geometry.trace_calls == 2
    assert actual.stop_reason == previous.stop_reason == "target_reached"
    assert {point for call in prepare.call_args_list for point in call.args[2]} == {
        (70, 50),
        (90, 50),
    }


def test_unknown_probe_does_not_reserve_its_neighbour_on_the_early_path():
    geometry = SearchGeometry()
    geometry.unknown_x = 71
    result = evaluate(
        project_with_saved(),
        geometry,
        [operation(30), operation(70), operation(71)],
        target=1,
    )
    assert [(r.operation_index, r.status) for r in result.candidate_results] == [
        (0, "blocked"),
        (2, "unknown"),
        (1, "allowed"),
    ]
    assert result.spacing_pruned == 0


def test_scope_failure_is_preserved_even_if_every_candidate_is_rejected_cheaply():
    project, geometry = project_with_saved(), SearchGeometry()
    geometry.exit_error = ValueError("Снимок изменился")
    before = project.model_dump(mode="json")
    with pytest.raises(ValueError, match="Снимок изменился"):
        evaluate(project, geometry, [operation(30)] * 10)
    assert project.model_dump(mode="json") == before
    assert geometry.entered == geometry.exited == 1


def test_invalid_species_keeps_the_original_geometry_first_failure():
    project, geometry = project_with_saved(), SearchGeometry()
    geometry.violation = PositionViolation(
        "TEST_BLOCKED", "Здание", "В здании", "building", None, None, None
    )
    result = evaluate(project, geometry, [operation(30, species="missing@revision")])
    assert result.saved_spacing_pruned == 0
    assert result.candidate_results[0].code == "TEST_BLOCKED"
