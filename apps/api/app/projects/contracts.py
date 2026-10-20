"""Состояние проекта, исходник, зоны и план с отдельной готовностью расчётного основания
и путём возобновления.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.dxf_import.contracts import ImportStatus, SourceFile
from app.dxf_import.layers.contracts import Layer
from app.dxf_import.source.review_contracts import SourceReview
from app.geometry.contracts import CoordinateReference, GeometrySnapshot
from app.planning.contracts import Plan
from app.planting_zones.contracts import PlantingZoneAssignment

ProjectResumeKind = Literal["workspace", "source_preparation", "awaiting_autocad"]
PreparedAvailability = Literal[
    "available", "missing", "corrupt", "stale", "unsupported", "not_required"
]


class ProjectStatus(StrEnum):
    EMPTY = "empty"
    IMPORTED = "imported"
    MAPPED = "mapped"
    ZONES_SELECTED = "zones_selected"
    EDITING = "editing"


class Project(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    status: ProjectStatus = ProjectStatus.EMPTY
    source_file: SourceFile | None = None
    # DXF остаётся неизменяемым CAD-входом. import_status сообщает, содержит ли
    # состояние семантику выпуска, необходимую для следующей редактируемой ревизии.
    import_status: ImportStatus = Field(default_factory=ImportStatus)
    # Редактируемый черновик не доказывает завершение проверки ограничений.
    # Статус хранится вне карты и остаётся видимым в компактном HTTP-чтении.
    source_review: SourceReview | None = None
    layers: list[Layer] = Field(default_factory=list)
    coordinate_reference: CoordinateReference = Field(
        default_factory=CoordinateReference
    )
    source_geometry: GeometrySnapshot | None = None
    geometry: GeometrySnapshot | None = None
    # Обычное чтение проекта намеренно не содержит карты. Малый постоянный флаг
    # различает предпросмотр исходника и подготовленную карту даже без внешнего
    # контура DXF и вычисленной площади территории.
    map_ready: bool = False
    geometry_version: int = Field(default=0, ge=0)
    planting_zones: list[PlantingZoneAssignment] = Field(default_factory=list)
    site_area_m2: float | None = Field(default=None, ge=0)
    planning_area_m2: float | None = Field(default=None, ge=0)
    allowed_area_m2: float | None = Field(default=None, ge=0)
    plan: Plan | None = None
    state_version: int = Field(default=1, ge=1)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    @field_validator("status", mode="before")
    @classmethod
    def migrate_legacy_status(cls, value: object) -> object:
        """Читает старые записи SQLite и заменяет выведенную из обращения семантику
        статусов.
        """
        return {
            "configured": ProjectStatus.ZONES_SELECTED.value,
            "generated": ProjectStatus.EDITING.value,
            # Прежние версии считали проверку и экспорт стадиями проекта. Сейчас план
            # проверяется непрерывно, а DXF является его артефактом; оба старых состояния
            # возобновляются как редактируемый проект.
            "validated": ProjectStatus.EDITING.value,
            "checked": ProjectStatus.EDITING.value,
            "exported": ProjectStatus.EDITING.value,
        }.get(str(value), value)

    @model_validator(mode="after")
    def infer_map_readiness_for_existing_projects(self) -> Project:
        # Старые записи SQLite не содержат map_ready. При загрузке полного снимка
        # однократно выводим флаг, сохраняя рабочий редактор вместо ложного
        # предпросмотра только для чтения.
        if self.geometry is not None:
            self.map_ready = True
        return self


class ProjectSummary(BaseModel):
    id: str
    name: str
    status: ProjectStatus
    source_name: str | None = None
    source_size: int | None = None
    has_geometry: bool = False
    planting_zone_count: int = Field(default=0, ge=0)
    plan_object_count: int = Field(default=0, ge=0)
    plan_version: int | None = Field(default=None, ge=1)
    import_status: ImportStatus = Field(default_factory=ImportStatus)
    state_version: int = Field(default=1, ge=1)
    created_at: str
    updated_at: str
    resume_kind: ProjectResumeKind = "awaiting_autocad"
    prepared_state: PreparedAvailability = "not_required"


class ProjectRead(Project):
    """Runtime availability is returned to clients, never persisted as fact."""

    resume_kind: ProjectResumeKind = "awaiting_autocad"
    prepared_state: PreparedAvailability = "not_required"


class ProjectCreate(BaseModel):
    name: str = "Новый проект"
