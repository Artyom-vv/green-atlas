"""Предпросмотр и сохранение рабочих зон с квитанциями, историей и доказанным
переиспользованием проверки плана.
"""

from __future__ import annotations

from collections.abc import Callable
from threading import RLock

from shapely.geometry import shape

from app.dxf_import.contracts import ImportEditability
from app.dxf_import.source.admission import require_calculation_source
from app.history.ports import ProjectHistoryPort
from app.planning.contracts import Plan
from app.planting_zones.change_contracts import ZoneChangeResult
from app.planting_zones.config import MAX_PARTIAL_OVERLAP_M2
from app.planting_zones.contracts import PlantingZoneAssignment
from app.planting_zones.domain import (
    attach_planting_zone_features,
    validate_changed_planting_zones,
)
from app.planting_zones.ports import ZoneProjectRepository, ZoneValidator
from app.projects.contracts import Project, ProjectStatus
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason
from app.validation.application import PlanValidation
from app.validation.ports import PlanValidatorPort


class PlantingZoneApplication:
    def __init__(
        self,
        *,
        repository: ZoneProjectRepository,
        history: ProjectHistoryPort,
        validator: PlanValidatorPort,
        validation: PlanValidation,
        edit_lock: RLock,
        invalidate_spatial: Callable[[str], None],
        zone_validator: ZoneValidator | None = None,
    ) -> None:
        self.repository = repository
        self.history = history
        self.validator = validator
        self.validation = validation
        self.edit_lock = edit_lock
        self.invalidate_spatial = invalidate_spatial
        self.zone_validator = zone_validator or validate_changed_planting_zones

    def get(self, project_id: str, *, lightweight: bool = False) -> Project:
        return self.repository.get(project_id, lightweight=lightweight)

    def validate_planting_zones(
        self, project: Project, zones: list[PlantingZoneAssignment]
    ) -> None:
        self.zone_validator(project, zones)

    def preview_planting_zone(
        self, project_id: str, zone: PlantingZoneAssignment
    ) -> dict[str, object]:
        """Проверяет зону без записи; сохранение повторяет проверку текущего проекта."""
        project = self.repository.get(project_id)
        zones = [item for item in project.planting_zones if item.id != zone.id] + [zone]
        error = None
        try:
            self.validate_planting_zones(project, zones)
        except DomainFailure as problem:
            error = public_failure_message(problem)
        try:
            parsed = shape(zone.geometry)
            if not parsed.is_valid:
                return {"can_save": False, "area_m2": 0, "error": error, "overlaps": []}
        except Exception:
            return {"can_save": False, "area_m2": 0, "error": error, "overlaps": []}
        overlaps = []
        for other in project.planting_zones:
            if other.id == zone.id:
                continue
            geometry = shape(other.geometry)
            intersection = parsed.intersection(geometry)
            nested = parsed.covers(geometry) or geometry.covers(parsed)
            if intersection.area > MAX_PARTIAL_OVERLAP_M2 and not nested:
                overlaps.append(
                    {
                        "zone_id": other.id,
                        "label": other.label,
                        "area_m2": round(intersection.area, 2),
                        "geometry": intersection.__geo_interface__,
                    }
                )
        return {
            "can_save": error is None,
            "area_m2": round(parsed.area, 2),
            "error": error,
            "overlaps": overlaps,
        }

    def save_planting_zones(
        self,
        project_id: str,
        zones: list[PlantingZoneAssignment],
        *,
        preserve_plan: bool = False,
        mutation_receipt: dict | None = None,
    ) -> Project:
        project = self.repository.get(project_id)
        previous = project.model_copy(deep=False)
        base_state_version = project.state_version
        protected_objects = (
            [
                item.model_dump(mode="json", exclude={"status"})
                for item in project.plan.objects
            ]
            if preserve_plan and project.plan is not None
            else None
        )
        self.validate_planting_zones(project, zones)
        project.planting_zones = [zone.model_copy(deep=True) for zone in zones]
        if project.geometry is not None:
            project.geometry = project.geometry.model_copy(
                update={"feature_collection": dict(project.geometry.feature_collection)}
            )
            attach_planting_zone_features(project)
            project.geometry_version += 1
        project.status = (
            ProjectStatus.EDITING
            if project.plan is not None
            else ProjectStatus.ZONES_SELECTED
        )
        try:
            if project.plan is not None:
                self.validation.refresh_after_zone_change(
                    previous, project, project.plan
                )
                if protected_objects is not None and protected_objects != [
                    item.model_dump(mode="json", exclude={"status"})
                    for item in project.plan.objects
                ]:
                    raise DomainFailure(FailureReason.ZONE_CHANGE_PLANTINGS_AFFECTED)
            if mutation_receipt is not None:
                receipt = ZoneChangeResult.model_validate(mutation_receipt)
                if (
                    not preserve_plan
                    or receipt.project_id != project_id
                    or receipt.base_state_version != base_state_version
                    or receipt.state_version != base_state_version + 1
                    or receipt.geometry_version != project.geometry_version
                    or receipt.plan_version
                    != (project.plan.version if project.plan else None)
                    or [item.model_dump(mode="json") for item in receipt.after_zones]
                    != [item.model_dump(mode="json") for item in project.planting_zones]
                ):
                    raise DomainFailure(FailureReason.ZONE_CHANGE_SAVE_RECEIPT_MISMATCH)
                saved = self.repository.save_with_receipt(
                    project,
                    "planting_zones",
                    receipt.preview_id,
                    receipt.model_dump(mode="json"),
                )
            else:
                saved = self.repository.save(project)
        except Exception:
            if preserve_plan:
                # Проверка кеширует будущую ревизию до устойчивой записи. После отказа
                # проверки или условной записи следующая правка не использует этот неподтверждённый индекс.
                self.validator.discard(project.id)
            raise
        self.invalidate_spatial(project.id)
        if project.plan is None:
            self.history.clear(project.id)
        else:
            self.history.rebase_planting_zones(project.id, saved.planting_zones)
        return saved

    def get_zone_change_receipt(
        self, project_id: str, preview_id: str, digest: str, base_state_version: int
    ) -> ZoneChangeResult | None:
        """Читает точный сохранённый результат изменения зоны независимо от срока
        предпросмотра.
        """
        value = self.repository.mutation_receipt(
            project_id, "planting_zones", preview_id
        )
        if value is None:
            return None
        receipt = ZoneChangeResult.model_validate(value)
        if (
            receipt.project_id != project_id
            or receipt.preview_id != preview_id
            or receipt.digest != digest
            or receipt.base_state_version != base_state_version
        ):
            raise DomainFailure(FailureReason.ZONE_CHANGE_RECEIPT_MISMATCH)
        return receipt

    def create_manual_plan(self, project_id: str) -> Project:
        project = self.repository.get(project_id)
        require_calculation_source(project.source_file)
        if project.import_status.editability == ImportEditability.READ_ONLY:
            raise DomainFailure(FailureReason.SOURCE_READ_ONLY)
        if project.geometry is None:
            raise DomainFailure(FailureReason.MANUAL_MAP_REQUIRED)
        if not project.planting_zones:
            raise DomainFailure(FailureReason.MANUAL_ZONES_REQUIRED)
        attach_planting_zone_features(project)
        project.plan = Plan(objects=[], issues=[])
        # Для ручной работы используется единый расчётный снимок. Сырой снимок был
        # нужен для расчёта и повторного назначения до создания плана; два GeoJSON-графа
        # удваивали каждую DXF-сущность. Исходный DXF BLOB остаётся для аудита и выпуска,
        # а назначения на этом шаге уже зафиксированы.
        project.source_geometry = None
        project.status = ProjectStatus.EDITING
        self.history.clear(project.id)
        return self.repository.save(project)
