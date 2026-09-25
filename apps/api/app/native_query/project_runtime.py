"""Route each new AutoCAD project to its persisted, checked live capture."""

from threading import RLock

from app.native_query.domain_checkpoint import DomainCheckpointStore
from app.native_query.live_client import LiveQueryClient, LiveQueryError
from app.native_query.live_provider import LiveNativeGeometryEngine
from app.native_query.prepared_provider import PreparedGeometryEngine
from app.native_query.prepared_snapshot import preparation_key, prepare_snapshot
from app.planting_zones.domain import (
    validate_changed_planting_zones,
    validate_planting_zones,
)


class ProjectLiveGeometry:
    def __init__(self, compatibility, compatibility_zones=None, *, prepared_store=None):
        self.compatibility = compatibility
        self.compatibility_zones = compatibility_zones
        self._engines = {}
        self._lock = RLock()
        self.prepared_store = prepared_store
        self._prepared = {}

    def live_engine(self, project):
        source = getattr(project, "source_file", None)
        session = getattr(source, "native_session", None)
        if session is None:
            return self.compatibility
        if source.content_sha256 != session.snapshot_sha256:
            raise ValueError("Расчётный сеанс не соответствует исходным данным проекта")
        key = session.model_dump_json()
        with self._lock:
            current = self._engines.get(project.id)
            if current is None or current[0] != key:
                try:
                    engine = LiveNativeGeometryEngine(
                        session, LiveQueryClient(pid=session.pid)
                    )
                except FileNotFoundError as error:
                    raise LiveQueryError("live_session_expired") from error
                self._engines[project.id] = (key, engine)
            return self._engines[project.id][1]

    def engine(self, project):
        source = getattr(project, "source_file", None)
        session = getattr(source, "native_session", None)
        if session is None or self.prepared_store is None:
            return self.live_engine(project)
        key = preparation_key(project, session)
        with self._lock:
            cached = self._prepared.get(project.id)
            if cached is not None and cached._basis_key == key:
                return cached
            snapshot = self.prepared_store.load(key)
            if snapshot is None:
                snapshot = prepare_snapshot(self.live_engine(project), project)
                self.prepared_store.save(snapshot)
            engine = PreparedGeometryEngine(
                snapshot,
                project,
                checkpoints=DomainCheckpointStore(
                    self.prepared_store.directory / "domains",
                ),
            )
            self._prepared[project.id] = engine
            return engine

    def __getattr__(self, name):
        def invoke(project, *args, **kwargs):
            # Editing/reviewing CAD still requires AutoCAD. Planting does not.
            engine = (
                self.live_engine(project)
                if name in {"review_area_group", "native_face_review"}
                else self.engine(project)
            )
            method = getattr(engine, name, None)
            if method is None and name in {
                "prepare_positions",
                "assert_current",
                "explain_position",
                "source_coverage",
            }:
                return None
            if method is None:
                raise ValueError("Для действия требуется подключённый сеанс AutoCAD")
            return method(project, *args, **kwargs)

        return invoke

    def validate_zones(self, project, zones):
        if project.source_file and project.source_file.native_session:
            return validate_planting_zones(
                project, zones, changed_only=True, check_source_boundary=False
            )
        if self.compatibility_zones:
            return self.compatibility_zones(project, zones)
        return validate_changed_planting_zones(project, zones)
