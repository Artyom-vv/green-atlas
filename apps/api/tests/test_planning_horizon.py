"""Общие прогнозные габариты сценария и защита локального расчётного основания."""

from typing import cast
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from shapely.geometry import LineString, Point, box, mapping, shape
from test_native_live_provider import StubNative, rectangle
from test_native_live_provider import setup as setup
from test_network_rules import network_project
from test_placement_allocation import application
from test_prepared_geometry import local

from app.geometry.checking.domain import PositionChecker
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.preparation import preparation_identity
from app.planning.contracts import (
    Plan,
    PlanObject,
    PlanObjectCreate,
    PlanObjectUpdate,
    PlanPlanningHorizonUpdate,
)
from app.planning.domain import PlantSpacingIndex, required_spacing
from app.planning.patterns.contracts import FillPatternRequest
from app.planning.rules import growth_radii
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.species.catalog import get_species, growth_forecasts
from app.species.forecast import forecast_at
from app.validation.adapters import RuleBasedPlanValidator

TREE = "tilia-cordata@2026-08-28.1"
LARCH = "larix-decidua@2026-09-17.1"


def tree(x: float) -> PlanObject:
    canopy, roots = growth_forecasts(get_species(TREE), "standard")
    return PlanObject(
        kind="tree",
        x=x,
        y=50,
        radius=1.6,
        species_revision_id=TREE,
        canopy_forecast=canopy,
        root_forecast=roots,
        planting_zone_id="west",
    )


@pytest.mark.parametrize("value", [-1, 41, True, 4.5, "10"])
def test_horizon_contract_rejects_out_of_range_and_non_integer_values(
    value: object,
) -> None:
    with pytest.raises(ValidationError):
        PlanPlanningHorizonUpdate.model_validate(
            {"planning_horizon_year": value, "base_plan_version": 1}
        )


def test_default_interpolation_and_minimum_spacing_share_one_horizon() -> None:
    assert Plan().planning_horizon_year == 0
    first, second = tree(50), tree(56)
    assert forecast_at(first.canopy_forecast, 12) is not None
    assert required_spacing(first, second, 0) >= 4.8
    assert required_spacing(first, second, 0) < required_spacing(first, second, 40)
    assert PlantSpacingIndex([first], planning_horizon_year=0).respects(second)
    assert not PlantSpacingIndex([first], planning_horizon_year=40).respects(second)
    short_radii = growth_radii([TREE], "standard", 0)
    long_radii = growth_radii([TREE], "standard", 40)
    assert short_radii is not None and long_radii is not None
    assert short_radii < long_radii


def test_larch_root_bound_matches_manual_pattern_update_and_revalidation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = application()
    plan = cast(Plan, project.plan)
    plan.planning_horizon_year = 20
    detail = Mock(wraps=app.geometry.future_growth_advisory_detail)
    update = Mock(wraps=app.geometry.future_growth_advisory)
    automatic = Mock(wraps=app.geometry.automatic_safe_geometry)
    monkeypatch.setattr(app.geometry, "future_growth_advisory_detail", detail)
    monkeypatch.setattr(app.geometry, "future_growth_advisory", update)
    monkeypatch.setattr(app.geometry, "automatic_safe_geometry", automatic)
    app.validation.validator = RuleBasedPlanValidator(geometry=app.geometry)
    expected = (4.57, 1.68)
    try:
        candidate, _ = app.evaluation.preview_addition(
            project,
            plan,
            PlanObjectCreate(
                kind="tree", x=50, y=50, size_class="large", species_revision_id=LARCH
            ),
        )
        assert detail.call_args.args[3:5] == expected
        assert candidate.root_forecast[-1].radius_max_m == 6.86
        plan.objects = [candidate]
        app.evaluation.preview_update(
            project, plan, candidate.id, PlanObjectUpdate(x=52)
        )
        assert update.call_args.args[3:5] == expected
        app.validation.refresh(project, plan)
        assert detail.call_args.args[3:5] == growth_radii([LARCH], "large", 0)

        result = app.preview_pattern(
            project.id,
            FillPatternRequest(
                scenario_horizon_year=20,
                base_plan_version=1,
                zone_ids=["west"],
                species_revision_id=LARCH,
                size_class="large",
                target_count=1,
                placement_mode="count",
                layout="regular",
            ),
        )
        assert result.accepted_count == 1
        assert (
            automatic.call_args.kwargs["growth_canopy_radius"],
            automatic.call_args.kwargs["growth_root_radius"],
        ) == expected
        assert result.change_set is not None
        assert result.change_set.additions[0].root_forecast == candidate.root_forecast
        assert app.get(project.id).plan is not None
        assert cast(Plan, app.get(project.id).plan).objects == []
    finally:
        cast(SqliteProjectRepository, app.repository).close()


