from __future__ import annotations

import json
import plistlib
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import green_atlas_autocad_mcp
import pytest
from green_atlas_autocad_mcp import (
    PLUGIN_VERSION,
    TOOLS,
    ToolFailure,
    _bridge_status,
    _compiler_python,
    _handle,
    _prepare_dxf,
    _request_native_export,
)


def test_initialize_and_list_tools() -> None:
    initialized = _handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        }
    )
    assert initialized is not None
    assert initialized["result"]["serverInfo"]["name"] == "green-atlas-autocad"

    listed = _handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    assert listed["result"]["tools"] == TOOLS


def test_stdio_payload_is_json_serializable() -> None:
    response = _handle({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert json.loads(json.dumps(response))["result"] == {}


def test_unknown_tool_is_a_tool_error() -> None:
    response = _handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "missing", "arguments": {}},
        }
    )
    assert response is not None
    assert response["result"]["isError"] is True


def test_bridge_status_accepts_sandboxed_process_probe(tmp_path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "status.json").write_text(
        json.dumps({"ready": True, "plugin_version": PLUGIN_VERSION, "pid": 42})
    )
    monkeypatch.setattr(green_atlas_autocad_mcp, "_queue_directory", lambda: queue)

    def deny_process_probe(pid: int, signal: int) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(green_atlas_autocad_mcp.os, "kill", deny_process_probe)

    status = _bridge_status()

    assert status["ready"] is True
    assert status["process_alive"] is None
    assert status["process_check"] == "permission-denied"


def test_bridge_status_reports_native_progress(tmp_path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "status.json").write_text(
        json.dumps({"ready": True, "plugin_version": PLUGIN_VERSION, "pid": 42})
    )
    progress = {
        "schema": "green-atlas.autocad-mcp-progress/1",
        "request_id": "a" * 32,
        "phase": "collecting",
        "processed_entities": 20000,
    }
    (queue / f"progress-{'a' * 32}.json").write_text(json.dumps(progress))
    monkeypatch.setattr(green_atlas_autocad_mcp, "_queue_directory", lambda: queue)
    monkeypatch.setattr(green_atlas_autocad_mcp.os, "kill", lambda _pid, _signal: None)

    status = _bridge_status()

    assert status["active_request"] == progress


def test_bridge_status_rejects_stale_plugin_version(tmp_path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    (queue / "status.json").write_text(
        json.dumps({"ready": True, "plugin_version": "0.1.28", "pid": 42})
    )
    monkeypatch.setattr(green_atlas_autocad_mcp, "_queue_directory", lambda: queue)
    monkeypatch.setattr(green_atlas_autocad_mcp.os, "kill", lambda _pid, _signal: None)

    status = _bridge_status()

    assert status["ready"] is False
    assert f"expected {PLUGIN_VERSION}, got 0.1.28" in status["reason"]


def test_transport_uses_packaged_plugin_version() -> None:
    root = Path(__file__).resolve().parents[1]
    with (root / "native/Info.plist").open("rb") as stream:
        assert PLUGIN_VERSION == plistlib.load(stream)["CFBundleShortVersionString"]
    assert PLUGIN_VERSION == ET.parse(root / "PackageContents.xml").getroot().attrib["AppVersion"]
    assert f'kPluginVersion = "{PLUGIN_VERSION}"' in (root / "native/bridge_config.h").read_text()


@pytest.mark.parametrize("reported", [PLUGIN_VERSION, "0.1.28"])
def test_dead_process_is_not_reported_as_version_mismatch(tmp_path, monkeypatch, reported) -> None:
    (tmp_path / "status.json").write_text(
        json.dumps({"ready": True, "plugin_version": reported, "pid": 42})
    )
    monkeypatch.setattr(green_atlas_autocad_mcp, "_queue_directory", lambda: tmp_path)

    def missing_process(pid, sig):
        raise ProcessLookupError()

    monkeypatch.setattr(green_atlas_autocad_mcp.os, "kill", missing_process)
    result = _bridge_status()
    assert result["ready"] is False
    assert result["process_check"] == "not-found"
    assert "process is not running" in result["reason"]
    assert "version mismatch" not in result["reason"]


def test_inactive_plugin_is_not_reported_as_version_mismatch(tmp_path, monkeypatch) -> None:
    (tmp_path / "status.json").write_text(
        json.dumps({"ready": False, "plugin_version": PLUGIN_VERSION, "pid": 42})
    )
    monkeypatch.setattr(green_atlas_autocad_mcp, "_queue_directory", lambda: tmp_path)
    monkeypatch.setattr(green_atlas_autocad_mcp.os, "kill", lambda *_: None)
    result = _bridge_status()
    assert result["ready"] is False
    assert result["reason"] == "AutoCAD plugin is not active"


def test_timeout_requests_native_cancellation(tmp_path, monkeypatch) -> None:
    queue = tmp_path / "queue"
    queue.mkdir()
    source = tmp_path / "source.dxf"
    source.write_text("fixture")
    request_id = "b" * 32
    (queue / f"processing-{request_id}.txt").write_text(str(source))
    clock = iter((0.0, 0.0, 0.0, 6.0))
    monkeypatch.setattr(green_atlas_autocad_mcp, "_queue_directory", lambda: queue)
    monkeypatch.setattr(
        green_atlas_autocad_mcp,
        "_bridge_status",
        lambda: {"ready": True},
    )
    monkeypatch.setattr(
        green_atlas_autocad_mcp.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex=request_id),
    )
    monkeypatch.setattr(green_atlas_autocad_mcp.time, "monotonic", lambda: next(clock))

    try:
        _request_native_export(source, 0)
    except ToolFailure as exc:
        assert "native cancellation was requested" in str(exc)
    else:
        raise AssertionError("timeout must fail")

    assert (queue / f"cancel-{request_id}.txt").read_text() == "cancel\n"


