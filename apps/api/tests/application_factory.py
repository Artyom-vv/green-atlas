"""Compose isolated test applications instead of mutating wired dependencies."""

from app.application import ProjectApplication
from app.geometry.ports import GeometryQueryPort
from app.history.ports import ProjectHistoryPort
from app.projects.ports import ProjectRepository
from app.validation.ports import PlanValidatorPort


def recompose_application(
    template: ProjectApplication,
    *,
    repository: ProjectRepository | None = None,
    history: ProjectHistoryPort | None = None,
    geometry_query: GeometryQueryPort | None = None,
    validator: PlanValidatorPort | None = None,
) -> ProjectApplication:
    return ProjectApplication(
        repository=template.repository if repository is None else repository,
        operation_repository=template.operation_repository,
        history=template.history if history is None else history,
        dxf_reader=template.dxf_reader,
        geometry=template.geometry,
        geometry_query=template.geometry_query if geometry_query is None else geometry_query,
        validator=template.validator if validator is None else validator,
        writer=template.writer,
        candidate_generator=template.candidate_generator,
    )
