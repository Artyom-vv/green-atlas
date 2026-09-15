"""Scene reads only load source data; the projection is independently testable."""

from collections.abc import Callable

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
        return build_scene(
            self.repository.get(project_id), horizon_year, species=self.species
        )
