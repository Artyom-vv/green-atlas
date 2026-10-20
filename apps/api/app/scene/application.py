"""Чтение сцены по проекту и каталогу видов; чистая сборка представления проверяется
независимо от хранилища.
"""

from collections.abc import Callable

from app.projects.contracts import Project
from app.projects.ports import ProjectReader
from app.scene.contracts import SceneSnapshot
from app.scene.domain import build_scene, validate_horizon
from app.species.contracts import SpeciesRevision


class SceneApplication:
    def __init__(
        self, repository: ProjectReader, species: Callable[[str], SpeciesRevision]
    ) -> None:
        self.repository = repository
        self.species = species

    def get_scene(self, project_id: str, horizon_year: int) -> SceneSnapshot:
        validate_horizon(horizon_year)
        return self.from_project(self.repository.get(project_id), horizon_year)

    def from_project(self, project: Project, horizon_year: int) -> SceneSnapshot:
        """Использует снимок публикации без повторной загрузки большого CAD-проекта."""
        return build_scene(project, horizon_year, species=self.species)
