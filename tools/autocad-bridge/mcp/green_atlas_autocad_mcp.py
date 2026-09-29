#!/usr/bin/env python3
"""Local MCP transport for the Green Atlas ObjectARX bridge.

The server does not parse CAD geometry. It asks the already-running AutoCAD
plugin to open the exact DWG or DXF in an isolated AcDbDatabase, waits for the native
evidence file, and then runs the deterministic admission compiler.
"""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
COMPILER = ROOT / "scripts" / "cad-lab" / "compile_autocad_region_probe.py"
# Use the packaged plugin identity; a separate literal drifts after an update.
with (ROOT / "tools/autocad-bridge/native/Info.plist").open("rb") as metadata:
    PLUGIN_VERSION = plistlib.load(metadata)["CFBundleShortVersionString"]
QUEUE_VERSION = "v033"
PROTOCOL_VERSION = "2025-06-18"


class ToolFailure(RuntimeError):
    def __init__(self, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details = details or {}


def _compiler_python() -> Path:
    configured = os.environ.get("GREEN_ATLAS_API_PYTHON")
    candidates = [Path(configured).expanduser()] if configured else []
    candidates.extend(
        [
            ROOT / "apps" / "api" / ".venv" / "bin" / "python",
            ROOT / "apps" / "api" / ".venv" / "Scripts" / "python.exe",
            Path(sys.executable),
        ]
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            # A venv interpreter is commonly a symlink. Resolving it would
            # execute the base Python without the virtualenv site-packages.
            return candidate.absolute()
    raise ToolFailure(
        "Green Atlas API Python is unavailable; install apps/api dependencies"
    )


def _queue_directory() -> Path:
    return Path("/tmp") / f"green-atlas-autocad-mcp-{os.getuid()}-{QUEUE_VERSION}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bridge_status() -> dict[str, Any]:
    status_path = _queue_directory() / "status.json"
    if not status_path.is_file():
        return {
            "ready": False,
            "reason": "AutoCAD bridge status is absent; start or restart AutoCAD",
            "status_path": str(status_path),
        }
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ready": False,
            "reason": f"AutoCAD bridge status is unreadable: {exc}",
            "status_path": str(status_path),
        }
    pid = status.get("pid")
    process_alive: bool | None = False
    process_check = "missing-pid"
    if isinstance(pid, int) and pid > 0:
        try:
            os.kill(pid, 0)
            process_alive = True
            process_check = "confirmed"
        except PermissionError:
            # A sandboxed MCP host may be forbidden from probing an unrelated
            # GUI process even when that process belongs to the same user.
            # Treat EPERM as indeterminate; the queue request itself is the
            # authoritative liveness check.
            process_alive = None
            process_check = "permission-denied"
        except ProcessLookupError:
            process_check = "not-found"
        except OSError:
            process_check = "unavailable"
    reported_version = status.get("plugin_version")
    ready = (
        bool(status.get("ready"))
        and process_alive is not False
        and reported_version == PLUGIN_VERSION
    )
    progress = None
    progress_paths = sorted(
        _queue_directory().glob("progress-*.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if progress_paths:
        try:
            progress = json.loads(progress_paths[0].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            progress = None
    if process_alive is False:
        reason = "AutoCAD process is not running; open AutoCAD and try again"
    elif reported_version != PLUGIN_VERSION:
        reason = (
            "AutoCAD plugin version mismatch: "
            f"expected {PLUGIN_VERSION}, got {reported_version or 'not reported'}"
        )
    elif not status.get("ready"):
        reason = "AutoCAD plugin is not active"
    else:
        reason = None
    return {
        **status,
        "ready": ready,
        "process_alive": process_alive,
        "process_check": process_check,
        "status_path": str(status_path),
        "reason": reason,
        "active_request": progress,
    }


def _request_native_export(source: Path, timeout_seconds: int) -> dict[str, Any]:
    status = _bridge_status()
    if not status.get("ready"):
        raise ToolFailure(str(status.get("reason")))

    queue = _queue_directory()
    queue.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(queue, 0o700)
    request_id = uuid.uuid4().hex
    request_path = queue / f"request-{request_id}.txt"
    temporary_path = request_path.with_suffix(".tmp")
    response_path = queue / f"response-{request_id}.json"
    processing_path = queue / f"processing-{request_id}.txt"
    cancellation_path = queue / f"cancel-{request_id}.txt"
    temporary_path.write_text(f"{source}\n", encoding="utf-8")
    os.chmod(temporary_path, 0o600)
    temporary_path.replace(request_path)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if response_path.is_file():
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            finally:
                response_path.unlink(missing_ok=True)
            if response.get("request_id") != request_id:
                raise ToolFailure("AutoCAD bridge returned a mismatched request id")
            if not response.get("success"):
                raise ToolFailure(str(response.get("error") or "AutoCAD export failed"))
            output = Path(str(response.get("output_path", ""))).resolve()
            if not output.is_file():
                raise ToolFailure("AutoCAD reported success without an evidence file")
            return {
                **response,
                "output_path": str(output),
                "output_sha256": _sha256(output),
                "output_bytes": output.stat().st_size,
            }
        time.sleep(0.1)

    cancellation_path.write_text("cancel\n", encoding="utf-8")
    os.chmod(cancellation_path, 0o600)
    request_path.unlink(missing_ok=True)
    cancellation_deadline = time.monotonic() + 5
    while time.monotonic() < cancellation_deadline:
        if response_path.is_file():
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            finally:
                response_path.unlink(missing_ok=True)
            raise ToolFailure(
                str(response.get("error") or "AutoCAD export cancelled after timeout")
            )
        if not processing_path.exists():
            cancellation_path.unlink(missing_ok=True)
            break
        time.sleep(0.1)
    raise ToolFailure(
        f"AutoCAD did not answer within {timeout_seconds} seconds; "
        "native cancellation was requested"
    )


def _prepare_dxf(arguments: dict[str, Any]) -> dict[str, Any]:
    raw_path = arguments.get("source_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise ToolFailure("source_path is required")
    source = Path(raw_path).expanduser().resolve()
    if not source.is_absolute() or source.suffix.lower() not in {".dwg", ".dxf"}:
        raise ToolFailure("source_path must be an absolute DWG or DXF path")
    if not source.is_file():
        raise ToolFailure(f"drawing does not exist: {source}")
    if "\n" in str(source) or "\r" in str(source):
        raise ToolFailure("drawing path contains a line break")

    timeout = arguments.get("timeout_seconds", 600)
    if not isinstance(timeout, int) or not 10 <= timeout <= 1800:
        raise ToolFailure("timeout_seconds must be an integer from 10 to 1800")

    native = _request_native_export(source, timeout)
    probe = Path(native["output_path"])
    try:
        probe_payload = json.loads(probe.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolFailure(
            "AutoCAD returned unreadable native evidence",
            details={"native_evidence": native},
        ) from exc
    probe_diagnostics = {
        "summary": probe_payload.get("summary"),
        "limitations": probe_payload.get("limitations", []),
    }
    command = [
        str(_compiler_python()),
        str(COMPILER),
        str(probe),
        "--autocad-version",
        "2027.0.1",
        "--target",
        "macos-arm64",
    ]
    package_root = arguments.get("package_root")
    if package_root is not None:
        if not isinstance(package_root, str) or not package_root:
            raise ToolFailure("package_root must be a path string")
        package = Path(package_root).expanduser().resolve()
        if not package.is_dir():
            raise ToolFailure(f"package_root is not a directory: {package}")
        command.extend(["--package-root", str(package)])
    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise ToolFailure(
            f"native snapshot admission failed: {detail}",
            details={
                "native_evidence": native,
                "native_probe": probe_diagnostics,
                "admission_status": "rejected",
            },
        )
    try:
        admitted = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise ToolFailure("snapshot compiler returned invalid output") from exc
    snapshot_path = Path(admitted["output"]).resolve()
    if not snapshot_path.is_file():
        raise ToolFailure("snapshot compiler did not publish its output")
    return {
        "source_path": str(source),
        "source_sha256": _sha256(source),
        "native_evidence": native,
        "native_probe": probe_diagnostics,
        "snapshot_path": str(snapshot_path),
        "snapshot_sha256": _sha256(snapshot_path),
        "snapshot_bytes": snapshot_path.stat().st_size,
        "admission": admitted,
    }


# Read-only Python entry points for repository diagnostics. Production callers
# still cross the MCP tool boundary below; the matrix runner uses these names
# to exercise exactly the same implementation without duplicating transport.
bridge_status = _bridge_status
prepare_dxf = _prepare_dxf


TOOLS = [
    {
        "name": "autocad_bridge_status",
        "description": (
            "Check whether the installed Green Atlas ObjectARX bridge is active "
            "inside full AutoCAD for Mac. This is read-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "autocad_prepare_dxf",
        "description": (
            "Use full AutoCAD, not a portable parser, to extract native geometry "
            "evidence from a saved DWG or DXF and compile the adjacent verified Green "
            "Atlas snapshot. AutoCAD must be running with the bridge loaded."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "source_path": {"type": "string", "description": "Absolute DWG or DXF path"},
                "package_root": {
                    "type": "string",
                    "description": "Package root used to verify contributing XREF files",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 10,
                    "maximum": 1800,
                    "default": 600,
                },
            },
            "required": ["source_path"],
            "additionalProperties": False,
        },
    },
]


def _tool_result(value: dict[str, Any], *, error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, indent=2),
            }
        ],
        "structuredContent": value,
        "isError": error,
    }


def _handle(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        return None
    if method == "initialize":
        requested = message.get("params", {}).get("protocolVersion")
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "green-atlas-autocad",
                    "version": PLUGIN_VERSION,
                },
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = message.get("params", {})
        name = params.get("name")
        arguments = params.get("arguments") or {}
        try:
            if name == "autocad_bridge_status":
                result = _tool_result(_bridge_status())
            elif name == "autocad_prepare_dxf":
                result = _tool_result(_prepare_dxf(arguments))
            else:
                result = _tool_result({"error": f"unknown tool: {name}"}, error=True)
        except ToolFailure as exc:
            result = _tool_result(
                {"error": str(exc), **exc.details},
                error=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            result = _tool_result({"error": str(exc)}, error=True)
        return {"jsonrpc": "2.0", "id": request_id, "result": result}
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    for line in sys.stdin:
        try:
            message = json.loads(line)
            response = _handle(message)
            if response is not None:
                print(
                    json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                    flush=True,
                )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            print(f"green-atlas-autocad MCP error: {exc}", file=sys.stderr, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
