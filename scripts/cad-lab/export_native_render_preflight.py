"""Export a read-only, deliberately non-renderable native capture/plan preflight.

This does not parse CAD or infer surfaces, terrain, buildings, or georeference.
The output proves what one persisted project revision contains before a future
same-capture render-facts publisher is connected.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path


SCHEMA = "green-atlas.native-render-preflight.v1"


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def export(database: Path, project_id: str, output: Path) -> dict:
    if output.exists():
        raise FileExistsError(f"Refusing to replace an existing package: {output}")
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        connection.execute("BEGIN")
        row = connection.execute(
            "SELECT rowid, length(source), state_version, "
            "json_extract(payload,'$.geometry_version'), "
            "json_extract(payload,'$.source_file.content_sha256'), "
            "json_extract(payload,'$.source_file.size'), "
            "json_extract(payload,'$.coordinate_reference.status'), "
            "json_extract(payload,'$.plan'), "
            "json_extract(payload,'$.layers'), "
            "json_extract(source,'$.schema'), "
            "json_extract(source,'$.complete'), "
            "json_extract(source,'$.source.sha256'), "
            "json_extract(source,'$.summary'), "
            "json_extract(source,'$.xref_dependencies'), "
            "json_type(source,'$.elevation_controls'), "
            "json_type(source,'$.annotations') "
            "FROM projects WHERE id=?", (project_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Project not found: {project_id}")
        (rowid, source_bytes, state_version, geometry_version, expected_sha,
         expected_bytes, crs_status, plan_json, layers_json, capture_schema, capture_complete,
         document_sha, summary_json, xrefs_json, elevation_type,
         annotation_type) = row
        if source_bytes is None or not expected_sha or expected_bytes != source_bytes:
            raise ValueError("Missing or inconsistent saved capture identity")
        digest = hashlib.sha256()
        with connection.blobopen("projects", "source", rowid, readonly=True) as blob:
            while chunk := blob.read(1024 * 1024):
                digest.update(chunk)
        capture_sha = digest.hexdigest()
        if capture_sha != expected_sha:
            raise ValueError("Saved capture bytes do not match project source SHA")
        if not plan_json:
            raise ValueError("Project has no saved planting plan")
        plan = json.loads(plan_json)
        layers = json.loads(layers_json) if layers_json else []
        if not isinstance(layers, list):
            raise ValueError("Saved layer decisions are malformed")
        objects = plan.get("objects")
        if not isinstance(objects, list):
            raise ValueError("Saved plan has no object list")
        ids = [obj.get("id") for obj in objects]
        if any(not item for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("Planting objects need distinct stable IDs")
        basis = plan.get("validation_basis") or {}
        if (basis.get("plan_version") != plan.get("version")
                or basis.get("geometry_version") != geometry_version):
            raise ValueError("Plan validation basis differs from saved revisions")
        xrefs = json.loads(xrefs_json) if xrefs_json else []
        summary = json.loads(summary_json) if summary_json else {}
        plan_bytes = encoded(plan)
        layers_bytes = encoded(layers)
        blockers = ["native_render_facts_not_published",
                    "render_surface_semantics_not_published",
                    "capture_binding_of_validation_unproven"]
        if elevation_type is None:
            blockers.append("typed_ground_elevation_absent")
        if annotation_type is None:
            blockers.append("native_annotation_values_absent")
        if crs_status not in ("declared", "verified"):
            blockers.append("georeference_unverified")
        if not capture_complete:
            blockers.append("capture_incomplete")
        if any(xref.get("status") != "resolved" for xref in xrefs):
            blockers.append("xref_unresolved")
        manifest = {
            "schema": SCHEMA,
            "status": "preflight_only",
            "blender_ready": False,
            "project": {"id": project_id, "state_version": state_version,
                        "geometry_version": geometry_version},
            "capture": {"schema": capture_schema, "snapshot_sha256": capture_sha,
                        "snapshot_bytes": source_bytes, "document_sha256": document_sha,
                        "complete": bool(capture_complete),
                        "summary": {key: summary.get(key) for key in
                                    ("regions", "paths", "points", "source_instances")},
                        "xref_unresolved": sum(xref.get("status") != "resolved" for xref in xrefs)},
            "plan": {"path": "plan.json", "sha256": hashlib.sha256(plan_bytes).hexdigest(),
                     "bytes": len(plan_bytes), "id": plan.get("id"),
                     "version": plan.get("version"), "object_count": len(objects),
                     "validation_basis": basis},
            "layers": {"path": "layers.json", "sha256": hashlib.sha256(layers_bytes).hexdigest(),
                       "bytes": len(layers_bytes), "count": len(layers),
                       "mapping_confirmed": sum(layer.get("mapping_confirmed") is True for layer in layers)},
            "evidence": {"coordinate_reference_status": crs_status or "unknown",
                         "typed_elevation_controls": elevation_type is not None,
                         "native_annotation_values": annotation_type is not None},
            "blockers": blockers,
        }
    finally:
        connection.close()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".render-preflight-", dir=output.parent) as temporary:
        staged = Path(temporary)
        (staged / "plan.json").write_bytes(plan_bytes)
        (staged / "layers.json").write_bytes(layers_bytes)
        (staged / "manifest.json").write_bytes(encoded(manifest))
        os.rename(staged, output)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = export(args.database, args.project_id, args.output)
    print(json.dumps({"output": str(args.output), "status": result["status"],
                      "object_count": result["plan"]["object_count"],
                      "blockers": result["blockers"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
