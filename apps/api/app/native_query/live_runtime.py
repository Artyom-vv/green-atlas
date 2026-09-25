"""Persisted opt-in for the DXF live demo; old projects keep their own engine.

An expired bound session NEVER falls back to sampled CAD calculations.
"""

import os
from pathlib import Path
from threading import Lock

from app.native_query.contracts import NativeDto
from app.native_query.live_client import LiveQueryClient, LiveQueryError, LiveSession
from app.native_query.live_provider import LiveNativeGeometryEngine
from app.planting_zones.domain import (
    validate_changed_planting_zones,
    validate_planting_zones,
)


class LiveBinding(NativeDto):
    project_id: str
    session: LiveSession
    linear_layers: frozenset[str] = frozenset()


def binding_path(database_path: str | Path) -> Path:
    return Path(str(database_path) + ".native-live.json")


def save_binding(database_path: str | Path, binding: LiveBinding) -> None:
    path = binding_path(database_path)
    temporary = path.with_suffix(".pending")
    with temporary.open("x", encoding="utf-8") as output:
        output.write(binding.model_dump_json(indent=2))
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


class BoundLiveGeometry:
    def __init__(self, binding: LiveBinding, legacy):
        self.binding = binding
        self._native = None
        self._native_lock = Lock()
        self.legacy = legacy

    @property
    def native(self):
        # Opening saved source data must not require files from a live session.
        # Native calculations still fail closed if that session has expired.
        with self._native_lock:
            if self._native is None:
                try:
                    self._native = LiveNativeGeometryEngine(
                        self.binding.session,
                        LiveQueryClient(pid=self.binding.session.pid),
                        linear_layers=self.binding.linear_layers,
                    )
                except FileNotFoundError as error:
                    raise LiveQueryError("live_session_expired") from error
            return self._native

    def __getattr__(self, name):
        def invoke(project, *args, **kwargs):
            engine = (
                self.native if project.id == self.binding.project_id else self.legacy
            )
            method = getattr(engine, name, None)
            if method is None and name in {"prepare_positions", "assert_current", "source_coverage"}:
                return None
            return method(project, *args, **kwargs)

        return invoke

    def validate_zones(self, project, zones):
        if project.id != self.binding.project_id:
            return validate_changed_planting_zones(project, zones)
        # A work area is a search domain, not an approval of its whole CAD area.
        # Per-position native site membership is still mandatory for planting.
        return validate_planting_zones(
            project, zones, changed_only=True, check_source_boundary=False
        )

    def explain_position(self, project, *args):
        if project.id != self.binding.project_id:
            return None
        return self.native.explain_position(project, *args)

    def review_area_group(self, project, request):
        if project.id != self.binding.project_id:
            raise ValueError('Для проверки области нужен подключённый сеанс AutoCAD')
        return self.native.review_area_group(project, request)


def configured_live_engine(database_path, legacy):
    path = binding_path(database_path)
    if not path.exists():
        return legacy, None
    if path.stat().st_size > 65536:
        raise ValueError("Live binding exceeds size limit")
    binding = LiveBinding.model_validate_json(path.read_bytes())
    engine = BoundLiveGeometry(binding, legacy)
    return engine, engine.validate_zones
