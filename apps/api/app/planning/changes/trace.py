"""Привязка объяснений применённых правил к конкретной операции изменения посадки."""

from app.geometry.ports import GeometryEnginePort
from app.planning.changes.contracts import PlanChangeOperation
from app.projects.contracts import Project
from app.regulations.trace_contracts import PlantingRuleTrace
from app.species.rule_context import mature_crown_diameter


def operation_rule_trace(
    geometry: GeometryEnginePort,
    project: Project,
    operation: PlanChangeOperation,
) -> PlantingRuleTrace | None:
    """Объясняет запрошенную позицию даже после отказа на более ранней проверке."""
    if operation.type == "delete":
        return None
    if operation.type == "add":
        candidate = operation.object
        return geometry.position_rule_trace(
            project,
            candidate.x,
            candidate.y,
            candidate.kind,
            mature_crown_diameter(candidate.species_revision_id),
        )
    if project.plan is None:
        return None
    current = next(
        (item for item in project.plan.objects if item.id == operation.object_id), None
    )
    if current is None:
        return None
    changes = operation.changes.model_dump(exclude_unset=True)
    x, y = changes.get("x", current.x), changes.get("y", current.y)
    if x is None or y is None:
        return None
    return geometry.position_rule_trace(
        project,
        x,
        y,
        current.kind,
        mature_crown_diameter(
            changes.get("species_revision_id", current.species_revision_id)
        ),
    )
