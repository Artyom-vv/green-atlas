from collections.abc import Callable
from threading import RLock
from typing import Protocol

from app.planting_zones.change_contracts import ZoneChangeResult
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.projects.ports import ProjectReader, ProjectSnapshotRepository

ZoneValidator = Callable[[Project, list[PlantingZoneAssignment]], None]


class ZoneProjectRepository(ProjectSnapshotRepository, Protocol):
    def save_with_receipt(
        self, project: Project, kind: str, mutation_id: str, receipt: dict
    ) -> Project: ...

    def mutation_receipt(
        self, project_id: str, kind: str, mutation_id: str
    ) -> dict | None: ...


class ZoneCommands(ProjectReader, Protocol):
    edit_lock: RLock

    def validate_planting_zones(
        self, project: Project, zones: list[PlantingZoneAssignment]
    ) -> None: ...
    def preview_planting_zone(
        self, project_id: str, zone: PlantingZoneAssignment
    ) -> dict[str, object]: ...
    def save_planting_zones(
        self,
        project_id: str,
        zones: list[PlantingZoneAssignment],
        *,
        preserve_plan: bool = False,
        mutation_receipt: dict | None = None,
    ) -> Project: ...
    def get_zone_change_receipt(
        self, project_id: str, preview_id: str, digest: str, base_state_version: int
    ) -> ZoneChangeResult | None: ...
