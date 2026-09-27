"""Exercise a new isolated project from the real native capture through the service.

Only reads mappings/zones from the supplied reference DB. Never changes that DB
or its bound AutoCAD process. The caller supplies a separate test session.
"""

import argparse
import hashlib
import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

from app.composition import create_runtime
from app.desktop.handoff import LocalHandoff
from app.dxf_import.layer_contracts import Layer
from app.native_query.contracts import NativeObjectQuery
from app.native_query.derived_faces import face_targets
from app.native_query.live_client import LiveQueryClient
from app.planting_zones.contracts import PlantingZoneAssignment


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference-db", type=Path, required=True)
    parser.add_argument("--reference-project", required=True)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=args.resume)
    client = LiveQueryClient(pid=args.pid, timeout_seconds=180)
    session = client.reconnect(args.session)
    report = {"session": session.model_dump(), "checks": {}, "seconds": {}}
    before = hashlib.sha256(Path(session.source_path).read_bytes()).hexdigest()
    capture = Path(session.snapshot_path)
    os.environ["GREEN_ATLAS_CAD_INTAKE_PATH"] = str(root / "intake")
    os.environ["GREEN_ATLAS_CAD_ROOTS_JSON"] = "{}"
    runtime = create_runtime(root / "projects.sqlite3")
    transfers = root / "transfers"
    package = transfers / "test"
    package.mkdir(parents=True, exist_ok=args.resume)
    if not args.resume:
        os.link(capture, package / "Drawing.autocad.json")
    ticket = package / "transfer.gatransfer"
    ticket.write_text(
        json.dumps(
            {
                "schema": "green-atlas.transfer/4",
                "plugin_version": session.plugin_version,
                "producer": {"autocad_version": "2027.0.1", "target": "macos-x86_64"},
                "source_name": "Кустанайская — проверка областей.dxf",
                "live_session": session.model_dump(),
                "manifest": {
                    "entry": "Drawing.autocad.json",
                    "files": [
                        {
                            "name": "Drawing.autocad.json",
                            "kind": "live_capture",
                            "bytes": capture.stat().st_size,
                            "sha256": session.snapshot_sha256,
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
    )
    handoff = LocalHandoff(runtime, transfers)
    try:
        start = time.monotonic()
        receipt = handoff.submit(ticket)
        if receipt["id"] in handoff.futures:
            handoff.futures[receipt["id"]].result(timeout=600)
        receipt = handoff.get(receipt["id"])
        report["handoff"] = receipt
        assert receipt["status"] == "needs_review", receipt
        project = runtime.application.get(runtime.application.list_projects()[0].id)
        report["project_id"] = project.id
        report["seconds"]["import"] = time.monotonic() - start
        assert project.source_file.native_session == session
        source_features = (
            project.source_geometry or project.geometry
        ).feature_collection["features"]
        assert source_features, "Live handoff lost the source map"
        source_shapes = Counter(
            json.dumps(f["geometry"], sort_keys=True) for f in source_features
        )
        report["source_feature_count"] = len(source_features)
        # Reuse the user's chosen layer semantics only in this disposable project.
        with sqlite3.connect(
            f"file:{args.reference_db.resolve()}?mode=ro", uri=True
        ) as db:
            reference = json.loads(
                db.execute(
                    "select payload from projects where id=?", (args.reference_project,)
                ).fetchone()[0]
            )
        mappings = {
            row["source_name"]: Layer.model_validate(row) for row in reference["layers"]
        }
        for layer in project.layers:
            source = mappings.get(layer.source_name)
            if source:
                for field in (
                    "mapped_kind",
                    "mapping_confirmed",
                    "category",
                    "utility_context",
                ):
                    setattr(layer, field, getattr(source, field))
        project.source_file.accept_partial_geometry = (
            True  # Explicit test fixture policy
        )
        project.planting_zones = [
            PlantingZoneAssignment.model_validate(row)
            for row in reference["planting_zones"]
        ]
        runtime.project_repository.save(project)
        start = time.monotonic()
        operation = runtime.application.start_geometry_operation(project.id)
        runtime.application.run_geometry_operation(operation.id)
        operation = runtime.application.get_operation(project.id, operation.id)
        report["operation"] = operation.model_dump(mode="json")
        assert operation.status == "completed", str(operation.error)[:600]
        report["seconds"]["prepare"] = time.monotonic() - start
        project = runtime.application.get(project.id)
        engine = runtime.application.geometry.engine(project)
        engine.assert_current(project)
        capture_engine = runtime.application.geometry.live_engine(project)
        report["faces_by_layer"] = dict(Counter(f.layer for f in engine._faces))
        report["linear_remainders"] = len(capture_engine.inventory.linear_routes)
        report["face_issues"] = list(capture_engine.inventory.face_issues)
        report["final_check"] = getattr(engine, "final_check", "autocad")
        features = project.geometry.feature_collection["features"]
        retained_shapes = Counter(
            json.dumps(f["geometry"], sort_keys=True)
            for f in features
            if not f.get("properties", {}).get("source_native_face_id")
        )
        assert not (source_shapes - retained_shapes), "Calculation lost source geometry"
        report["checks"]["source_map_preserved"] = True
        displayed = {
            f["properties"]["source_native_face_id"]
            for f in features
            if f.get("properties", {}).get("source_native_face_id")
        }
        assert displayed == {f.id for f in engine._faces}
        report["checks"]["map_query_same_faces"] = True
        buildings = [face for face in engine._faces if face.layer.endswith("|Здания")]
        points = (
            (15986.0, -4975.0, 0.0),
            (15994.606306, -5245.249309, 0.0),
            (16087.0, -5231.0, 0.0),
            (15610.0, -5000.0, 0.0),
        )
        query = NativeObjectQuery(
            request_id=uuid4().hex,
            source_sha256=session.source_sha256,
            units_code=session.units_code,
            points=points,
            targets=tuple(row.target(area=True) for row in face_targets(buildings)),
        )
        start = time.monotonic()
        reply = client.measure(session, query)
        memberships = [
            [row.answers[i].membership for row in reply.objects] for i in range(4)
        ]
        assert all("occupied" in row for row in memberships[:3])
        assert all(value == "outside" for value in memberships[3])
        assert all(
            not row.preparation_error
            and all(not a.error and not a.status for a in row.answers)
            for row in reply.objects
        )
        report["seconds"]["building_queries"] = time.monotonic() - start
        report["checks"]["building_inside_outside"] = True
        report["read_issues"] = runtime.application.source_read_issues(
            project.id
        ).model_dump(mode="json")
        assert (
            before == hashlib.sha256(Path(session.source_path).read_bytes()).hexdigest()
        )
        report["checks"]["source_file_unchanged"] = True
        client.inspect(session)
        report["checks"]["live_revision_unchanged"] = True
        print(
            json.dumps(
                {
                    k: v
                    for k, v in report.items()
                    if k not in {"face_issues", "read_issues", "session"}
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    finally:
        (root / "receipt.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        handoff.close()
        runtime.close()


if __name__ == "__main__":
    main()
