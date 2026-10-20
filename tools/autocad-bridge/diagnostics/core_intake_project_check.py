"""Exercise a Core capture through the real backend in a private QA profile.

The legacy native ticket envelope is a test adapter, not a product Core API.
Never publish experimental source provenance into a user's project library.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.composition import create_runtime
from app.desktop.handoff import LocalHandoff
from app.dxf_import.layers.contracts import LayerKind, LayerMapping
from app.exporting.contracts import ReleaseCreateRequest
from app.native_query.project_runtime import ProjectLiveGeometry
from app.planning.contracts import PlanObjectCreate
from app.planting_zones.contracts import PlantingZoneAssignment
from capture_session_run import REPO
from core_intake_run import save

HANDOFF_DEADLINE_SECONDS = 90
HANDOFF_POLL_SECONDS = 0.05
DEAD_EDITOR_PID = 99999999


def stage_ticket(capture: Path, root: Path) -> Path:
    receipt = json.loads((capture / "receipt.json").read_bytes())
    if receipt["state"] != "captured" or not receipt["source_files_unchanged"]:
        raise ValueError("Only a verified complete synthetic Core capture can enter QA")
    transfer = root / "transfers/capture"
    transfer.mkdir(parents=True)
    shutil.copytree(capture / "package", transfer / "Native")
    for name in ("Drawing.autocad.json", "Drawing.autocad.json.inventory.json"):
        shutil.copyfile(capture / name, transfer / name)

    def entry(path: Path) -> dict[str, Any]:
        data = path.read_bytes()
        return {
            "name": path.relative_to(transfer).as_posix(),
            "bytes": len(data),
            "sha256": sha256(data).hexdigest(),
        }

    probe = json.loads((transfer / "Drawing.autocad.json").read_bytes())
    archive = json.loads((transfer / "Native/session.json").read_bytes())
    display = entry(transfer / "Drawing.autocad.json")
    inventory = entry(transfer / "Drawing.autocad.json.inventory.json")
    session = {
        "session_id": uuid.uuid4().hex,
        "pid": DEAD_EDITOR_PID,
        "plugin_version": archive["plugin_version"],
        "source_path": probe["source"]["path"],
        "source_sha256": probe["source"]["sha256"],
        "snapshot_path": "/expired/core-experiment.json",
        "snapshot_sha256": display["sha256"],
        "inventory_path": "/expired/core-experiment.json.inventory.json",
        "inventory_sha256": inventory["sha256"],
        "units_code": probe["source"]["units_code"],
        "watched_databases": len(archive["files"]),
        "unavailable_xrefs": sum(not row["available"] for row in archive["references"]),
    }
    payload = {
        "schema": "green-atlas.transfer/5",
        "plugin_version": archive["plugin_version"],
        "source_name": "host.dwg",
        "producer": {"autocad_version": "2027.0.1", "target": "macos-x86_64"},
        "manifest": {
            "entry": display["name"],
            "files": [display | {"kind": "live_capture"}],
        },
        "live_session": session,
        "native_session": {
            "entry": "Native/session.json",
            "files": [
                *(entry(path) for path in sorted((transfer / "Native").iterdir())),
                display,
                inventory,
            ],
        },
    }
    ticket = transfer / "transfer.gatransfer"
    save(ticket, payload)
    return ticket


def exercise(capture: Path, root: Path, worker: Path) -> dict[str, Any]:
    ticket = stage_ticket(capture, root)
    os.environ["GREEN_ATLAS_CAD_ROOTS_JSON"] = "{}"
    os.environ["GREEN_ATLAS_CAD_INTAKE_PATH"] = str(root / "intake")
    os.environ["GREEN_ATLAS_CAD_RELEASE_WORKER"] = str(worker)
    database = root / "workspace.sqlite3"
    runtime = create_runtime(database)
    handoff = LocalHandoff(runtime, root / "transfers")
    result: dict[str, Any] = {
        "experimental": True,
        "ticket_envelope": "QA adapter only",
    }
    try:
        with patch(
            "app.desktop.handoff.LiveQueryClient",
            side_effect=AssertionError("Editor access forbidden"),
        ):
            submitted = handoff.submit(ticket)
            deadline = time.monotonic() + HANDOFF_DEADLINE_SECONDS
            while handoff.get(submitted["id"])["status"] in (
                "queued",
                "running",
                "processing",
            ):
                if time.monotonic() > deadline:
                    raise RuntimeError("QA handoff timed out")
                time.sleep(HANDOFF_POLL_SECONDS)
            done = handoff.get(submitted["id"])
            if done["status"] != "needs_review":
                raise ValueError(f"QA handoff failed: {done}")
        project_id = handoff.journal.get(submitted["id"]).project_id
        if project_id is None:
            raise ValueError("QA handoff has no project")
        result["project_id"] = project_id
        app = runtime.application
        project = runtime.project_repository.get(project_id)
        if {layer.source_name for layer in project.layers} != {"SITE", "BUILDING", "0"}:
            raise ValueError("Project check requires the complete synthetic fixture")
        shutil.rmtree(ticket.parent)
        roles = {"SITE": LayerKind.SITE_BORDER, "BUILDING": LayerKind.BUILDING}
        app.save_mappings(
            project_id,
            [
                LayerMapping(
                    layer_id=layer.id,
                    kind=roles.get(layer.source_name, LayerKind.IGNORE),
                    confirmed=True,
                )
                for layer in project.layers
            ],
        )
        operation = app.start_geometry_operation(project_id)
        app.run_geometry_operation(operation.id)
        prepared = app.get_operation(project_id, operation.id)
        if prepared.status != "completed":
            raise ValueError(f"Core preparation failed: {prepared.error}")
        result["preparation"] = prepared.model_dump(mode="json")
        app.save_planting_zones(
            project_id,
            [
                PlantingZoneAssignment(
                    label="Synthetic QA zone",
                    geometry={
                        "type": "Polygon",
                        "coordinates": [
                            [[4, 4], [9, 4], [9, 9], [4, 9], [4, 4]],
                        ],
                    },
                )
            ],
        )
        app.create_manual_plan(project_id)
        with (
            patch(
                "app.native_query.archive.archive_client.run_native_query",
                side_effect=AssertionError("Planting must remain local"),
            ),
            patch(
                "app.native_query.archive.archive_client.prepare_archive_faces",
                side_effect=AssertionError("Planting must remain local"),
            ),
        ):
            plan = app.add_object(
                project_id, PlanObjectCreate(kind="shrub", x=6, y=6, radius=0.5)
            )
        result["plan"] = plan.model_dump(mode="json")
        stored_plan = runtime.project_repository.get(project_id).plan
        if stored_plan is None:
            raise ValueError("Saved project has no plan")
        result["saved_plan_sha256"] = sha256(
            stored_plan.model_dump_json().encode()
        ).hexdigest()
        result["release"] = app.create_release(
            project_id, ReleaseCreateRequest(mode="draft")
        ).model_dump(mode="json")
        result["history"] = app.get_plan_history(project_id).model_dump(mode="json")
    finally:
        handoff.close()
        runtime.close()
    with patch(
        "app.native_query.live.live_client.LiveQueryClient",
        side_effect=AssertionError("Reopen accessed editor"),
    ):
        resumed = create_runtime(database)
        try:
            project = resumed.application.get(result["project_id"])
            if project.plan is None:
                raise ValueError("Reopened project has no plan")
            if (
                sha256(project.plan.model_dump_json().encode()).hexdigest()
                != result["saved_plan_sha256"]
            ):
                raise ValueError("Cold reopen changed saved plan")
            geometry = resumed.application.geometry
            if not isinstance(geometry, ProjectLiveGeometry):
                raise TypeError("QA runtime has no native geometry owner")
            geometry.load_local(project)
            result["cold_reopen_verified"] = True
        finally:
            resumed.close()
    return result


def main() -> int:
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    if not root.is_relative_to(REPO / "output") or "core-intake-" not in str(root):
        parser.error("Use a new private output/core-intake-... directory")
    root.mkdir(parents=True, exist_ok=False)
    try:
        result = exercise(
            args.capture.resolve(strict=True), root, args.worker.resolve(strict=True)
        )
        result["state"] = "passed"
    except (OSError, ValueError, TypeError, RuntimeError, AssertionError) as error:
        result = {"state": "failed", "error": str(error)}
    save(root / "receipt.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(result["state"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
