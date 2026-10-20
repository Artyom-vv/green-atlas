"""Сценарий роста не меняет сохранённые посадки, текущую проверку или историю."""

from concurrent.futures import ThreadPoolExecutor
from math import hypot
from threading import Barrier
from typing import cast
from unittest.mock import Mock

import pytest
from application_factory import recompose_application
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_native_live_provider import setup as setup
from test_placement_allocation import application
from test_prepared_geometry import local

from app.api import router
from app.composition import get_application
from app.geometry.checking.domain import PositionAdvisory
from app.planning.changes.contracts import PlanChangeSetApplyRequest, PlanChangeSetDraft
from app.planning.contracts import PlacementCheckRequest, Plan, PlanObject
from app.planning.domain import required_spacing
from app.planning.patterns.contracts import FillPatternRequest
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.releases.service import _forecast
from app.species.catalog import forecast_at, get_species, growth_forecasts
from app.species.forecast import calculation_growth_radii
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.contracts import ValidationIssue
from app.validation.forecast import GrowthForecastApplication

TREE = "tilia-cordata@2026-08-28.1"


def planting(x: float, *, id_: str) -> PlanObject:
    canopy, roots = growth_forecasts(get_species(TREE), "standard")
    return PlanObject(
        id=id_,
        kind="tree",
        x=x,
        y=50,
        radius=1.6,
        species_revision_id=TREE,
        canopy_forecast=canopy,
        root_forecast=roots,
        planting_zone_id="west",
    )


@pytest.fixture
def scenario():
    app, project = application()
    app = recompose_application(
        app, validator=RuleBasedPlanValidator(geometry=app.geometry)
    )
    try:
        yield app, project
    finally:
        cast(SqliteProjectRepository, app.repository).close()


@pytest.mark.parametrize("value", [-1, 41, True, 4.5, "10"])
@pytest.mark.parametrize("request_type", [FillPatternRequest, PlanChangeSetDraft])
def test_scenario_contract_rejects_invalid_years(
    value: object, request_type: type
) -> None:
    fields = (
        {"zone_ids": ["west"]}
        if request_type is FillPatternRequest
        else {"label": "Сценарий", "operations": [{"type": "delete", "object_id": "a"}]}
    )
    with pytest.raises(ValidationError):
        request_type.model_validate(
            {"base_plan_version": 1, "scenario_horizon_year": value, **fields}
        )


def test_legacy_saved_horizon_never_changes_current_validation(scenario) -> None:
    app, project = scenario
    assert project.plan is not None
    project.plan.objects = [planting(50, id_="a"), planting(56, id_="b")]
    project.plan.planning_horizon_year = 40
    app.validation.refresh(project, project.plan)
    assert not any(item.code == "PLANT_SPACING" for item in project.plan.issues)
    assert project.plan.validation_basis is not None
    assert project.plan.validation_basis.planning_horizon_year == 0
    saved = app.repository.save(project)
    assert saved.plan is not None and saved.plan.planning_horizon_year == 40
    assert app.validation.is_current(saved, saved.plan)
    saved.plan.planning_horizon_year = 8
    assert app.validation.is_current(saved, saved.plan)


def test_forecast_is_read_only_and_reports_first_conflict_year(scenario) -> None:
    app, project = scenario
    assert project.plan is not None
    first, second = planting(50, id_="a"), planting(56, id_="b")
    project.plan.objects = [first, second]
    app.validation.refresh(project, project.plan)
    project = app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    actual = hypot(first.x - second.x, first.y - second.y)
    expected_first = next(
        year
        for year in range(41)
        if required_spacing(first, second, year) > actual + 1e-6
    )
    result = app.growth_forecast(project.id, 40)
    assert result.current_issues == []
    assert result.earliest_conflict_year == expected_first
    assert result.conflict_free_through_year == expected_first - 1
    assert result.forecast_unavailable_object_ids == []
    assert len(result.issues) == 1 and result.issues[0].related_object_ids == ["a", "b"]
    assert result.issues[0].code == "PLANT_SPACING"
    assert app.growth_forecast(project.id, expected_first - 1).issues == []
    assert (
        app.growth_forecast(project.id, expected_first).issues[0].id
        == result.issues[0].id
    )
    assert app.get(project.id).model_dump_json() == before
    assert app.get_plan_history(project.id).entries == []


