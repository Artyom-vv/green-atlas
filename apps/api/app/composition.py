"""Process composition for the API runtime.

The HTTP modules only depend on ``get_application``.  A runtime is created
once for the process, while tests and short lived tools can build an isolated
runtime explicitly with ``create_runtime`` and close it deterministically.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from app.agent_memory import ConversationStore
from app.agent_runtime.store import AgentRunStore
from app.application import ProjectApplication
from app.cad_intake.adapter import ProcessPackageInspection
from app.cad_intake.application import CadIntakeApplication
from app.cad_intake.asset_application import CadAssetApplication
from app.cad_intake.config import CadIntakeConfig
from app.cad_intake.prepare_adapter import ProcessCadProjectPreparation
from app.cad_intake.prepare_application import CadPrepareApplication
from app.cad_intake.preview_adapter import ProcessCadPreviewPreparation
from app.cad_intake.preview_application import CadPreviewApplication
from app.dxf_import.adapters import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.ports import GeometryEnginePort
from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.native_query.live_runtime import configured_live_engine
from app.native_query.prepared_snapshot import PreparedStore
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.adapters import SqliteOperationRepository
from app.planning.patterns import ShapelyCandidateGenerator
from app.planting_zones.ports import ZoneValidator
from app.projects.adapters import SqliteProjectRepository
from app.shared.identity import random_id, utc_now
from app.validation.adapters import RuleBasedPlanValidator


def default_database_path() -> str:
    return os.environ.get(
        "GREEN_ATLAS_DB_PATH",
        str(Path(__file__).resolve().parents[1] / "data" / "green-atlas.sqlite3"),
    )


@dataclass
class Runtime:
    database_path: str
    application: ProjectApplication
    project_repository: SqliteProjectRepository
    operation_repository: SqliteOperationRepository
    cad_intake: CadIntakeApplication
    cad_preview: CadPreviewApplication
    cad_assets: CadAssetApplication
    cad_prepare: CadPrepareApplication

    _closed: bool = False
    _conversations: ConversationStore | None = field(default=None, init=False)
    _agent_runs: AgentRunStore | None = field(default=None, init=False)
    _resource_lock: RLock = field(default_factory=RLock, init=False)

    @property
    def conversations(self) -> ConversationStore:
        with self._resource_lock:
            if self._closed:
                raise RuntimeError("Runtime is closed")
            if self._conversations is None:
                self._conversations = ConversationStore(self.database_path)
            return self._conversations

    @property
    def agent_runs(self) -> AgentRunStore:
        with self._resource_lock:
            if self._closed:
                raise RuntimeError("Runtime is closed")
            if self._agent_runs is None:
                self._agent_runs = AgentRunStore(self.database_path)
            return self._agent_runs

    def close(self) -> None:
        """Close runtime-owned resources once.

        ``SqliteProjectHistory`` shares the project repository connection, so
        closing the two owned repositories closes all resources created by the
        composition root exactly once.
        """

        with self._resource_lock:
            if self._closed:
                return
            self._closed = True
            if self._agent_runs is not None:
                self._agent_runs.close()
            if self._conversations is not None:
                self._conversations.close()
            self.operation_repository.close()
            self.project_repository.close()


def create_runtime(database_path: str | os.PathLike[str], *, geometry_engine: GeometryEnginePort | None = None,
                   zone_validator: ZoneValidator | None = None) -> Runtime:
    """Build an isolated application graph for one database path."""

    path = str(Path(database_path))
    cad_config = CadIntakeConfig.from_environment(path)
    project_repository = SqliteProjectRepository(path)
    operation_repository = SqliteOperationRepository(path)
    geometry = geometry_engine if geometry_engine is not None else ShapelyGeometryEngine()
    if geometry_engine is None:
        geometry, native_zones = configured_live_engine(path, geometry)
        geometry = ProjectLiveGeometry(geometry, native_zones, prepared_store=PreparedStore(
            Path(str(path) + ".prepared-geometry"),
        ))
        if zone_validator is None:
            zone_validator = geometry.validate_zones
    application = ProjectApplication(
        repository=project_repository,
        operation_repository=operation_repository,
        history=SqliteProjectHistory(project_repository),
        dxf_reader=EzdxfReader(),
        geometry=geometry,
        geometry_query=IndexedGeometryQuery(),
        validator=RuleBasedPlanValidator(geometry=geometry),
        writer=DxfRoundTripWriter(),
        candidate_generator=ShapelyCandidateGenerator(),
        zone_validator=zone_validator,
        now=utc_now,
        new_id=random_id,
    )
    return Runtime(
        database_path=path,
        application=application,
        project_repository=project_repository,
        operation_repository=operation_repository,
        cad_intake=CadIntakeApplication(
            cad_config,
            application.operations.lifecycle,
            ProcessPackageInspection(cad_config),
        ),
        cad_preview=CadPreviewApplication(
            cad_config,
            application.operations.lifecycle,
            ProcessCadPreviewPreparation(cad_config, path),
            application.spatial.invalidate,
        ),
        cad_assets=CadAssetApplication(cad_config, operation_repository),
        cad_prepare=CadPrepareApplication(
            cad_config,
            application.operations.lifecycle,
            ProcessCadProjectPreparation(cad_config, path),
            application.spatial.invalidate,
        ),
    )


_runtime: Runtime | None = None
_runtime_lock = RLock()


def get_runtime() -> Runtime:
    """Return the lazily-created process runtime."""

    global _runtime
    with _runtime_lock:
        if _runtime is None:
            _runtime = create_runtime(default_database_path())
        return _runtime


def get_application() -> ProjectApplication:
    return get_runtime().application


def get_cad_intake() -> CadIntakeApplication:
    return get_runtime().cad_intake


def get_cad_preview() -> CadPreviewApplication:
    return get_runtime().cad_preview


def get_cad_assets() -> CadAssetApplication:
    return get_runtime().cad_assets


def get_cad_prepare() -> CadPrepareApplication:
    return get_runtime().cad_prepare


def get_database_path() -> str:
    return get_runtime().database_path


def get_conversation_store() -> ConversationStore:
    return get_runtime().conversations


def get_agent_run_store() -> AgentRunStore:
    return get_runtime().agent_runs


def close_runtime() -> None:
    """Close and clear the process runtime for explicit session cleanup."""

    global _runtime
    with _runtime_lock:
        if _runtime is not None:
            _runtime.close()
            _runtime = None