def test_lower_root_bound_keeps_buildings_roads_and_network_minimum_excluded() -> None:
    project = network_project(("power_cable", box(70, -10, 71, 110), "outer_surface"))
    assert project.geometry is not None
    area = mapping(box(0, 0, 100, 100))
    project.geometry.feature_collection["features"].extend(
        [
            {
                "type": "Feature",
                "properties": {"kind": "site_border"},
                "geometry": area,
            },
            {
                "type": "Feature",
                "properties": {"kind": "building"},
                "geometry": mapping(box(40, 40, 60, 60)),
            },
            {
                "type": "Feature",
                "properties": {"kind": "road"},
                "geometry": mapping(LineString([(0, 20), (100, 20)])),
            },
        ]
    )
    checker = PositionChecker(project)
    radii = growth_radii([LARCH], "large", 20)
    assert radii == (4.57, 1.68)
    safe = checker.automatic_safe_area(shape(area), 1.6, "tree", *radii)
    assert safe.covers(Point(10, 50))
    for point in (Point(50, 50), Point(30, 20), Point(68.01, 50)):
        assert not safe.covers(point)
        violation = checker.check(point.x, point.y, 1.6, "tree")
        assert violation is not None
    network_violation = checker.check(68.01, 50, 1.6, "tree")
    assert network_violation is not None
    assert network_violation.required == 2


def test_root_policy_rebinds_calculation_without_preparing_cad_or_changing_plan(
    setup: tuple[LiveNativeGeometryEngine, StubNative, Project],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, client, project = local(setup)
    client.inspect = Mock(side_effect=AssertionError("Новая подготовка CAD не нужна"))
    current = preparation_identity.CALCULATION_POLICY_REVISION
    monkeypatch.setattr(
        preparation_identity, "CALCULATION_POLICY_REVISION", "autocad-local-check/2"
    )
    before = engine.validation_input_basis(project)
    work = rectangle(-70, -70, 70, 70)
    radii = growth_radii([LARCH], "large", 20)
    assert radii is not None
    engine.automatic_safe_geometry(project, work, 1.6, "tree", *radii)
    old_domain = engine._hybrid
    assert old_domain is not None
    old_keys = set(old_domain.jobs)
    geometry, snapshot, plan = (
        engine.projection,
        engine.snapshot,
        project.plan.model_dump_json() if project.plan else None,
    )

    monkeypatch.setattr(preparation_identity, "CALCULATION_POLICY_REVISION", current)
    after = engine.validation_input_basis(project)
    assert before.geometry_digest == after.geometry_digest
    assert before.evidence_digest == after.evidence_digest
    assert before.calculation_digest != after.calculation_digest
    assert engine._hybrid is None
    engine.automatic_safe_geometry(project, work, 1.6, "tree", *radii)
    assert engine._hybrid is not None
    assert not old_keys.intersection(engine._hybrid.jobs)
    assert engine.projection is geometry and engine.snapshot is snapshot
    assert project.plan is not None and project.plan.model_dump_json() == plan
    client.inspect.assert_not_called()


def test_prior_root_validation_is_stale_without_rewriting_the_saved_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = application()
    plan = cast(Plan, project.plan)
    current = app.validation.validator.revision
    try:
        monkeypatch.setattr(
            app.validation.validator,
            "revision",
            "rule-based-plan-v6-project-planning-horizon",
        )
        app.validation.refresh(project, plan)
        project = app.repository.save(project)
        before = project.model_dump_json()
        monkeypatch.setattr(app.validation.validator, "revision", current)

        assert not app.validation.is_current(project, cast(Plan, project.plan))
        assert project.model_dump_json() == before
        assert app.get(project.id).model_dump_json() == before
    finally:
        cast(SqliteProjectRepository, app.repository).close()
