#!/usr/bin/env python3
"""Run sequential AutoCAD-native admission for a manifest of real DWG/DXF files.

This runner does not read DXF geometry and has no portable-parser fallback. It
invokes the same local MCP implementation used by ``autocad_prepare_dxf``,
records immutable-source hashes and writes an atomic receipt after every input.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import time
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MCP_PATH = ROOT / "tools" / "autocad-bridge" / "mcp" / "green_atlas_autocad_mcp.py"
MANIFEST_SCHEMA = "green-atlas.autocad-native-matrix-input/1"
REPORT_SCHEMA = "green-atlas.autocad-native-matrix-report/1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_bridge() -> ModuleType:
    spec = importlib.util.spec_from_file_location("green_atlas_autocad_mcp", MCP_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load AutoCAD MCP module: {MCP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def resolve_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    manifest = json.loads(raw)
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise ValueError(f"manifest schema must be {MANIFEST_SCHEMA}")
    drawings = manifest.get("drawings", [])
    roots = manifest.get("roots", [])
    if not isinstance(drawings, list) or not isinstance(roots, list):
        raise TypeError("manifest drawings and roots must be lists")
    if not drawings and not roots:
        raise ValueError("manifest must contain drawings or roots")
    seen: set[str] = set()
    for index, drawing in enumerate(drawings):
        if not isinstance(drawing, dict):
            raise TypeError(f"drawing {index} must be an object")
        drawing_id = drawing.get("id")
        source_path = drawing.get("source_path")
        if not isinstance(drawing_id, str) or not drawing_id or drawing_id in seen:
            raise ValueError(f"drawing {index} has an invalid or duplicate id")
        if not isinstance(source_path, str) or not source_path:
            raise ValueError(f"drawing {drawing_id} misses source_path")
        seen.add(drawing_id)
    for index, root in enumerate(roots):
        if not isinstance(root, dict):
            raise TypeError(f"root {index} must be an object")
        root_path = root.get("path")
        prefix = root.get("id_prefix")
        extensions = root.get("extensions", [".dwg", ".dxf"])
        if not isinstance(root_path, str) or not root_path:
            raise ValueError(f"root {index} misses path")
        if not isinstance(prefix, str) or not prefix:
            raise ValueError(f"root {index} misses id_prefix")
        if (
            not isinstance(extensions, list)
            or not extensions
            or any(
                not isinstance(extension, str)
                or extension.lower() not in {".dwg", ".dxf"}
                for extension in extensions
            )
        ):
            raise ValueError(f"root {index} has invalid extensions")
    return manifest, hashlib.sha256(raw).hexdigest()


def expand_drawings(manifest: dict[str, Any], base: Path) -> list[dict[str, Any]]:
    drawings = [dict(item) for item in manifest.get("drawings", [])]
    seen = {item["id"] for item in drawings}
    for configured in manifest.get("roots", []):
        root = resolve_path(configured["path"], base)
        if not root.is_dir():
            raise ValueError(f"matrix root is not a directory: {root}")
        extensions = {item.lower() for item in configured.get("extensions", [".dwg", ".dxf"])}
        for source in sorted(
            (
                path
                for path in root.rglob("*")
                if path.is_file() and path.suffix.lower() in extensions
            ),
            key=lambda path: path.relative_to(root).as_posix(),
        ):
            relative = source.relative_to(root).as_posix()
            drawing_id = f"{configured['id_prefix']}:{relative}"
            if drawing_id in seen:
                raise ValueError(f"duplicate discovered drawing id: {drawing_id}")
            seen.add(drawing_id)
            drawings.append(
                {
                    "id": drawing_id,
                    "source_path": str(source),
                    "package_root": str(root),
                    "timeout_seconds": configured.get("timeout_seconds", 600),
                }
            )
    return drawings


def write_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def run_matrix(
    manifest_path: Path,
    output_path: Path,
    *,
    bridge: ModuleType,
    resume: bool = False,
) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    output_path = output_path.resolve()
    manifest, manifest_sha256 = load_manifest(manifest_path)
    status = bridge.bridge_status()
    if not status.get("ready"):
        raise RuntimeError(str(status.get("reason") or "AutoCAD bridge is unavailable"))
    bridge_version = status.get("plugin_version")
    if bridge_version != bridge.PLUGIN_VERSION:
        raise RuntimeError(
            f"installed bridge {bridge_version!r} differs from runner {bridge.PLUGIN_VERSION!r}"
        )

    previous: dict[str, dict[str, Any]] = {}
    if resume and output_path.is_file():
        saved = json.loads(output_path.read_bytes())
        if (
            saved.get("schema") != REPORT_SCHEMA
            or saved.get("manifest_sha256") != manifest_sha256
            or saved.get("bridge", {}).get("plugin_version") != bridge_version
        ):
            raise ValueError(
                "existing report belongs to another manifest or bridge version"
            )
        previous = {
            row["id"]: row
            for row in saved.get("drawings", [])
            if isinstance(row, dict) and isinstance(row.get("id"), str)
        }

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "manifest_path": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "bridge": status,
        "drawings": [],
    }
    base = manifest_path.parent
    drawings = expand_drawings(manifest, base)
    for drawing in drawings:
        started = time.monotonic()
        source = resolve_path(drawing["source_path"], base)
        row: dict[str, Any] = {
            "id": drawing["id"],
            "source_path": str(source),
            "status": "rejected",
        }
        try:
            if source.suffix.lower() not in {".dwg", ".dxf"} or not source.is_file():
                raise ValueError("source must be an existing DWG or DXF file")
            before = sha256(source)
            row["source_sha256"] = before
            saved = previous.get(drawing["id"])
            if saved and saved.get("status") == "admitted":
                snapshot = Path(str(saved.get("snapshot_path", "")))
                if (
                    saved.get("source_sha256") == before
                    and saved.get("source_unchanged") is True
                    and snapshot.is_file()
                    and saved.get("snapshot_sha256") == sha256(snapshot)
                ):
                    row = {**saved, "reused_from_previous_run": True}
                    report["drawings"].append(row)
                    write_report(output_path, report)
                    print(
                        json.dumps(
                            {
                                "id": row["id"],
                                "status": "admitted",
                                "reused": True,
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                    continue
            expected = drawing.get("source_sha256")
            if expected is not None and expected != before:
                raise ValueError("source SHA-256 differs from manifest")
            arguments: dict[str, Any] = {
                "source_path": str(source),
                "timeout_seconds": int(drawing.get("timeout_seconds", 600)),
            }
            package_root = drawing.get("package_root")
            if package_root is not None:
                arguments["package_root"] = str(resolve_path(package_root, base))
            result = bridge.prepare_dxf(arguments)
            row.update(
                status="admitted",
                snapshot_path=result["snapshot_path"],
                snapshot_sha256=result["snapshot_sha256"],
                snapshot_bytes=result["snapshot_bytes"],
                native_evidence=result["native_evidence"],
                admission=result["admission"],
            )
            row["source_unchanged"] = sha256(source) == before
            if not row["source_unchanged"]:
                raise RuntimeError("AutoCAD-native preparation modified the source DXF")
        except (OSError, ValueError, RuntimeError, KeyError, subprocess.SubprocessError) as error:
            row.update(
                status="rejected",
                error_type=type(error).__name__,
                reason=str(error),
            )
            details = getattr(error, "details", None)
            if isinstance(details, dict):
                row.update(details)
            if source.is_file() and "source_sha256" in row:
                row["source_unchanged"] = sha256(source) == row["source_sha256"]
        row["seconds"] = time.monotonic() - started
        report["drawings"].append(row)
        write_report(output_path, report)
        print(
            json.dumps(
                {
                    "id": row["id"],
                    "status": row["status"],
                    "seconds": round(row["seconds"], 3),
                    "reason": row.get("reason"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    report["summary"] = {
        "total": len(drawings),
        "admitted": sum(row["status"] == "admitted" for row in report["drawings"]),
        "rejected": sum(row["status"] == "rejected" for row in report["drawings"]),
    }
    write_report(output_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse admitted rows only when source and snapshot hashes still match",
    )
    arguments = parser.parse_args()
    report = run_matrix(
        arguments.manifest,
        arguments.output,
        bridge=load_bridge(),
        resume=arguments.resume,
    )
    return 0 if report["summary"]["rejected"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
