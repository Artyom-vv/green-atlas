"""Publish an isolated planting preview as a draft release and verify its DXF.

This tool only accepts an already completed diagnostic database and report.
It never mutates a user project or the source DXF.  The output directory is
new, and every release artifact is copied there with a machine-readable receipt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from io import BytesIO, StringIO
from pathlib import Path

import ezdxf
from app.composition import create_runtime
from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.contracts import SourceFile
from app.dxf_import.encoding import decode_text_dxf
from app.exporting.contracts import ReleaseCreateRequest
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.pattern_contracts import FillPatternRequest, PatternPreview


def run(source: Path, trial: Path, output: Path, scene_horizon: int) -> None:
    output.mkdir(parents=True, exist_ok=False)
    source_content = source.read_bytes()
    source_sha256 = hashlib.sha256(source_content).hexdigest()
    trial_report = json.loads((trial / "report.json").read_text())
    if trial_report.get("source_sha256") != source_sha256:
        raise ValueError("Trial receipt belongs to a different source DXF")
    if trial_report.get("status") != "preview_returned":
        raise ValueError("Trial does not contain a completed planting preview")
    if trial_report.get("diagnostic_inset_m") != 0:
        raise ValueError("Release requires the exact calculated work area")

    imported = EzdxfReader().read_prepared_file(source)
    recorded_preview = PatternPreview.model_validate(trial_report["preview"])
    if recorded_preview.change_set is None or not recorded_preview.change_set.can_apply:
        raise ValueError("Planting preview cannot be applied")

    runtime = create_runtime(trial / "isolated.sqlite3")
    try:
        summaries = runtime.application.list_projects()
        if len(summaries) != 1:
            raise ValueError("Trial database must contain exactly one project")
        project = runtime.application.get(summaries[0].id)
        if project.plan is None:
            raise ValueError("Trial plan is unavailable")
        project.source_file = SourceFile(
            name=source.name,
            content_sha256=source_sha256,
            size=len(source_content),
            imported_at=datetime.now(UTC).isoformat(),
            dxf_version=imported.dxf_version,
            units=imported.units,
            units_assumed=imported.units_assumed,
            entity_count=imported.entity_count,
            bounds=imported.bounds,
            warnings=list(imported.warnings),
        )
        runtime.project_repository.save(project, source=source_content)
        if project.plan.objects:
            if len(project.plan.objects) != recorded_preview.accepted_count:
                raise ValueError("Applied trial plan differs from the preview receipt")
            applied_ids = {item.id for item in project.plan.objects}
            plan_version = project.plan.version
        else:
            fresh_preview = runtime.application.preview_pattern(
                project.id,
                FillPatternRequest(
                    base_plan_version=project.plan.version,
                    zone_ids=[zone.id for zone in project.planting_zones],
                    placement_mode="count",
                    target_count=recorded_preview.requested_count,
                ),
            )
            if (
                fresh_preview.accepted_count != recorded_preview.accepted_count
                or fresh_preview.change_set is None
                or not fresh_preview.change_set.can_apply
            ):
                raise ValueError("Fresh planting preview differs from the trial receipt")
            change = fresh_preview.change_set
            applied = runtime.application.apply_change_set(
                project.id,
                PlanChangeSetApplyRequest(
                    preview_id=change.id,
                    digest=change.digest,
                    base_plan_version=change.base_plan_version,
                ),
            )
            applied_ids = set(applied.added_ids)
            plan_version = applied.plan_version
        release = runtime.application.create_release(
            project.id,
            ReleaseCreateRequest(mode="draft", scene_horizon=scene_horizon),
        )
        artifacts: dict[str, dict[str, object]] = {}
        contents: dict[str, bytes] = {}
        for artifact in release.artifacts:
            metadata, content = runtime.application.download_release_artifact(
                project.id, release.id, artifact.id
            )
            (output / metadata.filename).write_bytes(content)
            contents[metadata.kind] = content
            artifacts[metadata.kind] = {
                "filename": metadata.filename,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }

        exported = ezdxf.read(StringIO(decode_text_dxf(contents["dxf"])))
        planting = list(
            exported.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')
        )
        if len(planting) != recorded_preview.accepted_count:
            raise ValueError("Exported DXF planting count does not match preview")
        exported_ids = {
            str(tag.value).split("=", 1)[1]
            for entity in planting
            for tag in entity.get_xdata("GREEN_ATLAS")
            if tag.code == 1000 and str(tag.value).startswith("object_id=")
        }
        if exported_ids != applied_ids:
            raise ValueError("Exported DXF planting provenance is incomplete")

        with zipfile.ZipFile(BytesIO(contents["bundle"])) as archive:
            source_entries = [name for name in archive.namelist() if name.startswith("source/")]
            if len(source_entries) != 1:
                raise ValueError("Draft bundle does not contain one exact source DXF")
            bundled_source = archive.read(source_entries[0])

        result = {
            "scope": "isolated draft release; source semantics remain unreviewed",
            "source_sha256": source_sha256,
            "source_unchanged": hashlib.sha256(source.read_bytes()).hexdigest()
            == source_sha256,
            "bundled_source_sha256": hashlib.sha256(bundled_source).hexdigest(),
            "release_id": release.id,
            "release_status": release.status,
            "plan_version": plan_version,
            "plantings_applied": len(applied_ids),
            "plantings_reopened": len(planting),
            "planting_provenance_ids": len(exported_ids),
            "export_dxf_version": exported.dxfversion,
            "export_modelspace_entities": len(exported.modelspace()),
            "artifacts": artifacts,
            "native_autocad_reopen": "pending",
        }
        if result["bundled_source_sha256"] != source_sha256:
            raise ValueError("Release bundle changed the source DXF")
        (output / "verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2)
        )
        print(json.dumps(result, ensure_ascii=False))
    finally:
        runtime.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("trial", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--scene-horizon", type=int, default=20)
    args = parser.parse_args()
    run(args.source, args.trial, args.output, args.scene_horizon)
