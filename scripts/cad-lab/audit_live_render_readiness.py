"""Read-only inventory of actual Green Atlas native capture inputs for rendering.

Reads the persisted AutoCAD snapshot; never opens or parses DWG/DXF. The report
separates native geometry, semantic/height evidence and external context.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(database: Path, project_id: str, inventory: Path | None,
          map_context: Path | None) -> dict:
    connection = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = connection.execute(
            "SELECT rowid, length(source), state_version, "
            "json_extract(payload,'$.geometry_version'), "
            "json_extract(payload,'$.source_file.content_sha256'), "
            "json_extract(payload,'$.source_file.size'), "
            "json_extract(payload,'$.coordinate_reference.status'), "
            "json_extract(payload,'$.source_review.status'), "
            "json_extract(payload,'$.plan') "
            "FROM projects WHERE id=?", (project_id,),
        ).fetchone()
        if row is None:
            raise ValueError(f"Project not found: {project_id}")
        rowid, source_size, state_version, geometry_version, expected_sha, expected_size, crs_status, review_status, plan_json = row
        if source_size is None:
            raise ValueError("Project has no saved source snapshot")
        digest = hashlib.sha256()
        blob = connection.blobopen("projects", "source", rowid, readonly=True)
        try:
            while chunk := blob.read(1024 * 1024):
                digest.update(chunk)
        finally:
            blob.close()
        source_sha = digest.hexdigest()
        raw = connection.execute(
            "SELECT json_extract(source,'$.schema'), "
            "json_extract(source,'$.capture_mode'), "
            "json_extract(source,'$.complete'), "
            "json_extract(source,'$.source'), "
            "json_extract(source,'$.xref_dependencies'), "
            "json_extract(source,'$.summary'), "
            "json_type(source,'$.elevation_controls'), "
            "json_type(source,'$.annotations'), "
            "json_type(source,'$.georeference') "
            "FROM projects WHERE rowid=?", (rowid,),
        ).fetchone()
    finally:
        connection.close()
    schema, capture_mode, snapshot_complete, source_json, xrefs_json, summary_json, elevations_type, annotations_type, georef_type = raw
    source = json.loads(source_json) if source_json else {}
    xrefs = json.loads(xrefs_json) if xrefs_json else []
    summary = json.loads(summary_json) if summary_json else {}
    plan = json.loads(plan_json) if plan_json else None
    objects = plan.get("objects", []) if isinstance(plan, dict) else []
    basis = plan.get("validation_basis") if isinstance(plan, dict) else None
    if source_sha != expected_sha or source_size != expected_size:
        raise ValueError("Stored native snapshot differs from project source identity")
    external = {}
    for name, path in (("inventory", inventory), ("map_context", map_context)):
        if path is None:
            external[name] = {"status": "not_supplied_to_audit"}
        elif not path.is_file():
            external[name] = {"status": "missing_file", "path": str(path)}
        else:
            external[name] = {"status": "available_not_bound_to_capture",
                              "path": str(path.resolve()), "sha256": sha256_file(path)}
    resolved = [row for row in xrefs if row.get("status") == "resolved"]
    unresolved = [row for row in xrefs if row.get("status") != "resolved"]
    return {
        "schema": "green-atlas.native-render-input-audit.v1",
        "project_id": project_id, "project_state_version": state_version,
        "project_geometry_version": geometry_version,
        "plan": {
            "status": "present" if plan else "absent",
            "id": plan.get("id") if plan else None,
            "version": plan.get("version") if plan else None,
            "object_count": len(objects),
            "kinds": dict(sorted(Counter(obj.get("kind", "unknown") for obj in objects).items())),
            "statuses": dict(sorted(Counter(obj.get("status", "unknown") for obj in objects).items())),
            "validation_basis": basis,
            "basis_matches_saved_versions": bool(
                basis and basis.get("plan_version") == plan.get("version")
                and basis.get("geometry_version") == geometry_version
            ),
        },
        "native_capture": {
            "schema": schema, "mode": capture_mode, "snapshot_complete": bool(snapshot_complete),
            "snapshot_sha256": source_sha, "snapshot_bytes": source_size,
            "source_document_sha256": source.get("sha256"),
            "document_revision": source.get("document_revision"),
            "metres_per_unit": source.get("metres_per_unit"),
            "xref_resolved": len(resolved),
            "xref_unresolved": [{"name": row.get("block_name"), "status": row.get("status")}
                                for row in unresolved],
            "summary": {key: summary.get(key) for key in
                        ("regions", "paths", "points", "source_instances", "unresolved_instances")},
        },
        "readiness": {
            "native_display_geometry": "available_with_local_gaps" if unresolved else "available",
            "surface_semantics": "requires_confirmed_layer_decisions" if review_status != "confirmed" else "review_confirmed_needs_render_export",
            "ground_elevation": "not_published_as_typed_controls" if elevations_type is None else "typed_controls_present_needs_validation",
            "annotation_values": "not_published_in_snapshot" if annotations_type is None else "present_needs_validation",
            "georeference": crs_status or "unknown",
            "native_georeference_payload": "absent" if georef_type is None else "present_needs_validation",
            "project_plan": "empty" if not plan else "present_needs_capture_binding_check",
            "source_review": review_status,
        },
        "external": external,
        "interpretation": "No render package admitted: external files are not bound to this capture; XYZ alone is not typed ground or a geodetic transform.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--inventory", type=Path)
    parser.add_argument("--map-context", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.database, args.project_id, args.inventory, args.map_context)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "readiness": result["readiness"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
