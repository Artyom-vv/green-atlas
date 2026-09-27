from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator


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
        """Keep pre-simplification SQLite projects readable.

        Earlier builds stored non-blocking advice as ``recommendation``.
        The current manual-planning flow intentionally exposes just two
        actionable severities: a warning or an error.  Legacy advice is a
        warning, rather than an invalid project record that hides the list.
        """
        return "warning" if value == "recommendation" else value


class PlanValidationBasis(BaseModel):
    """The inputs of derived issues; independent of the planting edit version."""

    plan_version: int = Field(ge=1)
    geometry_version: int = Field(ge=0)
    validator_revision: str = Field(min_length=1)
    objects_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
