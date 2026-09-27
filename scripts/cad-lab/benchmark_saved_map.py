"""Read-only replay of the production viewport on a saved project.

Never constructs a writable repository, calculates constraints or edits a plan.
Only timing/coverage metadata is written; source and project bytes stay in SQLite.
"""
import argparse
import json
import resource
import sqlite3
import time
from pathlib import Path

from app.geometry.query_adapters import IndexedGeometryQuery
from app.projects.contracts import Project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--save-overview", type=Path)
    args = parser.parse_args()
    receipt = {"project_id": args.project, "database": str(args.db), "read_only": True}
    start = time.perf_counter()
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        payload, version = db.execute(
            "SELECT CAST(payload AS BLOB), state_version FROM projects WHERE id=?",
            (args.project,),
        ).fetchone()
    receipt["read_seconds"] = time.perf_counter() - start
    receipt["payload_bytes"] = len(payload)
    start = time.perf_counter()
    project = Project.model_validate(json.loads(payload))
    del payload
    receipt["decode_seconds"] = time.perf_counter() - start
    receipt["state_version"] = version
    receipt["geometry_version"] = project.geometry_version
    receipt["source_sha256"] = project.source_file.content_sha256 if project.source_file else None
    query = IndexedGeometryQuery()
    start = time.perf_counter()
    indexed = query._index(project)
    receipt["index_seconds"] = time.perf_counter() - start
    receipt["indexed_features"] = len(indexed.features)
    from shapely import total_bounds
    extent = tuple(float(v) for v in total_bounds(indexed.geometries))
    receipt["extent"] = extent
    width, height = extent[2] - extent[0], extent[3] - extent[1]
    resolution = max(width / 1200, height / 900)
    receipt["queries"] = []
    boundary = next((layer.bounds for layer in project.layers
                     if layer.mapped_kind == "site_border" and layer.bounds), extent)
    bx, by = (boundary[0] + boundary[2]) / 2, (boundary[1] + boundary[3]) / 2
    for name, box, res in [
        ("full-cold", extent, resolution),
        ("full-warm", extent, resolution),
        ("street-detail", (bx - 100, by - 75, bx + 100, by + 75), 0.2),
        ("street-pan", (bx - 90, by - 75, bx + 110, by + 75), 0.2),
    ]:
        start = time.perf_counter()
        result = query.query_cached(project, box, res)
        elapsed = time.perf_counter() - start
        collection = result.feature_collection
        overview = collection.get("source_overview")
        if name == "full-cold" and args.save_overview and overview and overview.get("svg"):
            args.save_overview.parent.mkdir(parents=True, exist_ok=True)
            args.save_overview.write_text(overview["svg"])
        item = {"name": name, "extent": box, "resolution": res, "seconds": elapsed,
                "metadata": collection.get("metadata"),
                "overview": {k: v for k, v in overview.items() if k != "svg"} if overview else None}
        start = time.perf_counter()
        encoded = result.model_dump_json()
        item["encode_seconds"] = time.perf_counter() - start
        item["response_bytes"] = len(encoded.encode())
        del encoded, result
        receipt["queries"].append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    receipt["max_rss_bytes_macos"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    with sqlite3.connect(args.db.resolve().as_uri() + "?mode=ro", uri=True) as db:
        after = db.execute("SELECT state_version FROM projects WHERE id=?", (args.project,)).fetchone()[0]
    receipt["state_version_after"] = after
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    print(str(args.output), flush=True)


if __name__ == "__main__":
    main()
