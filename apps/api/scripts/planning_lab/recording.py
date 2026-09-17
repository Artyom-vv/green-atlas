"""Observe existing ports without changing their requests, order or decisions."""

from dataclasses import asdict
from time import perf_counter

from app.planning.change_contracts import ChangeSetPreview, PlanChangeSetDraft
from app.planning.pattern_contracts import BrushPreviewRequest, PatternPreviewRequest
from app.planning.ports import (
    CandidateGeneratorPort,
    ChangeSetPreviewPort,
    PatternCandidate,
)
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project

from .evidence import digest


class RecordingGenerator:
    def __init__(self, delegate: CandidateGeneratorPort) -> None:
        self.delegate = delegate
        self.calls: list[dict] = []
        self.seconds: list[float] = []

    def generate(
        self,
        request: PatternPreviewRequest | BrushPreviewRequest,
        zones: list[PlantingZoneAssignment],
        guide_geometries: list[dict] | None = None,
    ) -> list[PatternCandidate]:
        start = perf_counter()
        candidates = self.delegate.generate(request, zones, guide_geometries)
        self.seconds.append(perf_counter() - start)
        self.calls.append(
            {
                "effective_request": request.model_dump(mode="json"),
                "generation_zones_sha256": digest(
                    [z.model_dump(mode="json") for z in zones]
                ),
                "guide_geometries_sha256": digest(guide_geometries),
                "candidates": [asdict(candidate) for candidate in candidates],
            }
        )
        return candidates


class RecordingPreviews:
    def __init__(self, delegate: ChangeSetPreviewPort) -> None:
        self.delegate = delegate
        self.calls: list[dict] = []
        self.seconds: list[float] = []

    def preview_on_snapshot(
        self, project: Project, draft: PlanChangeSetDraft, *, cache_preview: bool = True
    ) -> ChangeSetPreview:
        start = perf_counter()
        result = self.delegate.preview_on_snapshot(
            project, draft, cache_preview=cache_preview
        )
        self.seconds.append(perf_counter() - start)
        self.calls.append(
            {
                "cache_preview": cache_preview,
                "draft": draft.model_dump(mode="json"),
                "preview": result.model_dump(mode="json"),
            }
        )
        return result
