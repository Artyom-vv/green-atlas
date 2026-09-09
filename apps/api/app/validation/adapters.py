from collections import OrderedDict
from math import hypot
from threading import RLock

from app.contracts import Plan, Project, ValidationIssue
from app.planning.domain import PlantSpacingIndex, required_spacing
from app.geometry.domain import PositionChecker


class RuleBasedPlanValidator:
    def __init__(self, max_cached_projects: int = 32) -> None:
        if max_cached_projects < 1:
            raise ValueError("Размер кэша проверок должен быть положительным")
        self.max_cached_projects = max_cached_projects
        self._position_checkers: OrderedDict[str, tuple[int, PositionChecker]] = OrderedDict()
        self._lock = RLock()

    def _position_checker(self, project: Project) -> PositionChecker:
        # SQLite deserializes a fresh Project object for each request, so an
        # object identity invalidates the cache on every tree click. Geometry
        # version changes only when the map's spatial basis changes and is the
        # durable cache key shared by independently loaded project copies.
        snapshot_version = project.geometry_version if project.geometry else 0
        with self._lock:
            cached = self._position_checkers.get(project.id)
            if cached is not None and cached[0] == snapshot_version:
                self._position_checkers.move_to_end(project.id)
                return cached[1]
            checker = PositionChecker(project)
            self._position_checkers[project.id] = (snapshot_version, checker)
            self._position_checkers.move_to_end(project.id)
            while len(self._position_checkers) > self.max_cached_projects:
                self._position_checkers.popitem(last=False)
            return checker

    def discard(self, project_id: str) -> None:
        """Release derived constraint indexes once their project is deleted."""
        with self._lock:
            self._position_checkers.pop(project_id, None)

    def validate_plan(self, project: Project, plan: Plan) -> list[ValidationIssue]:
        # Validation is derived only from the current drawing, manual areas
        # and planting objects. It never carries a separate project-wide
        # target or capacity score into a local editing decision.
        issues: list[ValidationIssue] = []
        position_checker = self._position_checker(project)
        for object_ in plan.objects:
            violation = position_checker.check(object_.x, object_.y, object_.radius, object_.kind)
            advisory = position_checker.advisory(object_.x, object_.y, object_.radius) if violation is None else None
            object_.status = "error" if violation else "warning" if advisory else "valid"
            if violation:
                issues.append(ValidationIssue(
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
                ))
            elif advisory:
                issues.append(ValidationIssue(
                    severity="warning",
                    code=advisory.code,
                    title=advisory.title,
                    description=advisory.description,
                    object_id=object_.id,
                    rule_id="untyped_utility",
                    x=object_.x,
                    y=object_.y,
                    suggested_action=advisory.suggested_action,
                ))
            canopy_20 = next((item for item in object_.canopy_forecast if item.horizon_year == 20), None)
            roots_20 = next((item for item in object_.root_forecast if item.horizon_year == 20), None)
            growth_advisory = position_checker.growth_advisory(
                object_.x,
                object_.y,
                canopy_20.radius_max_m,
                roots_20.radius_max_m,
            ) if canopy_20 and roots_20 else None
            if growth_advisory:
                if object_.status == "valid":
                    object_.status = "warning"
                issues.append(ValidationIssue(
                    severity="warning",
                    code=growth_advisory.code,
                    title=growth_advisory.title,
                    description=growth_advisory.description,
                    object_id=object_.id,
                    rule_id="growth_forecast",
                    x=object_.x,
                    y=object_.y,
                    suggested_action=growth_advisory.suggested_action,
                ))
            if not object_.species_revision_id:
                if object_.status == "valid":
                    object_.status = "warning"
                issues.append(ValidationIssue(
                    severity="warning",
                    code="SPECIES_UNASSIGNED",
                    title="Порода не назначена",
                    description="Посадочное место можно редактировать, но вид и прогноз роста пока не определены.",
                    object_id=object_.id,
                    x=object_.x,
                    y=object_.y,
                    suggested_action="Назначить породу одному объекту или выбранной группе",
                ))
        spacing_index = PlantSpacingIndex()
        for first in plan.objects:
            for second in spacing_index.nearby(first):
                actual = hypot(first.x - second.x, first.y - second.y)
                required = required_spacing(first, second)
                if actual + 1e-6 >= required:
                    continue
                first.status = "error"
                second.status = "error"
                issues.append(ValidationIssue(
                    severity="error",
                    code="PLANT_SPACING",
                    title="Недостаточное расстояние между посадками",
                    description=f"Между объектами {actual:.2f} м при требовании {required:.2f} м.",
                    object_id=first.id,
                    actual=round(actual, 2),
                    required=round(required, 2),
                    unit="м",
                    rule_id="plant_spacing",
                    x=first.x,
                    y=first.y,
                    suggested_action=f"Разнести посадки ещё минимум на {required - actual:.2f} м",
                    related_object_ids=[first.id, second.id],
                ))
            spacing_index.add(first)
        return issues
