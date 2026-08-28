from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.contracts import PatternPreviewRequest, PlantingZoneAssignment


@dataclass(frozen=True)
class PatternCandidate:
    x: float
    y: float


class CandidateGeneratorPort(Protocol):
    def generate(self, request: PatternPreviewRequest, zones: list[PlantingZoneAssignment]) -> list[PatternCandidate]: ...
