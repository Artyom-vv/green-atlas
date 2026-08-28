from __future__ import annotations

import csv
from datetime import UTC, datetime
from hashlib import sha256
from html import escape
from io import BytesIO, StringIO
import json
from pathlib import Path
import zipfile

from app.contracts import Project, ReleaseArtifact, ReleaseCreateRequest, ReleasePackage, SceneSnapshot
from app.species.catalog import get_species


RULE_SET_REVISION = "green-atlas-spatial-draft@2026-08-28.1"
SPECIES_CATALOG_REVISION = "green-atlas-species@2026-08-28.1"
FIXED_ZIP_TIME = (2026, 8, 28, 0, 0, 0)


def release_identity(project: Project, request: ReleaseCreateRequest) -> str:
    if project.plan is None:
        raise ValueError("План ещё не создан")
    basis = {
        "project_id": project.id,
        "plan": project.plan.model_dump(mode="json"),
        "geometry_version": project.geometry_version,
        "mode": request.mode,
        "scene_horizon": request.scene_horizon,
        "rules": RULE_SET_REVISION,
        "catalog": SPECIES_CATALOG_REVISION,
    }
    digest = sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"release-{digest[:20]}"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _forecast(object_: object, field: str, horizon: int) -> tuple[str, str]:
    values = getattr(object_, field)
    item = next((candidate for candidate in values if candidate.horizon_year == horizon), None)
    return ("", "") if item is None else (str(item.radius_min_m), str(item.radius_max_m))


def planting_schedule(project: Project, horizon: int) -> bytes:
    assert project.plan is not None
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow([
        "object_id", "kind", "species_revision_id", "common_name", "size_class", "planting_zone_id",
        "x_m", "y_m", "status", "locked", f"canopy_{horizon}y_min_m", f"canopy_{horizon}y_max_m",
        f"root_{horizon}y_min_m", f"root_{horizon}y_max_m",
    ])
    for object_ in sorted(project.plan.objects, key=lambda item: item.id):
        species = get_species(object_.species_revision_id) if object_.species_revision_id else None
        canopy_min, canopy_max = _forecast(object_, "canopy_forecast", horizon)
        root_min, root_max = _forecast(object_, "root_forecast", horizon)
        writer.writerow([
            object_.id, object_.kind, object_.species_revision_id or "", species.common_name if species else "Вид не назначен",
            object_.size_class, object_.planting_zone_id or "", object_.x, object_.y, object_.status,
            "true" if object_.locked else "false", canopy_min, canopy_max, root_min, root_max,
        ])
    return "\ufeff".encode("utf-8") + stream.getvalue().encode("utf-8")


def dendroplan_svg(project: Project, horizon: int) -> bytes:
    assert project.plan is not None
    objects = sorted(project.plan.objects, key=lambda item: item.id)
    if objects:
        left = min(item.x - (item.layout_radius_m or item.radius) for item in objects)
        top = min(item.y - (item.layout_radius_m or item.radius) for item in objects)
        right = max(item.x + (item.layout_radius_m or item.radius) for item in objects)
        bottom = max(item.y + (item.layout_radius_m or item.radius) for item in objects)
    else:
        left = top = 0.0
        right = bottom = 10.0
    pad = max(2.0, (right - left + bottom - top) * 0.02)
    width = max(1.0, right - left + 2 * pad)
    height = max(1.0, bottom - top + 2 * pad)
    rows = [
        '<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc" '
        f'viewBox="{left - pad:.3f} {top - pad:.3f} {width:.3f} {height:.3f}">',
        f'<title id="title">Дендроплан {escape(project.name)}</title>',
        '<desc id="desc">Эскизный векторный слой посадок. Подложка и полный контекст сохранены в DXF.</desc>',
        '<g fill="none" stroke="#667085" stroke-width="0.12" vector-effect="non-scaling-stroke">',
    ]
    for object_ in objects:
        forecast = next((item for item in object_.canopy_forecast if item.horizon_year == horizon), None)
        radius = forecast.radius_max_m if forecast else (object_.layout_radius_m or object_.radius)
        color = "#16794c" if object_.kind == "tree" else "#568f42"
        rows.append(
            f'<g id="plant-{escape(object_.id)}" data-object-id="{escape(object_.id)}" data-species-revision-id="{escape(object_.species_revision_id or "")}">'
            f'<circle cx="{object_.x:.6f}" cy="{object_.y:.6f}" r="{radius:.6f}" fill="{color}" fill-opacity="0.16" stroke="{color}"/>'
            f'<circle cx="{object_.x:.6f}" cy="{object_.y:.6f}" r="0.18" fill="{color}" stroke="none"/>'
            f'</g>'
        )
    rows.extend(["</g>", "</svg>"])
    return ("\n".join(rows) + "\n").encode("utf-8")


def _artifact(project_id: str, release_id: str, kind: str, filename: str, media_type: str, content: bytes) -> ReleaseArtifact:
    artifact_id = f"{release_id}-{kind}"
    return ReleaseArtifact(
        id=artifact_id,
        filename=filename,
        kind=kind,
        media_type=media_type,
        size=len(content),
        sha256=sha256(content).hexdigest(),
        download_url=f"/api/projects/{project_id}/releases/{release_id}/artifacts/{artifact_id}",
    )