def test_future_geometry_uses_the_same_local_growth_checker(
    scenario, monkeypatch
) -> None:
    app, project = scenario
    assert project.plan is not None
    project.plan.objects = [planting(50, id_="a")]
    app.repository.save(project)
    detail = Mock(wraps=app.geometry.future_growth_advisory_detail)
    monkeypatch.setattr(app.geometry, "future_growth_advisory_detail", detail)
    network = Mock(side_effect=AssertionError("Сценарий не повторяет сетевой паспорт"))
    monkeypatch.setattr(app.geometry, "position_rule_trace", network)
    result = app.growth_forecast(project.id, 5)
    assert result.current_issues == result.issues == []
    assert result.conflict_free_through_year == 40
    detail.assert_called()
    network.assert_not_called()
    calls = detail.call_count
    app.growth_forecast(project.id, 5)
    assert detail.call_count == calls
    changed = app.get(project.id)
    assert changed.plan is not None
    changed.plan.objects[0].x = 51
    changed.plan.version += 1
    app.repository.save(changed)
    app.growth_forecast(project.id, 5)
    assert detail.call_count > calls


def test_forecast_uses_injected_validator_and_revision_without_mutating_plan(
    scenario,
) -> None:
    template, project = scenario
    assert project.plan is not None
    project.plan.objects = [planting(50, id_="a")]
    template.repository.save(project)
    before = template.get(project.id).model_dump_json()

    class ScenarioValidator:
        revision = "injected-growth-1"

        def __init__(self) -> None:
            self.years: list[int] = []

        def validate_plan(self, project: Project, plan: Plan) -> list[ValidationIssue]:
            raise AssertionError("Прогноз использует сценарную проверку порта")

        def validate_growth_scenario(
            self, project: Project, plan: Plan, year: int
        ) -> list[ValidationIssue]:
            assert plan is not project.plan
            assert project.plan is not None
            assert plan.objects[0] is not project.plan.objects[0]
            self.years.append(year)
            plan.objects[0].status = "error"
            return [
                ValidationIssue(
                    severity="warning",
                    code="GROWTH_INJECTED",
                    title="Замечание внедрённого валидатора",
                    description=f"Год {year}",
                    object_id=plan.objects[0].id,
                )
            ]

        def discard(self, project_id: str) -> None:
            pass

    validator = ScenarioValidator()
    app = recompose_application(template, validator=validator)
    result = app.growth_forecast(project.id, 5)
    assert result.current_issues[0].code == result.issues[0].code == "GROWTH_INJECTED"
    assert validator.years == [0, 5]
    result.issues[0].title = "Изменение ответа не меняет кеш"
    repeated = app.growth_forecast(project.id, 5)
    assert repeated.issues[0].title == "Замечание внедрённого валидатора"
    assert validator.years == [0, 5]
    validator.revision = "injected-growth-2"
    ready = Barrier(4)

    def forecast(_index: int) -> list[ValidationIssue]:
        ready.wait(timeout=5)
        return app.growth_forecast(project.id, 5).issues

    with ThreadPoolExecutor(max_workers=4) as workers:
        concurrent = list(workers.map(forecast, range(4)))
    assert all(issues[0].code == "GROWTH_INJECTED" for issues in concurrent)
    assert validator.years == [0, 5, 0, 5]
    assert app.get(project.id).model_dump_json() == before


def test_missing_forecast_never_claims_future_conflict_free(scenario) -> None:
    app, project = scenario
    assert project.plan is not None
    first = planting(50, id_="a")
    first.root_forecast = []
    project.plan.objects = [first]
    app.repository.save(project)
    result = app.growth_forecast(project.id, 5)
    assert result.forecast_unavailable_object_ids == ["a"]
    assert result.conflict_free_through_year is None
    assert result.earliest_conflict_year is None
    assert {item.code for item in result.issues} == {"GROWTH_FORECAST_UNAVAILABLE"}


def test_scenario_preview_does_not_persist_horizon_when_accepted(scenario) -> None:
    app, project = scenario
    before = app.get(project.id).model_dump_json()
    request = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["west"],
        scenario_horizon_year=10,
        species_revision_id=TREE,
        size_class="standard",
        target_count=3,
        placement_mode="count",
        layout="regular",
    )
    result = app.preview_pattern(project.id, request)
    assert result.planning_horizon_year == 10
    assert result.accepted_count == 3
    assert app.get(project.id).model_dump_json() == before
    assert result.change_set is not None and result.change_set.can_apply
    applied = app.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=result.change_set.id,
            digest=result.change_set.digest,
            base_plan_version=1,
        ),
    )
    assert applied.plan.planning_horizon_year == 0
    assert applied.plan.validation_basis is not None
    assert applied.plan.validation_basis.planning_horizon_year == 0
    assert all(
        "horizon" not in key
        for item in applied.plan.objects
        for key in item.model_dump()
    )
    assert not any(item.code == "PLANT_SPACING" for item in applied.plan.issues)
    assert app.growth_forecast(project.id, 0).issues == []


