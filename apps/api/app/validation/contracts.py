"""Адресные замечания проверки и идентичность её основания: версии плана, геометрии,
правил и входных данных.
"""

from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR, PlanningHorizonYear

GROWTH_UNVERIFIED_CODES = frozenset({"NATIVE_LOCAL_UNKNOWN", "UNTYPED_UTILITY_REVIEW"})


class ValidationIssue(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    severity: Literal["warning", "error"]
    code: str
    title: str
    description: str
    object_id: str | None = None
    actual: float | int | None = None
    required: float | int | None = None
    unit: str | None = None
    rule_id: str | None = None
    x: float | None = None
    y: float | None = None
    suggested_action: str | None = None
    related_object_ids: list[str] = Field(default_factory=list)

    @field_validator("severity", mode="before")
    @classmethod
    def migrate_legacy_recommendation_severity(cls, value: object) -> object:
        """Сохраняет чтение проектов SQLite, записанных до упрощения уровней замечаний.

        Старые сборки сохраняли необязательные советы как recommendation. Текущий
        ручной поток планирования показывает два уровня, требующих реакции:
        предупреждение и ошибку. Старый совет читается как предупреждение, чтобы
        запись проекта оставалась допустимой и доступной в списке.
        """
        return "warning" if value == "recommendation" else value


class PlanValidationBasis(BaseModel):
    """Основание замечаний: версия плана, горизонт и идентичность расчётных данных."""

    plan_version: int = Field(ge=1)
    geometry_version: int = Field(ge=0)
    validator_revision: str = Field(min_length=1)
    planning_horizon_year: PlanningHorizonYear = DEFAULT_PLANNING_HORIZON_YEAR
    objects_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    geometry_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    calculation_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evidence_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CalculationInputBasis(BaseModel):
    """Checked local input identity; missing legacy fields never imply freshness."""

    model_config = {"frozen": True}

    geometry_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    calculation_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    evidence_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class GrowthForecastResponse(BaseModel):
    """Временная проверка роста сохранённых посадок без изменения плана и его замечаний."""

    year: PlanningHorizonYear
    plan_version: int = Field(ge=1)
    geometry_version: int = Field(ge=0)
    current_issues: list[ValidationIssue] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    earliest_conflict_year: PlanningHorizonYear | None = None
    conflict_free_through_year: PlanningHorizonYear | None = None
    forecast_unavailable_object_ids: list[str] = Field(default_factory=list)
    unverified_object_ids: list[str] = Field(default_factory=list)