def _zip(entries: dict[str, bytes]) -> bytes:
    stream = BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for filename in sorted(entries):
            info = zipfile.ZipInfo(filename, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, entries[filename])
    return stream.getvalue()


def build_release(
    project: Project,
    request: ReleaseCreateRequest,
    release_id: str,
    dxf_content: bytes,
    scene: SceneSnapshot,
    source_content: bytes,
) -> tuple[ReleasePackage, dict[str, bytes]]:
    assert project.plan is not None and project.source_file is not None
    stem = Path(project.source_file.name).stem or "green-atlas"
    schedule = planting_schedule(project, request.scene_horizon)
    scene_content = _json_bytes(scene.model_dump(mode="json"))
    dendroplan = dendroplan_svg(project, request.scene_horizon)
    entries = {
        f"{stem}-planting-plan.dxf": dxf_content,
        f"{stem}-planting-schedule.csv": schedule,
        f"{stem}-scene-{request.scene_horizon}y.json": scene_content,
        f"{stem}-dendroplan.svg": dendroplan,
    }
    entry_hashes = {name: sha256(content).hexdigest() for name, content in sorted(entries.items())}
    missing_species = sorted(item.id for item in project.plan.objects if not item.species_revision_id)
    hard_errors = sorted(issue.id for issue in project.plan.issues if issue.severity == "error")
    warnings = [
        "Выпуск является проектным материалом и не означает согласование или выдачу порубочного билета.",
        "ПП-616 и ПП-1160 отражены как обязательные процессные основания, но их полный машиночитаемый реестр ещё не подключён.",
        "Почва, влажность, инсоляция, рельеф и высоты зданий не подтверждены исходным DXF.",
    ]
    if missing_species:
        warnings.append(f"Вид не назначен для {len(missing_species)} посадок.")
    if hard_errors:
        warnings.append(f"В плане осталось ошибок: {len(hard_errors)}.")
    manifest = {
        "schema": "green-atlas-release:1",
        "release_id": release_id,
        "mode": request.mode,
        "status": "draft" if request.mode == "draft" else "ready",
        "project": {
            "id": project.id,
            "name": project.name,
            "plan_version": project.plan.version,
            "geometry_version": project.geometry_version,
            "coordinate_reference": project.coordinate_reference.model_dump(mode="json"),
        },
        "source": {
            "filename": project.source_file.name,
            "size": len(source_content),
            "sha256": sha256(source_content).hexdigest(),
            "dxf_version": project.source_file.dxf_version,
            "units": project.source_file.units,
            "units_assumed": project.source_file.units_assumed,
        },
        "layer_mappings": [
            {"layer_id": layer.id, "source_name": layer.source_name, "kind": layer.mapped_kind, "geometry_complete": layer.geometry_complete}
            for layer in sorted(project.layers, key=lambda item: item.id)
        ],
        "objects": sorted(item.id for item in project.plan.objects),
        "object_count": len(project.plan.objects),
        "missing_species_object_ids": missing_species,
        "hard_error_ids": hard_errors,
        "rule_set_revision": RULE_SET_REVISION,
        "species_catalog_revision": SPECIES_CATALOG_REVISION,
        "regulatory_scope": {
            "spatial_draft": "implemented",
            "pp_743": "catalogue context only; specialist review required",
            "pp_616": "process requirement recorded; full machine-readable rules not implemented",
            "pp_1160": "process requirement recorded; full machine-readable rules not implemented",
        },
        "data_gaps": scene.data_gaps,
        "limitations": warnings,
        "files": entry_hashes,
    }
    manifest_content = _json_bytes(manifest)
    entries[f"{stem}-manifest.json"] = manifest_content
    bundle = _zip(entries)
    files = {
        "dxf": (f"{stem}-planting-plan.dxf", "application/dxf", dxf_content),
        "schedule": (f"{stem}-planting-schedule.csv", "text/csv; charset=utf-8", schedule),
        "manifest": (f"{stem}-manifest.json", "application/json", manifest_content),
        "scene": (f"{stem}-scene-{request.scene_horizon}y.json", "application/json", scene_content),
        "dendroplan": (f"{stem}-dendroplan.svg", "image/svg+xml", dendroplan),
        "bundle": (f"{stem}-release.zip", "application/zip", bundle),
    }
    artifacts = [_artifact(project.id, release_id, kind, filename, media_type, content) for kind, (filename, media_type, content) in files.items()]
    package = ReleasePackage(
        id=release_id,
        project_id=project.id,
        plan_version=project.plan.version,
        geometry_version=project.geometry_version,
        mode=request.mode,
        status="draft" if request.mode == "draft" else "ready",
        created_at=datetime.now(UTC).isoformat(),
        rule_set_revision=RULE_SET_REVISION,
        species_catalog_revision=SPECIES_CATALOG_REVISION,
        scene_horizon=request.scene_horizon,
        warnings=warnings,
        artifacts=artifacts,
    )
    return package, {artifact.id: files[artifact.kind][2] for artifact in artifacts}
