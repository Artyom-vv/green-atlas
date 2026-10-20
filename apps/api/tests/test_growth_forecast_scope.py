"""Growth scenarios share one checked source and publish cache after scope exit."""

from collections.abc import Iterator
from contextlib import contextmanager
from copy import deepcopy
from functools import partial
from unittest.mock import Mock

import pytest
from test_geometry_preparation import geometry_fake, project_with

from app.geometry.preparation import calculation_scope
from app.native_query.preparation.prepared_snapshot import PreparedGeometryUnavailable
from app.operations.progress import OperationCancelled
from app.species.catalog import get_species, growth_forecasts
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.contracts import CalculationInputBasis
from app.validation.forecast import GrowthForecastApplication


class ForecastGeometry:
    def __init__(self):
        self.geometry = geometry_fake()
        self.depth = self.entries = 0
        self.enter_error = self.body_error = self.exit_error = None
        for name in ("prepare_positions", "future_growth_advisory_detail"):
            method = getattr(self.geometry, name)
            original = method.side_effect

            def inside(*args, _original=original, **kwargs):
                assert self.depth == 1
                if self.body_error:
                    raise self.body_error
                return _original(*args, **kwargs)

            method.side_effect = inside

    def __getattr__(self, name):
        return getattr(self.geometry, name)

    @contextmanager
    def calculation_scope(self, project) -> Iterator[None]:
        assert self.depth == 0
        self.entries += 1
        if self.enter_error:
            raise self.enter_error
        self.depth = 1
        try:
            yield
            if self.exit_error:
                raise self.exit_error
        finally:
            self.depth = 0


def forecast_application(*, explicit_factory: bool = True):
    project = project_with((10, 5), (50, 5))
    assert project.plan is not None
    canopy, roots = growth_forecasts(
        get_species("tilia-cordata@2026-08-28.1"), "standard"
    )
    for obj in project.plan.objects:
        obj.species_revision_id = "tilia-cordata@2026-08-28.1"
        obj.canopy_forecast, obj.root_forecast = canopy, roots
    geometry = ForecastGeometry()
    validator = RuleBasedPlanValidator(geometry=geometry)
    repository = Mock()
    repository.get.return_value = project
    basis_reads = []

    def basis(current):
        assert current is project
        assert geometry.depth == 1
        basis_reads.append(geometry.entries)
        return CalculationInputBasis(
            geometry_digest="a" * 64,
            calculation_digest="b" * 64,
            evidence_digest="c" * 64,
        )

    kwargs = (
        {"calculation_scope_factory": partial(calculation_scope, geometry)}
        if explicit_factory
        else {}
    )
    forecast = GrowthForecastApplication(
        repository, validator, basis_reader=basis, **kwargs
    )
    return forecast, validator, geometry, project, basis_reads


@pytest.mark.parametrize("explicit_factory", [False, True])
def test_cold_all_years_and_warm_selected_year_use_one_checked_scope(
    explicit_factory: bool,
) -> None:
    forecast, _, geometry, project, basis_reads = forecast_application(
        explicit_factory=explicit_factory
    )
    before = project.model_dump_json()
    first = forecast.forecast(project.id, 20)
    assert geometry.entries == 1 and geometry.depth == 0
    assert basis_reads == [1]
    # No conflicts: the summary checks every year 0..40 and the selected year.
    assert geometry.geometry.prepare_positions.call_count == 42
    assert geometry.geometry.future_growth_advisory_detail.call_count == 84
    assert first.earliest_conflict_year is None
    assert first.conflict_free_through_year == 40
    assert forecast.forecast(project.id, 20) == first
    assert geometry.entries == 2 and basis_reads == [1, 2]
    assert geometry.geometry.prepare_positions.call_count == 42
    forecast.forecast(project.id, 21)
    assert geometry.entries == 3 and geometry.depth == 0
    assert geometry.geometry.prepare_positions.call_count == 43
    assert project.model_dump_json() == before


def test_standalone_growth_validator_holds_one_scope_for_every_planting() -> None:
    _, validator, geometry, project, _ = forecast_application()
    assert project.plan is not None
    plan = project.plan.model_copy(deep=True)
    assert validator.validate_growth_scenario(project, plan, 20) == []
    assert geometry.entries == 1 and geometry.depth == 0
    assert geometry.geometry.prepare_positions.call_count == 1
    assert geometry.geometry.future_growth_advisory_detail.call_count == 2


@pytest.mark.parametrize("warm", [False, True])
@pytest.mark.parametrize("stage", ["enter", "body", "exit"])
@pytest.mark.parametrize(
    "error_type", [PreparedGeometryUnavailable, OperationCancelled]
)
def test_failed_forecast_never_mutates_or_publishes_cache_even_when_warm(
    warm: bool, stage: str, error_type: type[Exception]
) -> None:
    forecast, _, geometry, project, _ = forecast_application()
    if warm:
        forecast.forecast(project.id, 20)
        forecast.forecast(project.id, 21)
    before = deepcopy(forecast._cache)
    source_before = project.model_dump_json()
    failure = error_type(
        "corrupt" if error_type is PreparedGeometryUnavailable else "cancel"
    )
    setattr(geometry, f"{stage}_error", failure)
    with pytest.raises(error_type) as caught:
        forecast.forecast(project.id, 22)
    assert caught.value is failure
    assert forecast._cache == before
    assert project.model_dump_json() == source_before
    assert geometry.depth == 0
    geometry.enter_error = geometry.body_error = geometry.exit_error = None
    result = forecast.forecast(project.id, 22)
    assert result.year == 22
    assert list(forecast._cache[project.id].years)[-1] == 22
    assert geometry.depth == 0


def test_failed_warm_cache_hit_does_not_reorder_years_or_projects() -> None:
    forecast, _, geometry, project, _ = forecast_application()
    forecast.forecast(project.id, 20)
    forecast.forecast(project.id, 21)
    other = project.model_copy(deep=True, update={"id": "another-project"})
    projects = {project.id: project, other.id: other}
    forecast.repository.get.side_effect = lambda identifier, **kwargs: projects[
        identifier
    ]

    def basis(current):
        assert geometry.depth == 1
        return CalculationInputBasis(
            geometry_digest="a" * 64,
            calculation_digest="b" * 64,
            evidence_digest="c" * 64,
        )

    forecast.basis_reader = basis
    forecast.forecast(other.id, 20)
    assert list(forecast._cache) == [project.id, other.id]
    before = deepcopy(forecast._cache)
    failure = PreparedGeometryUnavailable("corrupt")
    geometry.exit_error = failure
    with pytest.raises(PreparedGeometryUnavailable):
        forecast.forecast(project.id, 20)
    assert forecast._cache == before
    assert list(forecast._cache[project.id].years) == [20, 21]
    assert list(forecast._cache) == [project.id, other.id]
    geometry.exit_error = None
    forecast.forecast(project.id, 20)
    assert list(forecast._cache[project.id].years) == [21, 20]
    assert list(forecast._cache) == [other.id, project.id]
