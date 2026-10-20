"""Адаптер CAD-выпуска: сохранённый захват, отдельный процесс AutoCAD и новый DWG с
посадками.
"""

import json
from collections.abc import Callable
from pathlib import Path

from app.dxf_import.units import meters_per_dxf_unit
from app.exporting.cad_archive import CadArchiveProvider
from app.exporting.cad_contracts import CadPlant, CadRelease
from app.exporting.cad_process import write_cad_release
from app.native_query.capture.capture_contracts import (
    ARCHIVED_PLUGIN_VERSIONS,
    compatible_archive_worker,
)
from app.native_query.capture.capture_store import NativeCaptureStore
from app.native_query.transport.process_contracts import (
    NativeInputPackage,
    NativeQueryProcessConfig,
)
from app.native_query.transport.worker_config import (
    _autocad_installation as _autocad_installation,
)
from app.native_query.transport.worker_config import worker_config
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


def release_config(database_path: str) -> NativeQueryProcessConfig:
    return worker_config(database_path)


class AutoCadReleaseWriter:
    def __init__(
        self,
        database_path: str,
        archive: CadArchiveProvider | None = None,
        config_factory: Callable[[str], NativeQueryProcessConfig] = release_config,
        write: Callable[
            [NativeInputPackage, tuple[CadPlant, ...], int, NativeQueryProcessConfig],
            CadRelease,
        ] = write_cad_release,
    ) -> None:
        self.database_path = database_path
        self.archive = archive or CadArchiveProvider(
            NativeCaptureStore(
                Path(database_path + ".cad-archives"),
            )
        )
        self.config_factory = config_factory
        self.write = write

    def create(self, project: Project) -> CadRelease:
        if project.plan is None or not project.plan.objects:
            raise DomainFailure(FailureReason.CAD_RELEASE_PLANTINGS_REQUIRED)
        source = project.source_file
        session = source.native_session if source else None
        if source is None or session is None:
            raise DomainFailure(FailureReason.CAD_RELEASE_SOURCE_UNBOUND)
        scale = meters_per_dxf_unit(session.units_code)
        if scale is None:
            if session.units_code != 0 or not source.units_assumed:
                raise DomainFailure(FailureReason.CAD_RELEASE_SCALE_UNKNOWN)
            scale = 1.0  # Сохраняем то же явное допущение метров, что использовалось при импорте.
        plants = tuple(
            CadPlant(
                id=item.id,
                kind=item.kind,
                x=item.x / scale,
                y=item.y / scale,
                radius=item.radius / scale,
            )
            for item in project.plan.objects
        )
        package = self.archive.package(project)
        config = self.config_factory(self.database_path)
        if isinstance(config, NativeQueryProcessConfig) and not (
            compatible_archive_worker(session.plugin_version, config.plugin_version)
            if session.plugin_version in ARCHIVED_PLUGIN_VERSIONS
            else config.plugin_version == session.plugin_version
        ):
            raise DomainFailure(FailureReason.CAD_RELEASE_WORKER_INCOMPATIBLE)
        result = self.write(package, plants, session.units_code, config)
        result.files["green-atlas-plan.json"] = json.dumps(
            {
                "schema": "green-atlas.cad-plan/1",
                "project_id": project.id,
                "state_version": project.state_version,
                "geometry_version": project.geometry_version,
                "snapshot_sha256": session.snapshot_sha256,
                "cad_package_sha256": package.sha256,
                "source_sha256": session.source_sha256,
                "metres_per_unit": scale,
                "plan": project.plan.model_dump(mode="json"),
                "entry": result.entry,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        return result
