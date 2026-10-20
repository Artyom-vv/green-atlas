"""Просмотр сценария роста через общий локальный проверяющий модуль.

Текущие и будущие конфликты вычисляются из одного снимка. Ограниченный кэш хранит
только замечания и временную сводку, не копии CAD-графа или сохранённый горизонт.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, replace
from hashlib import sha256
from threading import RLock

from app.geometry.checking.checker_basis import checker_basis
from app.planning.config import MAX_PLANNING_HORIZON_YEAR, PlanningHorizonYear
from app.projects.contracts import Project
from app.projects.ports import ProjectReader
from app.shared.failures import DomainFailure, FailureReason
from app.species.forecast import forecast_at
from app.validation.contracts import (
    GROWTH_UNVERIFIED_CODES,
    CalculationInputBasis,
    GrowthForecastResponse,
    ValidationIssue,
)
from app.validation.ports import PlanValidatorPort

MAX_FORECAST_PROJECTS = 2
MAX_FORECAST_YEARS = 4


@dataclass
class _ForecastSummary:
    basis: str
    earliest_conflict_year: int | None
    unavailable_ids: list[str]
    unverified_ids: list[str]
    current_issues: list[ValidationIssue]
    years: OrderedDict[int, list[ValidationIssue]]


class GrowthForecastApplication:
    def __init__(
        self,
        repository: ProjectReader,
        validator: PlanValidatorPort,
        *,
        basis_reader: Callable[[Project], CalculationInputBasis | None] | None = None,
        calculation_scope_factory: Callable[[Project], AbstractContextManager[None]]
        | None = None,
    ) -> None:
        self.repository = repository
        self.validator = validator
        self.basis_reader = basis_reader
        self._scope_factory = calculation_scope_factory
        self._cache: OrderedDict[str, _ForecastSummary] = OrderedDict()
        self._lock = RLock()

    def _calculation_scope(self, project: Project) -> AbstractContextManager[None]:
        if self._scope_factory is not None:
            return self._scope_factory(project)
        method = getattr(type(self.validator), "calculation_scope", None)
        return method(self.validator, project) if callable(method) else nullcontext()

    def _basis(self, project: Project) -> str:
        assert project.plan is not None
        basis = self.basis_reader(project) if self.basis_reader else None
        value = [
            self.validator.revision,
            project.plan.id,
            project.plan.version,
            project.geometry_version,
            checker_basis(project),
            basis.model_dump(mode="json") if basis else None,
            [
                item.model_dump(mode="json", exclude={"status"})
                for item in sorted(project.plan.objects, key=lambda item: item.id)
            ],
        ]
        return sha256(
            json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    def _issues(self, project: Project, year: int) -> list[ValidationIssue]:
        assert project.plan is not None
        # Общий проверяющий модуль назначает статусы; они относятся лишь к этой копии.
        plan = project.plan.model_copy(deep=True)
        return self.validator.validate_growth_scenario(project, plan, year)

    def _summary(self, project: Project, basis: str) -> _ForecastSummary:
        assert project.plan is not None
        unavailable = [
            item.id
            for item in project.plan.objects
            if any(
                forecast_at(values, year) is None
                for values in (item.canopy_forecast, item.root_forecast)
                for year in (0, MAX_PLANNING_HORIZON_YEAR)
            )
        ]
        current = self._issues(project, 0)
        earliest: int | None = None
        unverified: set[str] = set()
        if project.plan.objects:
            for year in range(MAX_PLANNING_HORIZON_YEAR + 1):
                issues = current if year == 0 else self._issues(project, year)
                unverified.update(self._unverified_ids(issues))
                if any(
                    item.code != "GROWTH_FORECAST_UNAVAILABLE"
                    and (
                        item.code == "PLANT_SPACING"
                        or item.code.startswith(
                            ("GROWTH_", "ROOT_", "CANOPY_", "CROWN_")
                        )
                    )
                    for item in issues
                ):
                    earliest = year
                    break
        return _ForecastSummary(
            basis,
            earliest,
            sorted(unavailable),
            sorted(unverified),
            current,
            OrderedDict(),
        )

    @staticmethod
    def _unverified_ids(issues: list[ValidationIssue]) -> set[str]:
        return {
            item.object_id
            for item in issues
            if item.code in GROWTH_UNVERIFIED_CODES and item.object_id is not None
        }

    def forecast(
        self, project_id: str, year: PlanningHorizonYear
    ) -> GrowthForecastResponse:
        # Подготовленный снимок AutoCAD читает сам геометрический провайдер.
        # Год просмотра не требует повторного чтения всего CAD-графа из SQLite.
        project = self.repository.get(project_id, lightweight=True)
        if project.source_file is None or project.source_file.native_session is None:
            project = self.repository.get(project_id)
        if project.plan is None:
            raise DomainFailure(FailureReason.PLAN_MISSING)
        with self._lock:
            # Все годы и выбранный сценарий используют одно проверенное основание.
            # Кеш публикуется только после успешной проверки при выходе из области.
            with self._calculation_scope(project):
                basis = self._basis(project)
                previous = self._cache.get(project.id)
                cached = (
                    self._summary(project, basis)
                    if previous is None or previous.basis != basis
                    else replace(previous, years=OrderedDict(previous.years))
                )
                selected = (
                    cached.current_issues if year == 0 else cached.years.get(year)
                )
                if selected is None:
                    selected = self._issues(project, year)
                    cached.years[year] = selected
                if year in cached.years:
                    cached.years.move_to_end(year)
                while len(cached.years) > MAX_FORECAST_YEARS:
                    cached.years.popitem(last=False)
                unverified_ids = sorted(
                    set(cached.unverified_ids) | self._unverified_ids(selected)
                )
                safe_through = (
                    None
                    if cached.unavailable_ids
                    or unverified_ids
                    or cached.earliest_conflict_year == 0
                    else MAX_PLANNING_HORIZON_YEAR
                    if cached.earliest_conflict_year is None
                    else cached.earliest_conflict_year - 1
                )
                response = GrowthForecastResponse(
                    year=year,
                    plan_version=project.plan.version,
                    geometry_version=project.geometry_version,
                    current_issues=[
                        item.model_copy(deep=True) for item in cached.current_issues
                    ],
                    issues=[item.model_copy(deep=True) for item in selected],
                    earliest_conflict_year=cached.earliest_conflict_year,
                    conflict_free_through_year=safe_through,
                    forecast_unavailable_object_ids=list(cached.unavailable_ids),
                    unverified_object_ids=unverified_ids,
                )
            self._cache[project.id] = cached
            self._cache.move_to_end(project.id)
            while len(self._cache) > MAX_FORECAST_PROJECTS:
                self._cache.popitem(last=False)
            return response
