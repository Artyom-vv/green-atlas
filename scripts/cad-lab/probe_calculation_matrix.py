"""Read-only cross-file calculation probe, with no normative acceptance claim."""
import argparse
import gc
import hashlib
import json
from pathlib import Path
import time

from app.dxf_import.adapters import EzdxfReader
from app.geometry.adapters import ShapelyGeometryEngine
from app.projects.contracts import Project


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect(source):
    started = time.monotonic()
    before = digest(source)
    row = {"source": str(source), "source_sha256": before}
    try:
        imported = EzdxfReader().read_prepared_file(source)
        row.update(features=len(imported.geometry.feature_collection["features"]),
                   incomplete_layers=[layer.source_name for layer in imported.layers if not layer.geometry_complete],
                   warnings=imported.warnings)
        project = Project(name="Read-only matrix", layers=imported.layers,
                          source_geometry=imported.geometry, coordinate_reference=imported.coordinate_reference)
        for layer in project.layers:
            layer.mapped_kind = layer.suggested_kind
        calculation_start = time.monotonic()
        result = ShapelyGeometryEngine().calculate(project)
        row.update(status="calculated_with_unreviewed_suggestions",
                   calculation_seconds=time.monotonic()-calculation_start,
                   site_area_m2=result.site_area_m2, allowed_area_m2=result.allowed_area_m2)
    except Exception as error:
        row.update(status="rejected", error_type=type(error).__name__, reason=str(error))
    row.update(seconds=time.monotonic()-started, source_unchanged=before == digest(source))
    return row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("sources", nargs="+", type=Path)
    args = parser.parse_args()
    # New receipt only, flushed per input. Interrupted runs retain their evidence.
    with args.output.open("x", encoding="utf8") as stream:
        for source in args.sources:
            row = inspect(source)
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            stream.flush()
            print(json.dumps({k: v for k, v in row.items() if k != "warnings"}, ensure_ascii=False), flush=True)
            gc.collect()
