"""Memory/time-supervised publication of one live AutoCAD capture.

This is the same native snapshot compiler and provider as the small in-process
route, not a DXF reader or alternate geometry algorithm. The child owns its
temporary source graph and commits only after full validation succeeds.
"""

from __future__ import annotations

import json
import sys
from hashlib import sha256
from pathlib import Path

from app.composition import create_runtime
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.live_contracts import LiveImportTask
from app.projects.concurrency import (
    reset_expected_project_version,
    set_expected_project_version,
)


def execute(task: LiveImportTask) -> None:
    receipt = Path(task.receipt_path)
    runtime = None
    token = set_expected_project_version(str(task.expected_state_version))
    try:
        source = Path(task.source_path).read_bytes()
        if sha256(source).hexdigest() != task.source_sha256:
            raise ValueError("Снимок AutoCAD изменился до начала обработки")
        runtime = create_runtime(task.database_path)
        project = runtime.application._imports.import_autocad_live(
            task.project_id,
            task.filename,
            source,
            autocad_version=task.autocad_version,
            target=task.target,
            capacity=SourceGeometryCapacity.process_bounded(),
        )
        receipt.write_text(json.dumps({
            "ok": True,
            "project_id": project.id,
            "state_version": project.state_version,
            "source_sha256": task.source_sha256,
            "features": len(project.geometry.feature_collection["features"])
            if project.geometry else 0,
        }), encoding="utf-8")
    except Exception as error:
        receipt.write_text(json.dumps({
            "ok": False,
            "error": str(error),
        }, ensure_ascii=False), encoding="utf-8")
    finally:
        reset_expected_project_version(token)
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    execute(LiveImportTask.model_validate_json(
        Path(sys.argv[1]).read_text(encoding="utf-8")
    ))