def test_compiler_uses_project_api_environment(tmp_path, monkeypatch) -> None:
    interpreter = tmp_path / "apps" / "api" / ".venv" / "bin" / "python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    monkeypatch.setattr(green_atlas_autocad_mcp, "ROOT", tmp_path)
    monkeypatch.delenv("GREEN_ATLAS_API_PYTHON", raising=False)

    assert _compiler_python() == interpreter.absolute()
    assert _compiler_python().is_symlink()


def test_prepare_accepts_dwg_through_autocad_native_boundary(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "street.DWG"
    source.write_bytes(b"native-dwg-fixture")
    probe = tmp_path / "street.geometry.json"
    probe.write_text(json.dumps({"summary": {"native": 4}, "limitations": []}))
    snapshot = tmp_path / "street.snapshot.json"
    snapshot.write_text("{}")
    monkeypatch.setattr(
        green_atlas_autocad_mcp,
        "_request_native_export",
        lambda path, timeout: {"output_path": str(probe)},
    )
    monkeypatch.setattr(
        green_atlas_autocad_mcp.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 0, json.dumps({"output": str(snapshot)}), ""
        ),
    )

    result = _prepare_dxf({"source_path": str(source)})

    assert result["source_path"] == str(source)
    assert result["snapshot_path"] == str(snapshot)
    assert result["native_probe"]["summary"] == {"native": 4}


def test_admission_failure_keeps_autocad_native_diagnostics(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "partial.dwg"
    source.write_bytes(b"dwg")
    probe = tmp_path / "partial.geometry.json"
    probe.write_text(
        json.dumps(
            {
                "summary": {
                    "source_instances": 12,
                    "native": 9,
                    "unresolved_instances": 3,
                },
                "limitations": ["three unresolved instances"],
            }
        )
    )
    monkeypatch.setattr(
        green_atlas_autocad_mcp,
        "_request_native_export",
        lambda path, timeout: {"output_path": str(probe), "output_sha256": "a" * 64},
    )
    monkeypatch.setattr(
        green_atlas_autocad_mcp.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 2, "", "unresolved XREF"
        ),
    )

    try:
        _prepare_dxf({"source_path": str(source)})
    except ToolFailure as error:
        assert error.details["admission_status"] == "rejected"
        assert error.details["native_probe"]["summary"]["native"] == 9
        assert error.details["native_probe"]["summary"]["unresolved_instances"] == 3
    else:
        raise AssertionError("incomplete native evidence was admitted")


def test_native_hatch_path_guards_edge_apis_and_preserves_islands() -> None:
    """Typed polyline value arrays must not re-enable unsafe edge conversion."""

    native = Path(__file__).resolve().parents[1] / "native"
    source = (native / "entity_extraction.cpp").read_text()

    assert "hatch->getRegionArea" not in source
    assert "hatch->getArea" not in source
    active_hatch_branch = source.split(
        "} else if (AcDbHatch* hatch = AcDbHatch::cast(entity)) {", 1
    )[1].split("} else if (AcDbMline* mline", 1)[0]
    assert "getLoopAt" not in active_hatch_branch
    assert "numLoops" not in active_hatch_branch
    assert "extractPolylineHatchTopology" in active_hatch_branch
    extractor = (native / "hatch_extraction.cpp").read_text()
    assert "AcGeVoidPointerArray" not in extractor
    assert "hatch->getRegionArea" in extractor
    assert "delete areaRegion" in extractor
    assert "hatch->getArea" not in extractor
    call = extractor.index("hatch->getLoopAt(")
    assert extractor.index("hatch->numLoops()") < call
    assert extractor.index("!(type & AcDbHatch::kPolyline)") < call
    assert extractor.index("AcDbHatch::kSelfIntersecting") < call
    assert "getAssocObjIdsAt" in extractor
    capture = (native / "capture_commands.cpp").read_text()
    references = (native / "xref_resolver.cpp").read_text()
    traversal = (native / "block_traversal.cpp").read_text()
    assert "relinkUniquePackageLocalXrefs" in capture
    assert "findReferencePaths(parentDirectory(sourcePath), requests)" in references
    assert "traverse-package-local-xref-database" in traversal
    assert "acdbResolveCurrentXRefs(&sourceDatabase" not in capture
    assert "externalDatabases.modelSpace(" in traversal
