"""Exercise real intake and full editor preparation in a new isolated SQLite DB.

Run under app.cad_import.process.run_converter for an outer process-tree budget.
The source must already be a self-contained prepared DXF. Nothing is rewritten.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from app.cad_import.cache import file_sha256
from app.cad_intake.contracts import CadIntakeRequest
from app.cad_intake.prepare_contracts import CadPrepareRequest
from app.composition import create_runtime
from app.geometry.geojson_size import coordinate_count
from app.operations.contracts import OperationStatus
from app.projects.contracts import Project


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source, output = args.source.resolve(strict=True), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    database = output / "project.sqlite3"
    if database.exists():
        raise FileExistsError("Use a new output directory; existing projects are never replaced")
    os.environ["GREEN_ATLAS_CAD_ROOTS_JSON"] = json.dumps({"prepared": {"path": str(source.parent)}})
    os.environ["GREEN_ATLAS_CAD_INTAKE_PATH"] = str(output / "cad-intake")
    runtime = create_runtime(database)
    started = time.monotonic()
    result = {"source": str(source), "source_sha256": file_sha256(source), "source_bytes": source.stat().st_size}
    def record():
        result["elapsed_seconds"] = time.monotonic() - started
        (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    try:
        project = runtime.project_repository.create(Project(name="Полный DXF — проверка редактора"))
        result["project_id"] = project.id
        intake = runtime.cad_intake.start(project.id, CadIntakeRequest(root_id="prepared", entry=source.name, entry_sha256=result["source_sha256"]))
        runtime.cad_intake.run(intake.id)
        intake = runtime.operation_repository.get(intake.id)
        result["intake"] = intake.model_dump(mode="json")
        record()
        if intake.status != OperationStatus.COMPLETED:
            return
        operation = runtime.cad_prepare.start(project.id, CadPrepareRequest(intake_operation_id=intake.id, manifest_sha256=intake.cad_intake.passport.manifest_sha256))
        runtime.cad_prepare.run(operation.id)
        operation = runtime.operation_repository.get(operation.id)
        result["preparation"] = operation.model_dump(mode="json")
        record()
        if operation.status != OperationStatus.COMPLETED:
            return
        load_started = time.monotonic()
        project = runtime.project_repository.get(project.id)
        result["repository_read_seconds"] = time.monotonic() - load_started
        features = project.geometry.feature_collection["features"]
        result["published"] = {
            "features": len(features), "coordinates": sum(coordinate_count(f["geometry"]) for f in features),
            "editable": project.import_status.editability.value, "map_ready": project.map_ready,
            "source_review": project.source_review.model_dump(mode="json"),
            "original_bytes_preserved": hashlib.sha256(runtime.project_repository.get_source(project.id)).hexdigest() == result["source_sha256"],
        }
        record()
    finally:
        runtime.close()
    print(json.dumps({k:v for k,v in result.items() if k not in {"intake", "published"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
