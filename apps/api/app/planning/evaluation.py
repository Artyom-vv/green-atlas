from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from shapely.geometry.base import BaseGeometry
from shapely.prepared import PreparedGeometry

from app.geometry.domain import PositionAdvisory, PositionViolation
from app.geometry.ports import GeometryEnginePort
from app.planning.change_contracts import ChangeSetCandidateResult
from app.planning.contracts import Plan, PlanObject, PlanObjectCreate, PlanObjectUpdate
from app.planning.domain import PlantSpacingIndex
from app.planning.rules import (
    default_layout_radius,
    planting_zone_at,
)
from app.planning.types import OperationType, RejectedCategory, RejectedStatus
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.species.catalog import get_species, growth_forecasts
from app.species.placement_policy import plant_eligibility


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
            project, payload.x, payload.y, radius
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
                raise ValueError("Порода не соответствует типу посадочного места")
            self.require_assortment(object_.species_revision_id, zone)
            object_.canopy_forecast, object_.root_forecast = growth_forecasts(
                revision, object_.size_class
            )
            canopy_20 = next(
                (item for item in object_.canopy_forecast if item.horizon_year == 20),
                None,
            )
            roots_20 = next(
                (item for item in object_.root_forecast if item.horizon_year == 20),
                None,
            )
            if canopy_20 and roots_20:
                growth_advisory = self.geometry.future_growth_advisory_detail(
                    project,
                    object_.x,
                    object_.y,
                    canopy_20.radius_max_m,
                    roots_20.radius_max_m,
                )
                advisory = advisory or growth_advisory
        if not (spacing_index or PlantSpacingIndex(plan.objects)).respects(object_):
            raise CandidateRejected(
                CandidateIssue(
                    status="blocked",
                    code="PLANT_SPACING",
                    category="spacing",
                    message="Объект расположен слишком близко к существующим посадкам",
                    rule_id="group-spacing",
                    suggested_action="Увеличить расстояние или изменить политику плотности",
                    zone_id=zone.id if zone else None,
                )
            )
        issue = _advisory_issue(advisory) if advisory else None
        if issue and zone:
            issue = CandidateIssue(**{**issue.__dict__, "zone_id": zone.id})
        return object_, issue

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
            raise KeyError("Объект плана не найден")
        updates = payload.model_dump(exclude_unset=True)
        if not updates:
            raise ValueError("Не указаны изменения объекта")
        if current.locked and not (
            set(updates) == {"locked"} and updates["locked"] is False
        ):
            raise ValueError("Сначала снимите закрепление объекта")
        if set(updates) == {"locked"}:
            if not isinstance(updates["locked"], bool):
                raise ValueError("Укажите состояние закрепления")
            # A protection toggle changes no geometry. Existing placement
            # issues must not prevent unlocking an object to repair it.
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
        self.geometry.validate_position(
            project, next_x, next_y, next_radius, current.kind
        )
        zone = planting_zone_at(project, next_x, next_y, next_radius, compiled_zones)
        if project.planting_zones and zone is None:
            raise ValueError("Выберите позицию внутри одного из участков задания")
        updates["x"] = next_x
        updates["y"] = next_y
        updates["radius"] = next_radius
        updates["layout_radius_m"] = next_radius
        updates["planting_zone_id"] = zone.id if zone else None
        advisory = self.geometry.placement_advisory(
            project, next_x, next_y, next_radius
        )
        updates["status"] = "warning" if advisory else "valid"
        next_revision_id = updates.get(
            "species_revision_id", current.species_revision_id
        )
        next_size_class = updates.get("size_class", current.size_class)
        if next_revision_id:
            revision = get_species(str(next_revision_id))
            if revision.kind != current.kind:
                raise ValueError("Порода не соответствует типу посадочного места")
            self.require_assortment(str(next_revision_id), zone)
            canopy, roots = growth_forecasts(revision, str(next_size_class))
            updates["canopy_forecast"] = canopy
            updates["root_forecast"] = roots
            canopy_20 = next((item for item in canopy if item.horizon_year == 20), None)
            roots_20 = next((item for item in roots if item.horizon_year == 20), None)
            if canopy_20 and roots_20:
                advisory = (
                    self.geometry.future_growth_advisory(
                        project,
                        next_x,
                        next_y,
                        canopy_20.radius_max_m,
                        roots_20.radius_max_m,
                    )
                    or advisory
                )
        else:
            updates["canopy_forecast"] = []
            updates["root_forecast"] = []
        candidate = PlanObject.model_validate({**current.model_dump(), **updates})
        index = spacing_index or PlantSpacingIndex(plan.objects)
        ignore_id = None if spacing_index is not None else current.id
        if not index.respects(candidate, ignore_id=ignore_id):
            raise ValueError("Объект расположен слишком близко к существующим посадкам")
        return candidate, advisory

    @staticmethod
    def require_assortment(
        revision_id: str | None, zone: PlantingZoneAssignment | None
    ) -> None:
        # Unassigned layout positions remain repairable drafts. Release already
        # rejects them; assigning a real species must pass the saved zone policy.
        if revision_id is None:
            return
        check = plant_eligibility(
            revision_id,
            zone.territory if zone else None,
            zone.site_conditions if zone else None,
            zone_id=zone.id if zone else None,
        )
        if not check.allowed:
            raise CandidateRejected(
                CandidateIssue(
                    status="blocked",
                    code=check.code,
                    category="constraint",
                    message=check.reason,
                    rule_id="moscow_assortment",
                    suggested_action="Уточнить условия участка или выбрать подходящее растение",
                    zone_id=check.zone_id,
                )
            )

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
