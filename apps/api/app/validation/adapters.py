"""Проверка всего плана через общие геометрические правила, прогноз роста, расстояния и
сетевые свидетельства.
"""

import json
from collections import OrderedDict
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from functools import partial
from math import hypot
from threading import RLock
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from app.geometry.checking.checker_basis import (
    DEFAULT_CHECKER_CACHE_PROJECTS,
    checker_basis,
    checker_project,
)
from app.geometry.checking.domain import PositionAdvisory, PositionChecker
from app.geometry.ports import GeometryEnginePort
from app.geometry.preparation import calculation_scope, prepare_positions
from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR
from app.planning.contracts import Plan, PlanObject
from app.planning.domain import PlantSpacingIndex, required_spacing
from app.projects.contracts import Project
from app.shared.messages import message
from app.species.forecast import calculation_growth_radii, forecast_at
from app.species.rule_context import mature_crown_diameter
from app.validation.contracts import GROWTH_UNVERIFIED_CODES, ValidationIssue
from app.validation.networks import (
    compact_missing_network_issues,
    object_network_issues,
    object_network_trace_issues,
)
from app.validation.zone_reuse import unchanged_zone_checks


class RuleBasedPlanValidator:
    revision = "rule-based-plan-v8-current-with-growth-scenario"

    def __init__(
        self,
        max_cached_projects: int = DEFAULT_CHECKER_CACHE_PROJECTS,
        *,
        geometry: GeometryEnginePort | None = None,
    ) -> None:
        if max_cached_projects < 1:
            raise ValueError("Размер кэша проверок должен быть положительным")
        self.max_cached_projects = max_cached_projects
        self._geometry = geometry
        self._position_checkers: OrderedDict[str, tuple[tuple, PositionChecker]] = (
            OrderedDict()
        )
        self._lock = RLock()

    def _position_checker(self, project: Project) -> PositionChecker:
        # SQLite создаёт новый Project на каждый запрос; идентичность объекта
        # сбрасывала бы кеш при выборе дерева. Ревизия геометрии задаёт устойчивый
        # ключ пространственного основания для независимо загруженных копий проекта.
        snapshot_version = checker_basis(project)
        with self._lock:
            cached = self._position_checkers.get(project.id)
            if cached is not None and cached[0] == snapshot_version:
                self._position_checkers.move_to_end(project.id)
                return cached[1]
            checker = PositionChecker(checker_project(project))
            self._position_checkers[project.id] = (snapshot_version, checker)
            self._position_checkers.move_to_end(project.id)
            while len(self._position_checkers) > self.max_cached_projects:
                self._position_checkers.popitem(last=False)
            return checker

    def discard(self, project_id: str) -> None:
        """Освобождает производные индексы ограничений после удаления проекта."""
        with self._lock:
            self._position_checkers.pop(project_id, None)
        if self._geometry is not None:
            # Динамический CAD-маршрутизатор синтезирует неизвестные атрибуты.
            # Вызывать очистку безопасно только при явно реализованном методе.
            discard = getattr(type(self._geometry), "discard", None)
            if callable(discard):
                discard(self._geometry, project_id)

    def calculation_scope(self, project: Project) -> AbstractContextManager[None]:
        return (
            calculation_scope(self._geometry, project)
            if self._geometry is not None
            else nullcontext()
        )

    def can_reuse_after_zone_change(
        self, previous: Project, current: Project, plan: Plan
    ) -> bool:
        """Доказать независимость всех замечаний, включая прогнозные габариты."""
        if type(self) is not RuleBasedPlanValidator:
            return False
        if self._geometry is not None:
            method = getattr(
                type(self._geometry), "validation_independent_of_planting_zones", None
            )
            if callable(method) and method(self._geometry, current):
                return True
            # Неизвестный геометрический провайдер может учитывать зоны иначе.
            from app.geometry.checking.adapters import ShapelyGeometryEngine

            if type(self._geometry) is not ShapelyGeometryEngine:
                return False
        return unchanged_zone_checks(previous, current, plan)

    def validate_plan(self, project: Project, plan: Plan) -> list[ValidationIssue]:
        with self.calculation_scope(project):
            return self._validate_plan(project, plan)

    def _validate_plan(self, project: Project, plan: Plan) -> list[ValidationIssue]:
        # Проверка выводится из текущего чертежа, рабочих зон и посадок. Общая
        # целевая оценка или ёмкость проекта не влияет на локальное редактирование.
        issues: list[ValidationIssue] = []
        geometry = self._geometry
        if geometry is None:
            position_checker = self._position_checker(project)
            check = position_checker.check

            # Совместимый проверяющий модуль не принимает вид растения для предупреждения.
            def advisory_detail(
                x: float, y: float, radius: float, plant_kind: Literal["tree", "shrub"]
            ) -> PositionAdvisory | None:
                return position_checker.advisory(x, y, radius)

            def growth_advisory_detail(
                x: float,
                y: float,
                canopy_radius: float,
                root_radius: float,
                plant_kind: Literal["tree", "shrub"],
            ) -> PositionAdvisory | None:
                return position_checker.growth_advisory(
                    x, y, canopy_radius, root_radius
                )
        else:
            # Каждый запрос связан с одним провайдером и проектом. Ошибки провайдера
            # передаются вызывающему коду; прежний кеш не подменяет результат.
            prepare_positions(
                geometry, project, [(item.x, item.y) for item in plan.objects]
            )
            check = partial(geometry.position_violation, project)
            advisory_detail = partial(geometry.placement_advisory_detail, project)
            growth_advisory_detail = partial(
                geometry.future_growth_advisory_detail, project
            )
        for object_ in plan.objects:
            violation = check(object_.x, object_.y, object_.radius, object_.kind)
            advisory = (
                advisory_detail(object_.x, object_.y, object_.radius, object_.kind)
                if violation is None
                else None
            )
            object_.status = (
                "error" if violation else "warning" if advisory else "valid"
            )
            if violation:
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code=violation.code,
                        title=violation.title,
                        description=violation.description,
                        object_id=object_.id,
                        actual=violation.actual,
                        required=violation.required,
                        unit="м",
                        rule_id=violation.rule_id,
                        x=object_.x,
                        y=object_.y,
                        suggested_action=violation.suggested_action,
                    )
                )
            elif advisory:
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code=advisory.code,
                        title=advisory.title,
                        description=advisory.description,
                        object_id=object_.id,
                        rule_id="source_review"
                        if advisory.code
                        in {"SOURCE_REVIEW_PENDING", "SOURCE_GEOMETRY_PARTIAL"}
                        else "untyped_utility",
                        x=object_.x,
                        y=object_.y,
                        suggested_action=advisory.suggested_action,
                    )
                )
            if geometry is None:
                network_issues = object_network_issues(position_checker, object_)
            else:
                trace = geometry.position_rule_trace(
                    project,
                    object_.x,
                    object_.y,
                    object_.kind,
                    mature_crown_diameter(object_.species_revision_id),
                )
                network_issues = object_network_trace_issues(trace.entries, object_)
            for issue in network_issues:
                if (
                    violation
                    and issue.code == violation.code
                    and issue.rule_id == violation.rule_id
                ):
                    continue
                issues.append(issue)
                if issue.severity == "error":
                    object_.status = "error"
                elif object_.status == "valid":
                    object_.status = "warning"
            issues.extend(
                self._object_growth_issues(
                    object_, DEFAULT_PLANNING_HORIZON_YEAR, growth_advisory_detail
                )
            )
            if not object_.species_revision_id:
                if object_.status == "valid":
                    object_.status = "warning"
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="SPECIES_UNASSIGNED",
                        title="Порода не назначена",
                        description="Посадочное место можно редактировать, но вид и прогноз роста пока не определены.",
                        object_id=object_.id,
                        x=object_.x,
                        y=object_.y,
                        suggested_action="Назначить породу одному объекту или выбранной группе",
                    )
                )
        issues.extend(self._spacing_issues(plan, DEFAULT_PLANNING_HORIZON_YEAR))
        issues = compact_missing_network_issues(issues, object_count=len(plan.objects))
        return self._identify_issues(project, plan, issues)

    @staticmethod
    def _identify_issues(
        project: Project, plan: Plan, issues: list[ValidationIssue]
    ) -> list[ValidationIssue]:
        for issue in issues:
            # Измерения и текст могут меняться у того же замечания о тех же объектах.
            # Случайный id или ревизия геометрии не входит в его идентичность.
            identity = [
                project.id,
                plan.id,
                issue.rule_id,
                issue.code,
                sorted(
                    set(
                        issue.related_object_ids
                        or ([issue.object_id] if issue.object_id else [])
                    )
                ),
            ]
            issue.id = str(
                uuid5(
                    NAMESPACE_URL,
                    "green-atlas/plan-issue/"
                    + json.dumps(identity, ensure_ascii=False),
                )
            )
        return sorted(issues, key=lambda item: item.id)

    @staticmethod
    def _object_growth_issues(
        object_: PlanObject,
        year: int,
        growth_advisory_detail: Callable[..., PositionAdvisory | None],
    ) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        canopy_at_horizon = forecast_at(object_.canopy_forecast, year)
        roots_at_horizon = forecast_at(object_.root_forecast, year)
        if object_.species_revision_id and (
            canopy_at_horizon is None or roots_at_horizon is None
        ):
            object_.status = "error"
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="GROWTH_FORECAST_UNAVAILABLE",
                    title=message("growth.forecast_missing_title"),
                    description=message("GROWTH_FORECAST_UNAVAILABLE"),
                    object_id=object_.id,
                    rule_id="growth_forecast",
                    x=object_.x,
                    y=object_.y,
                    suggested_action=message("growth.forecast_missing_action"),
                )
            )
        growth_advisory = (
            growth_advisory_detail(
                object_.x,
                object_.y,
                *calculation_growth_radii(canopy_at_horizon, roots_at_horizon),
                object_.kind,
            )
            if canopy_at_horizon and roots_at_horizon
            else None
        )
        if growth_advisory:
            if object_.status == "valid":
                object_.status = "warning"
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code=growth_advisory.code,
                    title=growth_advisory.title,
                    description=growth_advisory.description,
                    object_id=object_.id,
                    rule_id="growth_forecast",
                    x=object_.x,
                    y=object_.y,
                    suggested_action=growth_advisory.suggested_action,
                )
            )
        return issues

    @staticmethod
    def _spacing_issues(plan: Plan, year: int) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        spacing_index = PlantSpacingIndex(planning_horizon_year=year)
        for first in plan.objects:
            for second in spacing_index.nearby(first):
                actual = hypot(first.x - second.x, first.y - second.y)
                required = required_spacing(first, second, year)
                if actual + 1e-6 >= required:
                    continue
                first.status = "error"
                second.status = "error"
                # Пара объектов задаёт одно замечание независимо от порядка списка.
                anchor = min((first, second), key=lambda item: item.id)
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="PLANT_SPACING",
                        title="Недостаточное расстояние между посадками",
                        description=f"Между объектами {actual:.2f} м при требовании {required:.2f} м.",
                        object_id=anchor.id,
                        actual=round(actual, 2),
                        required=round(required, 2),
                        unit="м",
                        rule_id="plant_spacing",
                        x=anchor.x,
                        y=anchor.y,
                        suggested_action=f"Разнести посадки ещё минимум на {required - actual:.2f} м",
                        related_object_ids=sorted([first.id, second.id]),
                    )
                )
            spacing_index.add(first)
        return issues

    def validate_growth_scenario(
        self, project: Project, plan: Plan, year: int
    ) -> list[ValidationIssue]:
        """Проверяет только прогнозные габариты теми же правилами, что текущий план."""
        with self.calculation_scope(project):
            return self._validate_growth_scenario(project, plan, year)

    def _validate_growth_scenario(
        self, project: Project, plan: Plan, year: int
    ) -> list[ValidationIssue]:
        if self._geometry is None:
            checker = self._position_checker(project)

            def growth(
                x: float,
                y: float,
                canopy: float,
                roots: float,
                kind: Literal["tree", "shrub"],
            ) -> PositionAdvisory | None:
                return checker.growth_advisory(x, y, canopy, roots)
        else:
            prepare_positions(
                self._geometry, project, [(item.x, item.y) for item in plan.objects]
            )
            growth = partial(self._geometry.future_growth_advisory_detail, project)
        issues = [
            issue
            for object_ in plan.objects
            for issue in self._object_growth_issues(object_, year, growth)
            if issue.code.startswith(("GROWTH_", "ROOT_", "CANOPY_", "CROWN_"))
            or issue.code in GROWTH_UNVERIFIED_CODES
        ]
        for issue in issues:
            if issue.code in GROWTH_UNVERIFIED_CODES:
                # Адресное замечание совпадает с текущей проверкой по идентичности.
                # Его нельзя повторно показывать как новый конфликт роста.
                issue.rule_id = "untyped_utility"
        issues.extend(self._spacing_issues(plan, year))
        return self._identify_issues(project, plan, issues)