def test_manual_candidate_uses_requested_scenario_without_changing_plan(
    scenario,
) -> None:
    app, project = scenario
    assert project.plan is not None
    project.plan.planning_horizon_year = 40
    project.plan.objects = [planting(50, id_="a")]
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    candidate = PlacementCheckRequest(
        kind="tree",
        x=56,
        y=50,
        radius=1.6,
        species_revision_id=TREE,
        size_class="standard",
    )
    current = app.check_placement(project.id, candidate)
    future = app.check_placement(
        project.id, candidate.model_copy(update={"scenario_horizon_year": 40})
    )
    assert current.allowed and current.planning_horizon_year == 0
    assert not future.allowed and future.planning_horizon_year == 40
    assert app.get(project.id).model_dump_json() == before


def test_forecast_api_is_read_only_and_rejects_removed_writer(scenario) -> None:
    app, project = scenario
    web = FastAPI()
    web.include_router(router)
    web.dependency_overrides[get_application] = lambda: app
    before = app.get(project.id).model_dump_json()
    with TestClient(web) as client:
        endpoint = f"/api/projects/{project.id}/plan/growth-forecast"
        response = client.get(endpoint, params={"year": 5})
        assert response.status_code == 200
        assert response.json()["year"] == 5
        for year in (-1, 41, "1.5"):
            assert client.get(endpoint, params={"year": year}).status_code == 422
        assert client.put(
            f"/api/projects/{project.id}/plan/planning-horizon",
            json={"planning_horizon_year": 40, "base_plan_version": 1},
        ).status_code in (404, 405)
    assert app.get(project.id).model_dump_json() == before


def test_forecast_does_not_repeat_source_warnings_as_growth_conflicts(
    scenario, monkeypatch
) -> None:
    app, project = scenario
    assert project.plan is not None
    project.plan.objects = [planting(50, id_="a")]
    app.repository.save(project)
    monkeypatch.setattr(
        app.geometry,
        "future_growth_advisory_detail",
        lambda *args: PositionAdvisory(
            code="SOURCE_GEOMETRY_PARTIAL",
            title="Пропуск источника",
            description="Подоснова",
            suggested_action="Проверить источник",
        ),
    )
    result = app.growth_forecast(project.id, 5)
    assert result.current_issues == result.issues == []
    assert result.earliest_conflict_year is None


@pytest.mark.parametrize("code", ["NATIVE_LOCAL_UNKNOWN", "UNTYPED_UTILITY_REVIEW"])
def test_addressed_uncertainty_is_retained_without_claiming_conflict_free(
    scenario, monkeypatch, code: str
) -> None:
    app, project = scenario
    assert project.plan is not None
    project.plan.objects = [planting(50, id_="a")]
    advisory = PositionAdvisory(
        code=code,
        title="Нужна проверка позиции",
        description="Не подтверждена ближайшая геометрия",
        suggested_action="Проверить объект исходника",
        source_layer="Сеть",
        source_feature_ids=("route-1",),
    )
    monkeypatch.setattr(
        app.geometry, "future_growth_advisory_detail", lambda *args: advisory
    )
    monkeypatch.setattr(
        app.geometry, "placement_advisory_detail", lambda *args: advisory
    )
    project.plan.issues = RuleBasedPlanValidator(geometry=app.geometry).validate_plan(
        project, project.plan
    )
    current_id = next(
        item.id
        for item in project.plan.issues
        if item.code == code and item.rule_id == "untyped_utility"
    )
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    result = app.growth_forecast(project.id, 5)
    assert result.unverified_object_ids == ["a"]
    assert result.forecast_unavailable_object_ids == []
    assert result.earliest_conflict_year is None
    assert result.conflict_free_through_year is None
    assert result.issues[0].code == code
    assert result.current_issues[0].id == result.issues[0].id == current_id
    assert app.get(project.id).model_dump_json() == before


