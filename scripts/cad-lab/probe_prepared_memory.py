"""Measure DXF reader/SDK lifetime/serialization without publishing a project.

Run with run_bounded.py. --collect measures reclaiming unreachable SDK objects
at the worker phase boundary; it never removes retained normalized geometry.
"""

import argparse
import gc
from hashlib import sha256
import json
from pathlib import Path
import sys
import time

import psutil

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.assembly import assemble_imported_project
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.editor_source import open_source_editor
from app.projects.contracts import Project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("report", type=Path)
    parser.add_argument("--collect", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    process = psutil.Process()
    report = {"source": str(args.source), "collect": args.collect, "stages": []}

    def record(stage, **extra):
        report["stages"].append({"stage": stage, "elapsed_seconds": time.monotonic() - started,
            "rss_bytes": process.memory_info().rss, **extra})
        args.report.write_text(json.dumps(report, indent=2), encoding="utf8")

    content = args.source.read_bytes()
    record("source_loaded", source_sha256=sha256(content).hexdigest())
    imported = EzdxfReader(capacity=SourceGeometryCapacity.process_bounded()).read(args.source.name, content)
    record("reader_completed", features=len(imported.geometry.feature_collection["features"]))
    if args.collect:
        collected = gc.collect()
        record("unreachable_sdk_collected", collected=collected)
    project = assemble_imported_project(Project(name="Memory probe"), args.source.name,
        content, imported, "2026-09-16T00:00:00+00:00")
    del imported
    project = open_source_editor(project)
    record("source_editor_composed")
    payload = project.model_dump_json()
    record("payload_serialized", payload_characters=len(payload))


if __name__ == "__main__":
    main()
