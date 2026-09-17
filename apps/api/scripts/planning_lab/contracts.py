"""Explicit input boundary for repeatable, metric planning experiments."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, model_validator

from app.planning.pattern_contracts import PatternPreviewRequest
from app.projects.contracts import Project


class PlanningCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    name: str
    input_kind: Literal["synthetic", "normalized_source"]
    units: Literal["m"]
    evidence_note: str
    project: Project
    request: PatternPreviewRequest

    @model_validator(mode="before")
    @classmethod
    def require_frozen_identity(cls, value: object) -> object:
        # Reject silent UUID/time defaults: an editable JSON must identify its
        # snapshot, not accidentally create a different project on each run.
        if not isinstance(value, dict) or not isinstance(value.get("project"), dict):
            return value
        project = value["project"]
        for key in ("id", "created_at", "updated_at"):
            if not project.get(key):
                raise ValueError(f"Frozen project requires {key}")
        plan = project.get("plan") or {}
        if not isinstance(plan, dict) or not plan.get("id"):
            raise ValueError("Frozen project requires plan.id")
        for collection in (
            project.get("layers", []),
            project.get("planting_zones", []),
            plan.get("objects", []),
            plan.get("issues", []),
        ):
            if not isinstance(collection, list) or any(
                not isinstance(item, dict) or not item.get("id") for item in collection
            ):
                raise ValueError("Frozen snapshot objects require explicit ids")
        return value

    @model_validator(mode="after")
    def require_analytical_snapshot(self) -> Self:
        if self.project.geometry is None or self.project.plan is None:
            raise ValueError("An analytical geometry snapshot and plan are required")
        return self