def test_future_only_addressed_uncertainty_prevents_a_timeline_safety_claim(
    scenario, monkeypatch
) -> None:
    app, project = scenario
    assert project.plan is not None
    project.plan.objects = [planting(50, id_="a")]
    app.repository.save(project)

    def future_unknown(project, x, y, canopy, roots, kind):
        if canopy < 3:
            return None
        return PositionAdvisory(
            code="NATIVE_LOCAL_UNKNOWN",
            title="Нужна проверка позиции",
            description="Не подтверждён объект внутри будущей кроны",
            suggested_action="Проверить объект исходника",
            source_layer="Край",
            source_feature_ids=("route-2",),
        )

    monkeypatch.setattr(app.geometry, "future_growth_advisory_detail", future_unknown)
    result = app.growth_forecast(project.id, 0)
    assert result.current_issues == result.issues == []
    assert result.unverified_object_ids == ["a"]
    assert result.earliest_conflict_year is None
    assert result.conflict_free_through_year is None


def test_forecast_geometry_failure_never_reuses_a_different_basis(
    scenario, monkeypatch
) -> None:
    app, project = scenario
    assert project.plan is not None
    project.plan.objects = [planting(50, id_="a")]
    app.repository.save(project)
    app.growth_forecast(project.id, 5)
    changed = app.get(project.id)
    changed.geometry_version += 1
    app.repository.save(changed)
    monkeypatch.setattr(
        app.geometry,
        "future_growth_advisory_detail",
        Mock(side_effect=RuntimeError("Расчётное основание недоступно")),
    )
    with pytest.raises(RuntimeError, match="Расчётное основание"):
        app.growth_forecast(project.id, 5)


def test_prepared_native_forecast_reads_metadata_without_hydrating_cad(
    scenario, setup, monkeypatch
) -> None:
    app, _ = scenario
    engine, client, project = local(setup)
    assert project.plan is not None
    project.plan.objects = [planting(-50, id_="a"), planting(-44, id_="b")]
    app.repository.create(project)
    getter = app.repository.get

    def compact_only(project_id: str, *, lightweight: bool = False):
        assert lightweight, "Год сценария не должен перечитывать CAD-граф"
        return getter(project_id, lightweight=True)

    monkeypatch.setattr(app.repository, "get", compact_only)
    client.inspect = Mock(side_effect=AssertionError("AutoCAD не нужен для прогноза"))
    forecast = GrowthForecastApplication(
        app.repository,
        RuleBasedPlanValidator(geometry=engine),
        basis_reader=engine.validation_input_basis,
    )
    before = getter(project.id).model_dump_json()
    result = forecast.forecast(project.id, 40)
    assert result.current_issues == []
    assert result.earliest_conflict_year is not None
    assert any(item.code == "PLANT_SPACING" for item in result.issues)
    assert getter(project.id).model_dump_json() == before
    client.inspect.assert_not_called()


def test_forecast_at_interpolates_arbitrary_year_between_catalogue_anchors() -> None:
    species = get_species("tilia-cordata@2026-08-28.1")

    forecast = forecast_at(species.canopy_forecast, 23)

    assert forecast is not None
    assert forecast.horizon_year == 23
    assert forecast.radius_min_m == 3.423
    assert forecast.radius_max_m == 7.0
    assert forecast.confidence == "low"


def test_forecast_at_does_not_extrapolate_beyond_catalogue_anchors() -> None:
    species = get_species("tilia-cordata@2026-08-28.1")

    assert forecast_at(species.canopy_forecast, -1) is None
    assert forecast_at(species.canopy_forecast, 41) is None


def test_release_forecast_uses_the_same_interpolation_at_arbitrary_year() -> None:
    species = get_species("tilia-cordata@2026-08-28.1")

    assert _forecast(species.canopy_forecast, 23) == ("3.423", "7.0")


def test_larch_calculation_uses_lower_roots_without_changing_biological_range() -> None:
    species = get_species("larix-decidua@2026-09-17.1")
    canopy, roots = growth_forecasts(species, "large")
    canopy_at_horizon = forecast_at(canopy, 20)
    roots_at_horizon = forecast_at(roots, 20)
    assert canopy_at_horizon is not None and roots_at_horizon is not None
    before = roots_at_horizon.model_dump()

    assert calculation_growth_radii(canopy_at_horizon, roots_at_horizon) == (4.57, 1.68)
    assert roots_at_horizon.radius_max_m == 6.86
    assert roots_at_horizon.model_dump() == before
    assert _forecast(roots, 20) == ("1.68", "6.86")
