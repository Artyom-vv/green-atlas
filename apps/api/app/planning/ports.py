from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.contracts import BrushPreviewRequest, PatternPreviewRequest, PlantingZoneAssignment


@dataclass(frozen=True)
class PatternCandidate:
    x: float
    y: float
    kind: str | None = None


class CandidateGeneratorPort(Protocol):
    def generate(
        self,
        request: PatternPreviewRequest | BrushPreviewRequest,
        zones: list[PlantingZoneAssignment],
        guide_geometries: list[dict] | None = None,
    ) -> list[PatternCandidate]: ...
