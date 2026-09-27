"""Optional, snapshot-bound prewarming for shared geometry consumers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.geometry.ports import GeometryEnginePort
    from app.projects.contracts import Project


def prepare_positions(
    geometry: GeometryEnginePort,
    project: Project,
    points: list[tuple[float, float]],
) -> None:
    """Prepare raw measurements once, without caching or suppressing failures."""
    prepare = getattr(geometry, "prepare_positions", None)
    if prepare is not None:
        prepare(project, points)
