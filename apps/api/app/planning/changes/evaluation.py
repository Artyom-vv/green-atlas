"""Единая оценка кандидатов посадки и правок по геометрии, росту, расстояниям и рабочим
зонам.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry

from app.dxf_import.contracts import ImportMode
from app.dxf_import.source.admission import partial_geometry_accepted
from app.geometry.checking.domain import PositionAdvisory, PositionViolation
from app.geometry.ports import GeometryEnginePort, PositionRejected
from app.planning.changes.contracts import ChangeSetCandidateResult
from app.planning.contracts import Plan, PlanObject, PlanObjectCreate, PlanObjectUpdate
from app.planning.domain import PlantSpacingIndex
from app.planning.rules import (
    default_layout_radius,
    planting_zone_at,
)
from app.planning.types import OperationType, RejectedCategory, RejectedStatus
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason, ResourceNotFound
from app.species.catalog import get_species, growth_forecasts
from app.species.forecast import calculation_growth_radii, forecast_at


@dataclass(frozen=True)
class CandidateIssue:
    status: RejectedStatus
    code: str
    category: RejectedCategory
    message: str
    rule_id: str | None = None
    source_layer: str | None = None
    source_feature_ids: tuple[str, ...] = ()
    actual_distance_m: float | None = None
    required_distance_m: float | None = None
    suggested_action: str | None = None
    zone_id: str | None = None


class CandidateRejected(ValueError):
    def __init__(self, issue: CandidateIssue) -> None:
        super().__init__(issue.message)
        self.issue = issue


def informational_source_warning(project: Project, *, status: str, code: str) -> bool:
    """Общий пробел native-исходника информирует, не требуя отдельного согласия.

    Допуск относится только к связанному захвату и рассчитанной карте по доступным
    данным. Неопределённость конкретной позиции, конфликт роста и нарушения правил
    остаются самостоятельными основаниями отказа. У сохранённых DXF-проектов
    сохраняется прежняя политика допуска; новый вход через них не развивается.
    """
    if status != "unknown" or code != "SOURCE_GEOMETRY_PARTIAL":
        return False
    if (
        project.geometry is None
        or project.geometry.calculation_scope != "available_data"
    ):
        return False
    source = project.source_file
    if source is None:
        return False
    if project.import_status.mode != ImportMode.AUTOCAD_LIVE:
        return partial_geometry_accepted(source)
    capture = source.native_session
    return capture is not None and source.content_sha256 == capture.snapshot_sha256


def _violation_issue(violation: PositionViolation) -> CandidateIssue:
    return CandidateIssue(
        status="blocked",
        code=violation.code,
        category="constraint",
        message=violation.description,
        rule_id=violation.rule_id,
        source_layer=violation.source_layer,
        source_feature_ids=violation.source_feature_ids,
        actual_distance_m=violation.actual,
        required_distance_m=violation.required,
        suggested_action=violation.suggested_action,
    )


def _advisory_issue(advisory: PositionAdvisory) -> CandidateIssue:
    growth = advisory.code.startswith(("GROWTH_", "ROOT_", "CANOPY_", "CROWN_"))
    return CandidateIssue(
        status="soft_conflict" if growth else "unknown",
        code=advisory.code,
        category="growth" if growth else "data",
        message=advisory.description,
        source_layer=advisory.source_layer,
        source_feature_ids=advisory.source_feature_ids,
        suggested_action=advisory.suggested_action,
    )


def candidate_result(
    operation_index: int,
    operation_type: OperationType,
    issue: CandidateIssue | None,
    object_id: str | None,
    accepted_message: str,
    zone_id: str | None = None,
) -> ChangeSetCandidateResult:
    if issue is None:
        return ChangeSetCandidateResult(
            operation_index=operation_index,
            type=operation_type,
            status="allowed",
            code="POSITION_ACCEPTED",
            category="accepted",
            reason=accepted_message,
            object_id=object_id,
            zone_id=zone_id,
        )
    return ChangeSetCandidateResult(
        operation_index=operation_index,
        type=operation_type,
        status=issue.status,
        code=issue.code,
        category=issue.category,
        reason=issue.message,
        object_id=object_id,
        rule_id=issue.rule_id,
        source_layer=issue.source_layer,
        source_feature_ids=list(issue.source_feature_ids),
        actual_distance_m=issue.actual_distance_m,
        required_distance_m=issue.required_distance_m,
        suggested_action=issue.suggested_action,
        zone_id=issue.zone_id or zone_id,
    )


class PlanEvaluation:
    def __init__(self, geometry: GeometryEnginePort, new_id: Callable[[], str]) -> None:
        self.geometry = geometry
        self.new_id = new_id

    def preview_addition(
        self,
        project: Project,
        plan: Plan,
        payload: PlanObjectCreate,
        spacing_index: PlantSpacingIndex | None = None,
        compiled_zones: list[
            tuple[PlantingZoneAssignment, BaseGeometry, PreparedGeometry]
        ]
        | None = None,
    ) -> tuple[PlanObject, CandidateIssue | None]:
        radius = (
            payload.layout_radius_m
            or payload.radius
            or default_layout_radius(payload.kind)
        )
        violation = self.geometry.position_violation(
            project, payload.x, payload.y, radius, payload.kind
        )
        if violation is not None:
            raise CandidateRejected(_violation_issue(violation))
        zone = planting_zone_at(project, payload.x, payload.y, radius, compiled_zones)
        if project.planting_zones and zone is None:
            raise CandidateRejected(
                CandidateIssue(
                    status="blocked",
                    code="PLANTING_ZONE",
                    category="constraint",
                    message="Выберите позицию внутри одного из участков задания",
                    rule_id="planting_zone",
                    suggested_action="Переместить посадку внутрь выбранного участка",
                )
            )
        advisory = self.geometry.placement_advisory_detail(
            project, payload.x, payload.y, radius, payload.kind
        )
        object_ = PlanObject(
            id=self.new_id(),
            **payload.model_dump(exclude={"radius", "layout_radius_m"}),
            radius=radius,
            layout_radius_m=radius,
            status="warning" if advisory else "valid",
            planting_zone_id=zone.id if zone else None,
        )
        if object_.species_revision_id:
            revision = get_species(object_.species_revision_id)
            if revision.kind != object_.kind:
                raise DomainFailure(
                    FailureReason.SPECIES_KIND_MISMATCH,
                    {
                        "species_revision_id": revision.id,
                        "expected_kind": object_.kind,
                        "actual_kind": revision.kind,
                    },
                )
            object_.canopy_forecast, object_.root_forecast = growth_forecasts(
                revision, object_.size_class
            )
        issue = self.preview_horizon(
            project,
            plan,
            object_,
            spacing_index,
            base_issue=_advisory_issue(advisory) if advisory else None,
        )
        return object_, issue

    def preview_horizon(
        self,
        project: Project,
        plan: Plan,
        object_: PlanObject,
        spacing_index: PlantSpacingIndex | None = None,
        *,
        base_issue: CandidateIssue | None = None,
    ) -> CandidateIssue | None:
        """Проверить другой год той же уже проверенной позиции и того же снимка.

        Позиция, рабочий габарит, порода, статические правила и источник должны
        оставаться неизменными. Общий вызывающий расчёт удерживает проверенный
        ресурс; перед публикацией выполняется полный preview_addition.
        """
        issue = base_issue
        if object_.species_revision_id:
            canopy_at_horizon = forecast_at(
                object_.canopy_forecast, plan.planning_horizon_year
            )
            roots_at_horizon = forecast_at(
                object_.root_forecast, plan.planning_horizon_year
            )
            if canopy_at_horizon and roots_at_horizon:
                growth_advisory = self.geometry.future_growth_advisory_detail(
                    project,
                    object_.x,
                    object_.y,
                    *calculation_growth_radii(canopy_at_horizon, roots_at_horizon),
                    object_.kind,
                )
                # Общее замечание о покрытии исходника не скрывает локальный конфликт
                # роста в конкретной позиции кандидата.
                if growth_advisory and (
                    issue is None or issue.code == "SOURCE_GEOMETRY_PARTIAL"
                ):
                    issue = _advisory_issue(growth_advisory)
        if not (
            spacing_index
            or PlantSpacingIndex(
                plan.objects, planning_horizon_year=plan.planning_horizon_year
            )
        ).respects(object_):
            raise CandidateRejected(
                CandidateIssue(
                    status="blocked",
                    code="PLANT_SPACING",
                    category="spacing",
                    message="Объект расположен слишком близко к существующим посадкам",
                    rule_id="group-spacing",
                    suggested_action="Увеличить расстояние или изменить политику плотности",
                    zone_id=object_.planting_zone_id,
                )
            )
        if issue and object_.planting_zone_id:
            issue = CandidateIssue(
                **{**issue.__dict__, "zone_id": object_.planting_zone_id}
            )
        return issue

    def preview_update(
        self,
        project: Project,
        plan: Plan,
        object_id: str,
        payload: PlanObjectUpdate,
        spacing_index: PlantSpacingIndex | None = None,
        compiled_zones: list[
            tuple[PlantingZoneAssignment, BaseGeometry, PreparedGeometry]
        ]
        | None = None,
    ) -> tuple[PlanObject, str | None]:
        current = next((item for item in plan.objects if item.id == object_id), None)
        if current is None:
            raise ResourceNotFound(FailureReason.PLAN_OBJECT_MISSING)
        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise DomainFailure(FailureReason.PLAN_OBJECT_CHANGES_REQUIRED)
        if current.locked and not (
            set(updates) == {"locked"} and updates["locked"] is False
        ):
            raise DomainFailure(FailureReason.PLAN_OBJECT_LOCKED)
        if set(updates) == {"locked"}:
            if not isinstance(updates["locked"], bool):
                raise DomainFailure(FailureReason.PLAN_OBJECT_LOCK_STATE_REQUIRED)
            # Смена защиты не меняет геометрию. Прежние замечания не должны мешать
            # снять блокировку объекта для его исправления.
            return current.model_copy(update={"locked": updates["locked"]}), None
        radius_value = updates.get(
            "layout_radius_m",
            updates.get("radius", current.layout_radius_m or current.radius),
        )
        if radius_value is None:
            radius_value = current.radius
        next_radius = float(radius_value)
        next_x = float(updates.get("x", current.x))
        next_y = float(updates.get("y", current.y))
        try:
            self.geometry.validate_position(
                project, next_x, next_y, next_radius, current.kind
            )
        except PositionRejected as error:
            raise CandidateRejected(_violation_issue(error.violation)) from error
        zone = planting_zone_at(project, next_x, next_y, next_radius, compiled_zones)
        if project.planting_zones and zone is None:
            raise DomainFailure(FailureReason.PLANTING_POSITION_OUTSIDE_ZONES)
        updates["x"] = next_x
        updates["y"] = next_y
        updates["radius"] = next_radius
        updates["layout_radius_m"] = next_radius
        updates["planting_zone_id"] = zone.id if zone else None
        advisory = self.geometry.placement_advisory(
            project, next_x, next_y, next_radius, current.kind
        )
        updates["status"] = "warning" if advisory else "valid"
        next_revision_id = updates.get(
            "species_revision_id", current.species_revision_id
        )
        next_size_class = updates.get("size_class", current.size_class)
        if next_revision_id:
            revision = get_species(str(next_revision_id))
            if revision.kind != current.kind:
                raise DomainFailure(
                    FailureReason.SPECIES_KIND_MISMATCH,
                    {
                        "species_revision_id": revision.id,
                        "expected_kind": current.kind,
                        "actual_kind": revision.kind,
                    },
                )
            canopy, roots = growth_forecasts(revision, str(next_size_class))
            updates["canopy_forecast"] = canopy
            updates["root_forecast"] = roots
            canopy_at_horizon = forecast_at(canopy, plan.planning_horizon_year)
            roots_at_horizon = forecast_at(roots, plan.planning_horizon_year)
            if canopy_at_horizon and roots_at_horizon:
                advisory = (
                    self.geometry.future_growth_advisory(
                        project,
                        next_x,
                        next_y,
                        *calculation_growth_radii(canopy_at_horizon, roots_at_horizon),
                        current.kind,
                    )
                    or advisory
                )
        else:
            updates["canopy_forecast"] = []
            updates["root_forecast"] = []
        candidate = PlanObject.model_validate({**current.model_dump(), **updates})
        index = spacing_index or PlantSpacingIndex(
            plan.objects, planning_horizon_year=plan.planning_horizon_year
        )
        ignore_id = None if spacing_index is not None else current.id
        if not index.respects(candidate, ignore_id=ignore_id):
            raise DomainFailure(FailureReason.PLANTING_POSITION_TOO_CLOSE)
        return candidate, advisory

    def automatic_generation_zones(
        self,
        project: Project,
        plant_kind: str,
        layout_radius_m: float | None,
        zone_ids: set[str] | None = None,
        growth_radii: tuple[float, float] | None = None,
    ) -> list[PlantingZoneAssignment]:
        kind = "shrub" if plant_kind == "shrub" else "tree"
        radius = layout_radius_m or default_layout_radius(kind)
        result: list[PlantingZoneAssignment] = []
        for zone in project.planting_zones:
            if zone_ids is not None and zone.id not in zone_ids:
                continue
            geometry = self.geometry.automatic_safe_geometry(
                project,
                zone.geometry,
                radius,
                kind,
                growth_canopy_radius=growth_radii[0] if growth_radii else None,
                growth_root_radius=growth_radii[1] if growth_radii else None,
            )
            result.append(zone.model_copy(update={"geometry": geometry}))
        return result
