"""Rebind is identity-only; never a source reimport or plan migration."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import app.desktop.live_demo as demo
import app.native_query.live_runtime as live_runtime
from app.native_query.live_client import LiveQueryError, LiveSession
from app.native_query.live_runtime import (
    BoundLiveGeometry,
    LiveBinding,
    binding_path,
    configured_live_engine,
    save_binding,
)


def binding():
    session = LiveSession(session_id="a" * 32, pid=123, plugin_version="0.1.38",
        source_path="/source.dxf", source_sha256="b" * 64,
        snapshot_path="/map.json", snapshot_sha256="c" * 64,
        inventory_path="/map.json.inventory.json", inventory_sha256="d" * 64,
        units_code=6, watched_databases=8, unavailable_xrefs=1)
    return LiveBinding(project_id="project", session=session, linear_layers={"curb"})


@pytest.mark.parametrize("field,value", [("source_sha256", "e" * 64),
    ("snapshot_sha256", "e" * 64), ("inventory_sha256", "e" * 64),
    ("units_code", 4), ("watched_databases", 7), ("unavailable_xrefs", 2)])
def test_changed_capture_never_replaces_binding(tmp_path, field, value):
    database = tmp_path / "project.sqlite"
    old = binding()
    save_binding(database, old)
    client = Mock()
    client.open.return_value = old.session.model_copy(update={field: value})
    with pytest.raises(ValueError, match="Исходные данные изменились"):
        demo.rebind_project(database, "project", client)
    assert LiveBinding.model_validate_json(binding_path(database).read_bytes()) == old


def test_same_capture_reconnect_preserves_project_and_policy(tmp_path, monkeypatch):
    database = tmp_path / "project.sqlite"
    old = binding()
    save_binding(database, old)
    client = Mock()
    current = old.session.model_copy(update={"session_id": "f" * 32, "pid": 456})
    client.open.return_value = current
    runtime = Mock()
    runtime.application.get.return_value = SimpleNamespace(id="project",
        source_file=SimpleNamespace(content_sha256=current.snapshot_sha256))
    monkeypatch.setattr(demo, "create_runtime", lambda _: runtime)
    monkeypatch.setattr(demo, "load_inventory", lambda _: None)
    demo.rebind_project(database, "project", client)
    saved = LiveBinding.model_validate_json(binding_path(database).read_bytes())
    assert saved.session == current and saved.linear_layers == old.linear_layers
    runtime.application.import_autocad_live.assert_not_called()
    runtime.project_repository.save.assert_not_called()
    client.inspect.assert_called_once_with(current)
    runtime.close.assert_called_once()


def test_saved_runtime_opens_without_loading_expired_native_files(tmp_path, monkeypatch):
    database = tmp_path / "project.sqlite"
    save_binding(database, binding())
    constructor = Mock(side_effect=FileNotFoundError("expired inventory"))
    monkeypatch.setattr(live_runtime, "LiveNativeGeometryEngine", constructor)
    legacy = Mock()
    engine, zones = configured_live_engine(database, legacy)
    constructor.assert_not_called()
    assert zones == engine.validate_zones
    with pytest.raises(LiveQueryError) as error:
        engine.calculate(SimpleNamespace(id="project"))
    assert error.value.code == "live_session_expired"
    legacy.calculate.assert_not_called()


def test_native_engine_initializes_once_when_a_calculation_needs_it(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(live_runtime, "LiveNativeGeometryEngine", constructor)
    engine = BoundLiveGeometry(binding(), Mock())
    project = SimpleNamespace(id="project")
    engine.calculate(project)
    engine.explain_position(project, 1, 2)
    constructor.assert_called_once()
    constructor.return_value.calculate.assert_called_once_with(project)
    constructor.return_value.explain_position.assert_called_once_with(project, 1, 2)
    engine.legacy.calculate.assert_not_called()


def test_other_project_does_not_require_bound_native_inventory(monkeypatch):
    constructor = Mock(side_effect=FileNotFoundError("expired inventory"))
    monkeypatch.setattr(live_runtime, "LiveNativeGeometryEngine", constructor)
    legacy = Mock()
    engine = BoundLiveGeometry(binding(), legacy)
    project = SimpleNamespace(id="another-project")
    engine.calculate(project)
    constructor.assert_not_called()
    legacy.calculate.assert_called_once_with(project)


def test_optional_coverage_does_not_require_cad_for_a_legacy_project():
    engine = BoundLiveGeometry(binding(), SimpleNamespace())
    assert engine.source_coverage(SimpleNamespace(id="another-project")) is None
