"""Проверенное чтение состояния без координат сохранённых native CAD-графов."""

from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.projects.ports import ProjectPlacementReader, ProjectReader


def read_verified_project(
    repository: ProjectReader,
    project_id: str,
    *,
    metadata: Project | None = None,
) -> Project:
    """Сохранить проверки байтов и ревизий, не материализуя карту native-проекта.

    Старые inline-проекты и reader без этого optional-порта продолжают читать
    полное совместимое состояние. При гонке ревизий читается свежий снимок,
    как при прежнем единственном полном чтении.

    Проекция не возвращает source_geometry. Если потребителю нужно различать
    исходный и расчётный графы, он отдельно ограничивает применение этого пути.
    """
    project = (
        metadata
        if metadata is not None
        else repository.get(project_id, lightweight=True)
    )
    if (
        project.source_file is not None
        and project.source_file.native_session is not None
        and isinstance(repository, ProjectPlacementReader)
    ):
        try:
            projected = repository.get_placement_projection(
                project_id, expected_version=project.state_version
            )
        except ProjectVersionConflict:
            projected = None
        if projected is not None:
            return projected
    return repository.get(project_id)
