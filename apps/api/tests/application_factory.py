"""Compose isolated test applications instead of mutating wired dependencies."""

from datetime import UTC, datetime

from app.application import ProjectApplication
from app.dxf_import.application import ImportApplication
from app.dxf_import.source.assembly import assemble_imported_project
from app.geometry.ports import GeometryQueryPort
from app.history.ports import ProjectHistoryPort
from app.projects.contracts import Project
from app.projects.ports import ProjectRepository
from app.validation.ports import PlanValidatorPort


def save_legacy_dxf_fixture(
    application: ProjectApplication | ImportApplication,
    project_id: str,
    filename: str,
    content: bytes | bytearray,
) -> Project:
    """Seed an already saved legacy project without exposing an import route."""
    project = application.repository.get(project_id)
    imported = application.dxf_reader.read(filename, content)
    prepared = assemble_imported_project(
        project, filename, content, imported, datetime.now(UTC).isoformat()
    )
    return application.repository.save(prepared, source=content)


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
        geometry_query=template.geometry_query
        if geometry_query is None
        else geometry_query,
        validator=template.validator if validator is None else validator,
        writer=template.writer,
        candidate_generator=template.candidate_generator,
    )
