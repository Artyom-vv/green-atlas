"""Изолированный native-импорт использует общий компилятор снимка и публикует проект
только после полного допуска.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from app.composition import create_runtime
from app.dxf_import.capacity import SourceGeometryCapacity
from app.dxf_import.native.live_contracts import (
    LiveImportConflict,
    LiveImportProjectMissing,
    LiveImportRefusal,
    LiveImportTask,
)
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.shared.failures import DomainFailure, ProjectNotFound


def _failure_receipt(
    error: Exception,
) -> LiveImportRefusal | LiveImportProjectMissing | LiveImportConflict | None:
    if isinstance(error, DomainFailure):
        return LiveImportRefusal(reason=error.reason, params=error.params)
    if isinstance(error, ProjectNotFound):
        return LiveImportProjectMissing(project_id=error.project_id)
    if isinstance(error, ProjectVersionConflict):
        return LiveImportConflict(
            project_id=error.project_id,
            expected_version=error.expected_version,
            current_version=error.current_version,
        )
    return None


def execute(task: LiveImportTask) -> None:
    receipt = Path(task.receipt_path)
    runtime = None
    token = set_expected_project_version(str(task.expected_state_version))
    try:
        runtime = create_runtime(task.database_path, recover_operations=False)
        native_root = (
            Path(task.native_capture_root) if task.native_capture_root else None
        )
        if (
            native_root is not None
            and native_root.resolve()
            != (
                Path(str(Path(task.database_path).resolve()) + ".cad-archives")
                / task.project_id
                / task.source_sha256
            ).resolve()
        ):
            raise ValueError(
                "Semantic evidence must stay in this project's private capture"
            )
        project = runtime.application._imports.import_autocad_live_file(
            task.project_id,
            task.filename,
            Path(task.source_path),
            task.source_sha256,
            autocad_version=task.autocad_version,
            target=task.target,
            capacity=SourceGeometryCapacity.process_bounded(),
            native_capture_root=native_root,
        )
        receipt.write_text(
            json.dumps(
                {
                    "ok": True,
                    "project_id": project.id,
                    "state_version": project.state_version,
                    "source_sha256": task.source_sha256,
                    "features": len(project.geometry.feature_collection["features"])
                    if project.geometry
                    else 0,
                }
            ),
            encoding="utf-8",
        )
    except Exception as error:
        logging.getLogger(__name__).exception(
            "Native capture worker failed for project %s", task.project_id
        )
        failure = _failure_receipt(error)
        receipt.write_text(
            json.dumps(
                {
                    "ok": False,
                    "error": str(error),
                    "failure": failure.model_dump(mode="json") if failure else None,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    finally:
        reset_expected_project_version(token)
        if runtime is not None:
            runtime.close()


if __name__ == "__main__":
    execute(
        LiveImportTask.model_validate_json(
            Path(sys.argv[1]).read_text(encoding="utf-8")
        )
    )
