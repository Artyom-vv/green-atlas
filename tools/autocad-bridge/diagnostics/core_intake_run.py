"""Experimental saved DWG intake through Core, without a GUI session.

Private copies, production capture, exact source hashes and independent reopen.
This does not install an importer or publish into the user's project library.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from capture_session_run import (
    ACAD,
    CORE,
    REPO,
    SUPPORTED_MAC_ARCHITECTURES,
    build,
    digest,
    geometry,
)
from core_intake_policy import assess_input
from product_capture_run import evaluate

MIN_FREE_BYTES = 2 * 1024**3
MAX_PACKAGE_BYTES = 2 * 1024**3
SHUTDOWN_GRACE_SECONDS = 15
TERMINATE_GRACE_SECONDS = 3
POLL_SECONDS = 0.2
DEFAULT_TIMEOUT_SECONDS = 180
MAX_TIMEOUT_SECONDS = 600
ASSET_SUFFIXES = {".dwg", ".dxf", ".pdf", ".png", ".jpg", ".jpeg", ".shx", ".ttf"}


def save(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def stage_package(
    source: Path, package: Path, destination: Path
) -> list[dict[str, Any]]:
    source = source.resolve(strict=True)
    package = package.resolve(strict=True)
    if not source.is_relative_to(package) or source.suffix.lower() != ".dwg":
        raise ValueError("Supply a DWG inside the explicit package directory")
    if destination.resolve().is_relative_to(package):
        raise ValueError("Experiment output must be outside the source package")
    files = []
    total = 0
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"Package symlink requires explicit resolution: {path}")
        if not path.is_file() or path.suffix.lower() not in ASSET_SUFFIXES:
            continue
        total += path.stat().st_size
        if total > MAX_PACKAGE_BYTES:
            raise ValueError("Experiment package exceeds the 2 GiB limit")
        files.append(path)
    destination.mkdir()
    records = []
    for path in files:
        target = destination / path.relative_to(package)
        target.parent.mkdir(parents=True, exist_ok=True)
        before = digest(path)
        shutil.copy2(path, target)
        if digest(target) != before or digest(path) != before:
            raise ValueError(f"Source changed while staging: {path}")
        records.append({"source": str(path), "copy": str(target), "sha256": before})
    return records


def stop_owned(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is not None:
        return
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=TERMINATE_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def run_stage(
    root: Path,
    bundle: Path,
    library: Path,
    action: str,
    drawing: Path | None,
    timeout: int,
    fixture: str,
    database_mode: str = "isolated",
    split_jobs: bool = False,
    architecture: str = "x86_64",
) -> dict[str, Any]:
    if architecture not in SUPPORTED_MAC_ARCHITECTURES:
        raise ValueError("Unsupported Mac Core architecture")
    stage = root / action
    stage.mkdir()
    profile = stage / "profile"
    profile.mkdir()
    bootstrap = stage / "Bootstrap.dwg"
    shutil.copy2(
        ACAD / "Contents/Resources/UserDataCache/en-us/Template/acadiso.dwt", bootstrap
    )
    script = stage / "run.scr"
    script.write_text(
        '(setvar "FILEDIA" 0)\n(setvar "SECURELOAD" 2)\n'
        '(setvar "LEGACYCODESEARCH" 0)\n(setvar "ACADLSPASDOC" 0)\n'
        '(setvar "APPAUTOLOAD" 0)\n'
        '(setvar "TRUSTEDPATHS" (getenv "GA_CORE_INTAKE_TRUSTED"))\n'
        '(arxload (getenv "GA_CORE_INTAKE_BUNDLE"))\n'
        + (
            '(command "_.OPEN" (getenv "GA_CORE_INTAKE_DRAWING"))\n'
            if drawing and database_mode == "active"
            else ""
        )
        + "GAINTAKEFILE\n_QUIT\n_Y\n\n",
        encoding="ascii",
    )
    command = [
        "/usr/bin/arch",
        "-" + architecture,
        str(CORE),
        "/i",
        str(bootstrap),
        "/s",
        str(script),
        "/isolate",
        "ga-intake-" + uuid.uuid4().hex,
        str(profile),
    ]
    env = dict(
        os.environ,
        GA_CORE_INTAKE_ROOT=str(root),
        GA_CORE_INTAKE_ACTION=action,
        GA_CORE_INTAKE_BUNDLE=str(bundle),
        GA_CORE_INTAKE_DRAWING=str(drawing or ""),
        GA_CORE_INTAKE_TRUSTED=str(bundle.parent) + "/...",
        GA_PRODUCTION_CAPTURE_LIBRARY=str(library),
        GA_CORE_INTAKE_FIXTURE=fixture,
        GA_CORE_INTAKE_DATABASE_MODE=database_mode,
        GA_CORE_INTAKE_SPLIT_JOBS="1" if split_jobs else "0",
    )
    started = time.monotonic()
    completed_at = None
    terminated = False
    child = None
    result: dict[str, Any] = {}
    try:
        with (stage / "core.log").open("xb") as log:
            child = subprocess.Popen(
                command,
                env=env,
                cwd=stage,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            while child.poll() is None:
                completed = (root / f"{action}-native-completed.json").exists()
                failed = (root / f"{action}-native-error.json").exists()
                if (completed or failed) and completed_at is None:
                    completed_at = time.monotonic()
                if time.monotonic() - started > timeout or (
                    completed_at is not None
                    and time.monotonic() - completed_at > SHUTDOWN_GRACE_SECONDS
                ):
                    stop_owned(child)
                    terminated = True
                    break
                time.sleep(POLL_SECONDS)
            result = {
                "command": command,
                "architecture": architecture,
                "pid": child.pid,
                "exit_code": child.returncode,
                "seconds": round(time.monotonic() - started, 3),
                "terminated": terminated,
                "native_completed": (root / f"{action}-native-completed.json").exists(),
            }
    finally:
        if child is not None:
            stop_owned(child)
        save(stage / "engine.json", result)
    print(json.dumps({"stage": action, **result}, ensure_ascii=False), flush=True)
    error = root / f"{action}-native-error.json"
    if error.exists():
        raise ValueError(json.loads(error.read_bytes())["error"])
    if result.get("exit_code") != 0 or terminated or not result.get("native_completed"):
        raise RuntimeError(f"Core stage {action} failed; inspect {stage / 'core.log'}")
    if action != "seed" and database_mode == "isolated":
        lifecycle = json.loads((root / f"{action}-lifecycle.json").read_bytes())
        if lifecycle != {
            "database_destroyed": True,
            "working_database_restored": True,
            "field_evaluation_restored": True,
        }:
            raise ValueError("Core did not confirm database cleanup")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--package-root", type=Path)
    parser.add_argument(
        "--fixture",
        choices=("complete", "missing", "unloaded", "instance-contract"),
        default="complete",
    )
    parser.add_argument("--production-library", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument(
        "--architecture", choices=SUPPORTED_MAC_ARCHITECTURES, default="x86_64"
    )
    parser.add_argument(
        "--database-mode", choices=("isolated", "active"), default="isolated"
    )
    parser.add_argument(
        "--split-jobs",
        action="store_true",
        help="Control: capture and archive in separate processes",
    )
    args = parser.parse_args()
    root = args.output.resolve()
    if not root.is_relative_to(REPO / "output") or "core-intake-" not in str(root):
        parser.error("Use a new output/core-intake-... directory in this checkout")
    if args.timeout < 1 or args.timeout > MAX_TIMEOUT_SECONDS:
        parser.error("Timeout must be between 1 and 600 seconds")
    library = args.production_library.resolve(strict=True)
    if not library.is_relative_to(REPO / ".runtime/autocad-bridge/macos"):
        parser.error("Use an explicit built production library from this checkout")
    root.mkdir(parents=True, exist_ok=False)
    print(root, flush=True)
    receipt: dict[str, Any] = {
        "schema": "green-atlas.core-intake-experiment/1",
        "experimental": True,
        "installed_product_changed": False,
        "database_mode": args.database_mode,
        "architecture": args.architecture,
        "split_jobs": args.split_jobs,
        "engines": {},
        "core_sha256": digest(CORE),
        "production_library_sha256": digest(library),
    }
    records: list[dict[str, Any]] = []
    try:
        if shutil.disk_usage(root).free < MIN_FREE_BYTES:
            raise ValueError("Less than 2 GiB free; no experiment")
        bundle = build(
            root,
            Path(__file__).with_name("core_intake_probe.cpp"),
            architecture=args.architecture,
        )
        receipt["probe_sha256"] = digest(bundle / "Contents/MacOS/CaptureSession")
        if args.source:
            package = (args.package_root or args.source.parent).resolve(strict=True)
            records = stage_package(args.source, package, root / "source")
            drawing = root / "source" / args.source.resolve().relative_to(package)
        else:
            receipt["engines"]["seed"] = run_stage(
                root,
                bundle,
                library,
                "seed",
                None,
                args.timeout,
                args.fixture,
                architecture=args.architecture,
            )
            if args.fixture == "missing":
                (root / "source/missing.dwg").rename(root / "missing-control.dwg")
            records = [
                {"source": str(p), "copy": str(p), "sha256": digest(p)}
                for p in sorted((root / "source").glob("*.dwg"))
            ]
            drawing = root / "source/host.dwg"
        receipt["files"] = records
        receipt["engines"]["capture"] = run_stage(
            root,
            bundle,
            library,
            "capture",
            drawing,
            args.timeout,
            args.fixture,
            args.database_mode,
            args.split_jobs,
            args.architecture,
        )
        if args.split_jobs:
            receipt["engines"]["archive"] = run_stage(
                root,
                bundle,
                library,
                "archive",
                drawing,
                args.timeout,
                args.fixture,
                args.database_mode,
                architecture=args.architecture,
            )
            before = json.loads((root / "before.json").read_bytes())
            archived = json.loads((root / "archive-opened.json").read_bytes())
            if geometry(before) != geometry(archived):
                raise ValueError(
                    "Saved-file archive opened a different source geometry"
                )
            receipt["saved_file_job_identity_verified"] = True
        # Make all staged source files unavailable during independent archive reopen.
        (root / "source").rename(root / "sealed-source")
        receipt["engines"]["inspect"] = run_stage(
            root,
            bundle,
            library,
            "inspect",
            root / "package/host.dwg",
            args.timeout,
            args.fixture,
            args.database_mode,
            architecture=args.architecture,
        )
        receipt["comparison"] = evaluate(root)
        inventory = json.loads(
            (root / "Drawing.autocad.json.inventory.json").read_bytes()
        )
        receipt["preflight"] = assess_input(
            json.loads((root / "before.json").read_bytes()), inventory
        )
        comparison = receipt["comparison"]
        if (
            not all(
                comparison[key]
                for key in (
                    "observed_source_geometry_unchanged",
                    "source_filename_unchanged",
                    "all_observed_instances_mapped_once",
                )
            )
            or comparison["differences"]
        ):
            raise ValueError("Archive reopen changed observed source geometry")
        receipt["state"] = receipt["preflight"]["state"]
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        receipt["state"] = "failed"
        receipt["error"] = str(error)
        before_file = root / "before.json"
        inventory_file = root / "Drawing.autocad.json.inventory.json"
        if before_file.is_file() and inventory_file.is_file():
            receipt["preflight"] = assess_input(
                json.loads(before_file.read_bytes()),
                json.loads(inventory_file.read_bytes()),
            )
            receipt["preflight"]["accepted_capture"] = False
    finally:
        if records:
            checks = []
            for row in records:
                copy = Path(row["copy"])
                if not copy.exists() and (root / "sealed-source").exists():
                    copy = root / "sealed-source" / copy.relative_to(root / "source")
                original = Path(row["source"])
                if not original.exists() and original.is_relative_to(root / "source"):
                    original = (
                        root / "sealed-source" / original.relative_to(root / "source")
                    )
                checks.append(
                    copy.is_file()
                    and original.is_file()
                    and digest(copy) == digest(original) == row["sha256"]
                )
            receipt["source_files_unchanged"] = all(checks)
            if not all(checks):
                receipt["state"] = "failed"
                receipt["error"] = "Source package changed during experiment"
        save(root / "receipt.json", receipt)
    print(
        json.dumps(
            {
                key: value
                for key, value in receipt.items()
                if key not in ("files", "engines", "comparison")
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    return int(receipt["state"] == "failed")


if __name__ == "__main__":
    raise SystemExit(main())
