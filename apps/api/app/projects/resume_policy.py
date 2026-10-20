"""Выбор экрана возобновления по сохранённому состоянию проекта независимо от
запущенного CAD-сеанса.
"""

from app.projects.contracts import Project, ProjectResumeKind


def resume_kind(project: Project) -> ProjectResumeKind:
    if project.plan is not None or project.map_ready or project.geometry is not None:
        return "workspace"
    if project.source_file is not None:
        return "source_preparation"
    return "awaiting_autocad"
