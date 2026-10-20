"""Паспорт исходных данных: наблюдаемое покрытие и ограничения доверия.

Контракт отделяет объекты CAD, отображение и расчётные представления. Отсутствие
слоя не доказывает свободную территорию; сведения о полноте не заменяют нормы.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.geometry.contracts import CoordinateReference
from app.geometry.coverage_contracts import LayerGeometryCoverage
from app.planning.config import PlanningHorizonYear

DataPassportKind = Literal[
    "site_border",
    "building",
    "road",
    "utility",
    "existing_green",
    "lawn",
    "water",
    "restricted",
    "unclassified",
]
DataPassportEntryStatus = Literal["verified", "partial", "missing", "excluded"]
DataPassportConfidence = Literal["high", "medium", "low"]
DataPassportDecision = Literal["stop", "warning", "advisory"]


class DataPassportEntry(BaseModel):
    """Класс исходных данных и его участие в текущем расчёте.

    Сообщаем только доказуемые сведения об исходнике и подготовке. Отсутствующий
    слой не становится подтверждением отсутствия физических объектов.
    """

    kind: DataPassportKind
    label: str
    status: DataPassportEntryStatus
    layer_names: list[str] = Field(default_factory=list)
    object_count: int = Field(default=0, ge=0)
    # Старые клиенты называли число частей отображения объектами в расчёте.
    # Журнала проверки каждого объекта здесь нет: None означает «не измерено».
    used_object_count: int | None = Field(default=None, ge=0, deprecated=True)
    display_feature_count: int | None = Field(default=None, ge=0)
    used_in_calculation: bool = False
    semantic_confidence: DataPassportConfidence = "low"
    decision_level: DataPassportDecision = "advisory"
    source_file_name: str | None = None
    source_imported_at: str | None = None
    source_owner: str | None = None
    note: str
    geometry_coverage: LayerGeometryCoverage | None = None


class DataPassport(BaseModel):
    """Сводка оснований перед массовой посадкой; сама посадка здесь не проверяется."""

    planning_horizon_year: PlanningHorizonYear | None = None
    overall_status: Literal["verified", "limited", "not_ready"]
    calculation_status: Literal["ready", "not_ready"]
    mass_placement_status: Literal["verified", "limited", "blocked"]
    summary: str
    source_file_name: str | None = None
    source_imported_at: str | None = None
    source_owner: str | None = None
    coordinate_reference: CoordinateReference = Field(
        default_factory=lambda: CoordinateReference()
    )
    entries: list[DataPassportEntry] = Field(default_factory=list)
    unclassified_layers: list[str] = Field(default_factory=list)
    incomplete_layers: list[str] = Field(default_factory=list)
    excluded_layers: list[str] = Field(default_factory=list)
    used_in_calculation: list[str] = Field(default_factory=list)
    missing_classes: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
