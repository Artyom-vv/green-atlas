"""Explicit layer review for legacy reader geometry controls.

These fixtures test geometry after a known mapping decision. Reader output
still remains unconfirmed until the fixture owner accepts those roles.
"""

from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.contracts import GeometrySnapshot
from app.operations.progress import ProgressReporter
from app.projects.contracts import Project


def calculate_confirmed_fixture(
    project: Project,
    progress: ProgressReporter | None = None,
    *,
    engine: ShapelyGeometryEngine | None = None,
) -> GeometrySnapshot:
    for layer in project.layers:
        if layer.mapping_review_required:
            layer.mapped_kind = layer.mapped_kind or layer.suggested_kind
            layer.mapping_confirmed = True
    return (engine or ShapelyGeometryEngine()).calculate(project, progress)
