from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.planning.change_contracts import (
    ChangeSetPreview,
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanMutationResult,
)
from app.planning.pattern_contracts import BrushPreviewRequest, PatternPreviewRequest
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project


@dataclass(frozen=True)
class PatternCandidate:
    x: float
    y: float
    kind: str | None = None
    group_key: str | None = None


class CandidateGeneratorPort(Protocol):
    def generate(
        self,
        request: PatternPreviewRequest | BrushPreviewRequest,
        zones: list[PlantingZoneAssignment],
        guide_geometries: list[dict] | None = None,
    ) -> list[PatternCandidate]: ...


class ChangeSetPreviewPort(Protocol):
    """Evaluate the same snapshot that supplied the generation version basis."""

    def preview_on_snapshot(
        self, project: Project, draft: PlanChangeSetDraft, *, cache_preview: bool = True
    ) -> ChangeSetPreview: ...


class ManualChangeSetPort(ChangeSetPreviewPort, Protocol):
    def apply_change_set(
        self, project_id: str, payload: PlanChangeSetApplyRequest
    ) -> PlanMutationResult: ...
