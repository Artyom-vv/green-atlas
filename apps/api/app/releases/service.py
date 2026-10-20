"""Комплект выпуска связывает исходный CAD, посадки и объяснения их проверки.

Модуль проверяет адреса и происхождение вложенных исходников, строит ведомость,
обзорный SVG и манифест, затем упаковывает точные байты. Он не восстанавливает
CAD, не выполняет нормативное согласование и не сохраняет выпуск в хранилище.
"""

from __future__ import annotations

import csv
import json
import zipfile
from datetime import UTC, datetime
from hashlib import sha256
from html import escape
from io import BytesIO, StringIO
from pathlib import Path
from posixpath import normpath
from typing import TYPE_CHECKING, TypedDict

from app.data_passport import build_data_passport
from app.exporting.cad_contracts import CadRelease
from app.exporting.contracts import (
    ReleaseArtifact,
    ReleaseArtifactKind,
    ReleaseCreateRequest,
    ReleasePackage,
)
from app.exporting.evidence import UNAVAILABLE_CHECKS, UnavailableReleaseEvidence
from app.geometry.checking.rule_trace import project_rule_traces
from app.planning.config import DEFAULT_PLANNING_HORIZON_YEAR
from app.projects.contracts import Project
from app.regulations.network_summary import network_review_reason
from app.regulations.profiles import LCT_REQUIREMENT_PROFILE, requirement_profile
from app.regulations.reference_catalog import REFERENCE_CATALOG
from app.regulations.registry import (
    REGISTRY_REVISION,
    applied_record_ids,
    registry_snapshot,
)
from app.releases.config import (
    FIXED_ZIP_TIME,
    RELEASE_DEFAULT_STEM,
    RULE_SET_REVISION,
    SPECIES_CATALOG_REVISION,
    SVG_CANOPY_OPACITY,
    SVG_CENTER_RADIUS,
    SVG_COORDINATE_FORMAT,
    SVG_EMPTY_EXTENT,
    SVG_EMPTY_ORIGIN,
    SVG_MIN_EXTENT,
    SVG_MIN_PADDING,
    SVG_OUTLINE_COLOR,
    SVG_PADDING_RATIO,
    SVG_SHRUB_COLOR,
    SVG_STROKE_WIDTH,
    SVG_TREE_COLOR,
    SVG_VIEWBOX_FORMAT,
    ZIP_COMPRESSION_LEVEL,
    ZIP_FILE_ATTRIBUTES,
)
from app.releases.manifest_serialization import manifest_bytes
from app.scene.contracts import SceneSnapshot
from app.shared.failures import DomainFailure, FailureReason
from app.shared.messages import message
from app.species.catalog import get_species
from app.species.forecast import ForecastAnchorSequence, forecast_at

if TYPE_CHECKING:
    from app.geometry.ports import GeometryRuleTracePort

ArtifactContent = tuple[str, str, bytes]


def _baseline_plan_payload(project: Project) -> dict[str, object]:
    """Выпуск описывает текущие посадки; прогноз изображения задан отдельно."""
    assert project.plan is not None
    payload = project.plan.model_dump(mode="json")
    payload["planning_horizon_year"] = DEFAULT_PLANNING_HORIZON_YEAR
    return payload


def release_identity(project: Project, request: ReleaseCreateRequest) -> str:
    """Определяет идентичность выпуска по неизменным версиям и параметрам."""

    if project.plan is None:
        raise DomainFailure(FailureReason.PLAN_MISSING)
    basis = {
        "project_id": project.id,
        "plan": _baseline_plan_payload(project),
        "geometry_version": project.geometry_version,
        "mode": request.mode,
        "scene_horizon": request.scene_horizon,
        "regulatory_basis": request.regulatory_basis.model_dump(mode="json")
        if request.regulatory_basis is not None
        else None,
        "rules": RULE_SET_REVISION,
        "regulatory_registry": REGISTRY_REVISION,
        "regulatory_requirement_profile": LCT_REQUIREMENT_PROFILE,
        "applied_rule_ids": applied_record_ids(
            [issue.rule_id for issue in project.plan.issues]
        ),
        "catalog": SPECIES_CATALOG_REVISION,
    }
    digest = sha256(
        json.dumps(
            basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    return f"release-{digest[:20]}"


def planting_schedule(project: Project, horizon: int) -> bytes:
    """Составляет ведомость с исходными габаритами и отдельными прогнозами роста."""

    assert project.plan is not None
    stream = StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerow(
        [
            "object_id",
            "kind",
            "species_revision_id",
            "common_name",
            "size_class",
            "planting_zone_id",
            "x_m",
            "y_m",
            "status",
            "locked",
            f"canopy_{horizon}y_min_m",
            f"canopy_{horizon}y_max_m",
            f"root_{horizon}y_min_m",
            f"root_{horizon}y_max_m",
        ]
    )
    for object_ in sorted(project.plan.objects, key=lambda item: item.id):
        species = (
            get_species(object_.species_revision_id)
            if object_.species_revision_id
            else None
        )
        canopy_min, canopy_max = _forecast(object_.canopy_forecast, horizon)
        root_min, root_max = _forecast(object_.root_forecast, horizon)
        writer.writerow(
            [
                object_.id,
                object_.kind,
                object_.species_revision_id or "",
                species.common_name
                if species
                else message("release.schedule.unknown_species"),
                object_.size_class,
                object_.planting_zone_id or "",
                object_.x,
                object_.y,
                object_.status,
                "true" if object_.locked else "false",
                canopy_min,
                canopy_max,
                root_min,
                root_max,
            ]
        )
    return "\ufeff".encode("utf-8") + stream.getvalue().encode("utf-8")


def dendroplan_svg(project: Project, horizon: int) -> bytes:
    """Создаёт обзорный SVG посадок; он не заменяет расчётную подоснову."""

    assert project.plan is not None
    objects = sorted(project.plan.objects, key=lambda item: item.id)
    if objects:
        left = min(item.x - (item.layout_radius_m or item.radius) for item in objects)
        top = min(item.y - (item.layout_radius_m or item.radius) for item in objects)
        right = max(item.x + (item.layout_radius_m or item.radius) for item in objects)
        bottom = max(item.y + (item.layout_radius_m or item.radius) for item in objects)
    else:
        left = top = SVG_EMPTY_ORIGIN
        right = bottom = SVG_EMPTY_EXTENT
    pad = max(SVG_MIN_PADDING, (right - left + bottom - top) * SVG_PADDING_RATIO)
    width = max(SVG_MIN_EXTENT, right - left + 2 * pad)
    height = max(SVG_MIN_EXTENT, bottom - top + 2 * pad)
    title = message("release.svg.title", {"project_name": escape(project.name)})
    description = message("release.svg.description")
    rows = [
        '<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="title desc" '
        f'viewBox="{left - pad:{SVG_VIEWBOX_FORMAT}} {top - pad:{SVG_VIEWBOX_FORMAT}} {width:{SVG_VIEWBOX_FORMAT}} {height:{SVG_VIEWBOX_FORMAT}}">',
        f'<title id="title">{title}</title>',
        f'<desc id="desc">{description}</desc>',
        f'<g fill="none" stroke="{SVG_OUTLINE_COLOR}" stroke-width="{SVG_STROKE_WIDTH}" vector-effect="non-scaling-stroke">',
    ]
    for object_ in objects:
        forecast = forecast_at(object_.canopy_forecast, horizon)
        radius = (
            forecast.radius_max_m
            if forecast
            else (object_.layout_radius_m or object_.radius)
        )
        color = SVG_TREE_COLOR if object_.kind == "tree" else SVG_SHRUB_COLOR
        rows.append(
            f'<g id="plant-{escape(object_.id)}" data-object-id="{escape(object_.id)}" data-species-revision-id="{escape(object_.species_revision_id or "")}">'
            f'<circle cx="{object_.x:{SVG_COORDINATE_FORMAT}}" cy="{object_.y:{SVG_COORDINATE_FORMAT}}" r="{radius:{SVG_COORDINATE_FORMAT}}" fill="{color}" fill-opacity="{SVG_CANOPY_OPACITY}" stroke="{color}"/>'
            f'<circle cx="{object_.x:{SVG_COORDINATE_FORMAT}}" cy="{object_.y:{SVG_COORDINATE_FORMAT}}" r="{SVG_CENTER_RADIUS}" fill="{color}" stroke="none"/>'
            f"</g>"
        )
    rows.extend(["</g>", "</svg>"])
    return ("\n".join(rows) + "\n").encode("utf-8")


def zip_release_entries(entries: dict[str, bytes]) -> bytes:
    """Упаковывает именованные байты детерминированно, без изменения их содержимого."""

    stream = BytesIO()
    with zipfile.ZipFile(
        stream,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=ZIP_COMPRESSION_LEVEL,
    ) as archive:
        for filename in sorted(entries):
            info = zipfile.ZipInfo(filename, date_time=FIXED_ZIP_TIME)
            # Права уже записаны в Unix-формате; не наследуем ОС сборщика ZIP.
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = ZIP_FILE_ATTRIBUTES
            archive.writestr(info, entries[filename])
    return stream.getvalue()


def build_release(
    project: Project,
    request: ReleaseCreateRequest,
    release_id: str,
    dxf_content: bytes,
    scene: SceneSnapshot,
    source_content: bytes,
    source_components: dict[str, bytes] | None = None,
    *,
    geometry: GeometryRuleTracePort | None = None,
    cad: CadRelease | None = None,
) -> tuple[ReleasePackage, dict[str, bytes]]:
    """Собирает проверенный комплект; сохранение и публикация принадлежат вызывающему сервису."""

    assert project.plan is not None and project.source_file is not None
    stem = Path(project.source_file.name).stem or RELEASE_DEFAULT_STEM
    files: dict[ReleaseArtifactKind, ArtifactContent] = {
        "schedule": (
            f"{stem}-planting-schedule.csv",
            "text/csv; charset=utf-8",
            planting_schedule(project, request.scene_horizon),
        ),
        "scene": (
            f"{stem}-scene-{request.scene_horizon}y.json",
            "application/json",
            _json_bytes(scene.model_dump(mode="json")),
        ),
        "dendroplan": (
            f"{stem}-dendroplan.svg",
            "image/svg+xml",
            dendroplan_svg(project, request.scene_horizon),
        ),
    }
    entries = {filename: content for filename, _, content in files.values()}
    source_files = _checked_source_entries(
        project, entries, stem, dxf_content, source_content, source_components, cad
    )
    entry_hashes = {
        name: sha256(content).hexdigest() for name, content in sorted(entries.items())
    }
    manifest, warnings = _release_manifest(
        project, request, release_id, scene, source_files, entry_hashes, geometry, cad
    )
    manifest_content = manifest_bytes(manifest, project.geometry)
    manifest_name = f"{stem}-manifest.json"
    entries[manifest_name] = manifest_content
    bundle = zip_release_entries(entries)
    # Порядок артефактов является частью ответа API, а порядок записей ZIP задаёт упаковщик.
    files = {
        "schedule": files["schedule"],
        "manifest": (manifest_name, "application/json", manifest_content),
        "scene": files["scene"],
        "dendroplan": files["dendroplan"],
        "bundle": (f"{stem}-release.zip", "application/zip", bundle),
    }
    if cad is None:
        files["dxf"] = (f"{stem}-planting-plan.dxf", "application/dxf", dxf_content)
    else:
        files["cad"] = (
            f"{stem}-cad.zip",
            "application/zip",
            zip_release_entries(cad.files),
        )
    return _release_package(project, request, release_id, warnings, files)


def _safe_archive_name(name: str) -> bool:
    """Отсекает обход каталогов, абсолютные пути и платформенные разделители."""

    if not name or "\\" in name or "\x00" in name:
        return False
    # Даже при чтении ZIP в памяти адрес записи должен оставаться однозначным.
    # Пустые сегменты не меняют поиск, а «.» и «..» допускают обход каталогов.
    if any(part in {".", ".."} for part in name.split("/")):
        return False
    normalized = normpath(name)
    return (
        not name.startswith(("/", "\\"))
        and normalized not in {".", ".."}
        and not normalized.startswith("../")
        and "/../" not in f"/{normalized}/"
    )


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _forecast(values: ForecastAnchorSequence, horizon: int) -> tuple[str, str]:
    """Форматирует общий прогноз; вне опорного интервала ячейки остаются пустыми."""

    item = forecast_at(values, horizon)
    return (
        ("", "") if item is None else (str(item.radius_min_m), str(item.radius_max_m))
    )


def _checked_source_entries(
    project: Project,
    entries: dict[str, bytes],
    stem: str,
    dxf_content: bytes,
    source_content: bytes,
    source_components: dict[str, bytes] | None,
    cad: CadRelease | None,
) -> dict[str, bytes]:
    """Проверяет состав исходников до добавления в архив и сохраняет порядок отказов."""

    assert project.source_file is not None
    if cad is None:
        entries[f"{stem}-planting-plan.dxf"] = dxf_content
    else:
        for path, content in cad.files.items():
            if not _safe_archive_name(path):
                raise ValueError(message("release.source.invalid_cad_path"))
            entries[f"cad/{path}"] = content
    source_components = source_components or {}
    provenance = project.source_file.prepared_provenance
    primary_path = (
        "autocad-snapshot.json"
        if cad is not None
        else provenance.entry
        if provenance is not None
        else project.source_file.name
    )
    if primary_path in source_components:
        raise ValueError(message("release.source.primary_duplicate"))
    if source_components and (provenance is None or not provenance.drawings):
        raise ValueError(message("release.source.provenance_missing"))
    source_files = {primary_path: source_content, **source_components}
    if provenance is not None and provenance.drawings:
        expected = {item.path: item for item in provenance.drawings}
        if set(source_files) != set(expected) or any(
            len(source_files[path]) != item.source_bytes
            or sha256(source_files[path]).hexdigest() != item.source_sha256
            for path, item in expected.items()
        ):
            raise ValueError(message("release.source.set_mismatch"))
    for path, content in source_files.items():
        archive_path = f"source/{path}"
        if not _safe_archive_name(archive_path):
            raise ValueError(message("release.source.invalid_path"))
        entries[archive_path] = content
    return source_files


class _PlanEvidence(TypedDict):
    """Поля манифеста, принадлежащие объяснению плана; это не состояние проекта."""

    objects: list[str]
    object_count: int
    plan: dict[str, object]
    planting_zones: list[dict[str, object]]
    groups: list[str]
    missing_species_object_ids: list[str]
    hard_error_ids: list[str]
    regulatory_registry: dict[str, object]
    planting_rule_traces: dict[str, dict[str, object]]


def _plan_evidence(
    project: Project,
    request: ReleaseCreateRequest,
    geometry: GeometryRuleTracePort | None,
) -> tuple[_PlanEvidence, list[str]]:
    """Собирает объяснения плана и предупреждения, не подменяя нормативное согласование."""

    assert project.plan is not None
    missing_species = sorted(
        item.id for item in project.plan.objects if not item.species_revision_id
    )
    hard_errors = sorted(
        issue.id for issue in project.plan.issues if issue.severity == "error"
    )
    warnings = [
        message("release.warning.project_material"),
        (
            message("release.warning.regulatory_basis_present")
            if request.regulatory_basis is not None
            else message("release.warning.regulatory_basis_missing")
        ),
        message("release.warning.environment_unconfirmed"),
    ]
    if missing_species:
        warnings.append(
            message("release.warning.missing_species", {"count": len(missing_species)})
        )
    if isinstance(geometry, UnavailableReleaseEvidence):
        warnings.append(UNAVAILABLE_CHECKS)
    if project.source_review is not None:
        warnings.append(message("release.warning.source_review"))
    if hard_errors:
        warnings.append(
            message("release.warning.plan_errors", {"count": len(hard_errors)})
        )
    plan_payload = _baseline_plan_payload(project)
    planting_zone_payload = [
        zone.model_dump(mode="json") for zone in project.planting_zones
    ]
    groups = sorted(
        {group_id for item in project.plan.objects for group_id in item.group_ids}
    )
    traces = project_rule_traces(project, geometry=geometry)
    network_limitation = network_review_reason(traces.values())
    if network_limitation:
        warnings.append(network_limitation)
    traced_rule_ids = [
        entry.rule_id
        for trace in traces.values()
        for entry in trace.entries
        if entry.status in {"passed", "failed"} and entry.rule_id is not None
    ]
    return {
        "objects": sorted(item.id for item in project.plan.objects),
        "object_count": len(project.plan.objects),
        "plan": plan_payload,
        "planting_zones": planting_zone_payload,
        "groups": groups,
        "missing_species_object_ids": missing_species,
        "hard_error_ids": hard_errors,
        "regulatory_registry": registry_snapshot(
            [*traced_rule_ids, *[issue.rule_id for issue in project.plan.issues]]
        ),
        "planting_rule_traces": {
            object_id: trace.model_dump(mode="json")
            for object_id, trace in traces.items()
        },
    }, warnings


def _source_metadata(
    project: Project,
    source_files: dict[str, bytes],
    entry_hashes: dict[str, str],
) -> dict[str, object]:
    """Описывает вложенные исходники по уже вычисленным хешам архивных записей."""

    assert project.source_file is not None
    primary_path = next(iter(source_files))
    source_metadata: dict[str, object] = {
        "prepared_provenance": project.source_file.prepared_provenance.model_dump(
            mode="json"
        )
        if project.source_file.prepared_provenance is not None
        else None,
        "accept_partial_geometry": project.source_file.accept_partial_geometry,
        "filename": project.source_file.name,
        "path": f"source/{primary_path}",
        "size": len(source_files[primary_path]),
        "sha256": entry_hashes[f"source/{primary_path}"],
        "embedded": True,
        "dxf_version": project.source_file.dxf_version,
        "units": project.source_file.units,
        "units_assumed": project.source_file.units_assumed,
        "drawings": [
            {
                "path": path,
                "size": len(content),
                "sha256": entry_hashes[f"source/{path}"],
                "archive_path": f"source/{path}",
            }
            for path, content in sorted(source_files.items())
        ],
    }
    return source_metadata


def _layer_mappings(project: Project) -> list[dict[str, object]]:
    """Проецирует подтверждения и полноту слоёв без изменения их расчётной роли."""

    return [
        {
            "layer_id": layer.id,
            "source_name": layer.source_name,
            "kind": layer.mapped_kind,
            "suggested_kind": layer.suggested_kind,
            "suggestion_confidence": layer.suggestion_confidence,
            "suggestion_reasons": layer.suggestion_reasons,
            "mapping_review_required": layer.mapping_review_required,
            "mapping_confirmed": bool(
                layer.mapping_confirmed or not layer.mapping_review_required
            ),
            "parsing_status": "complete" if layer.geometry_complete else "partial",
            "semantic_status": "excluded"
            if str(layer.mapped_kind) == "LayerKind.IGNORE"
            or getattr(layer.mapped_kind, "value", layer.mapped_kind) == "ignore"
            else "classified",
            "used_in_calculation": bool(
                project.source_review is None
                and layer.geometry_complete
                and getattr(layer.mapped_kind, "value", layer.mapped_kind)
                not in {None, "ignore", "unclassified"}
            ),
            "utility_context": layer.utility_context.model_dump(mode="json")
            if layer.utility_context is not None
            else None,
            "utility_axis_bindings": [
                binding.model_dump(mode="json")
                for binding in layer.utility_axis_bindings
            ],
        }
        for layer in sorted(project.layers, key=lambda item: item.id)
    ]


def _release_manifest(
    project: Project,
    request: ReleaseCreateRequest,
    release_id: str,
    scene: SceneSnapshot,
    source_files: dict[str, bytes],
    entry_hashes: dict[str, str],
    geometry: GeometryRuleTracePort | None,
    cad: CadRelease | None,
) -> tuple[dict[str, object], list[str]]:
    """Соединяет проекции в контракт манифеста; геометрия добавляется сериализатором отдельно."""

    assert project.plan is not None
    evidence, warnings = _plan_evidence(project, request, geometry)
    source_metadata = _source_metadata(project, source_files, entry_hashes)
    manifest: dict[str, object] = {
        # Native-выпуск не передаётся старому восстановителю комплектов DXF.
        "schema": "green-atlas-release:3"
        if cad is not None
        else "green-atlas-release:2",
        "editable": cad is None,
        "release_id": release_id,
        "mode": request.mode,
        "status": "draft" if request.mode == "draft" else "ready",
        "project": {
            "source_review": project.source_review.model_dump(mode="json")
            if project.source_review is not None
            else None,
            "id": project.id,
            "name": project.name,
            "state_version": project.state_version,
            "plan_version": project.plan.version,
            "planning_horizon_year": DEFAULT_PLANNING_HORIZON_YEAR,
            "geometry_version": project.geometry_version,
            "status": project.status.value,
            "coordinate_reference": project.coordinate_reference.model_dump(
                mode="json"
            ),
            "planting_zones": evidence["planting_zones"],
        },
        "source": source_metadata,
        "layer_mappings": _layer_mappings(project),
        "objects": evidence["objects"],
        "object_count": evidence["object_count"],
        "plan": evidence["plan"],
        "planting_zones": evidence["planting_zones"],
        "groups": evidence["groups"],
        "views": {
            "scene_horizon": request.scene_horizon,
            "dendroplan": "svg",
        },
        "missing_species_object_ids": evidence["missing_species_object_ids"],
        "hard_error_ids": evidence["hard_error_ids"],
        "rule_set_revision": RULE_SET_REVISION,
        "regulatory_registry": evidence["regulatory_registry"],
        "regulatory_requirement_profile": requirement_profile().model_dump(mode="json"),
        "planting_rule_traces": evidence["planting_rule_traces"],
        "species_catalog_revision": SPECIES_CATALOG_REVISION,
        "regulatory_scope": dict(REFERENCE_CATALOG.release_scope),
        "regulatory_basis": request.regulatory_basis.model_dump(mode="json")
        if request.regulatory_basis is not None
        else None,
        "data_passport": build_data_passport(project, geometry=geometry).model_dump(
            mode="json"
        ),
        "data_gaps": scene.data_gaps,
        "limitations": warnings,
        "files": entry_hashes,
    }
    if cad is not None:
        manifest["cad"] = {
            "entry": f"cad/{cad.entry}",
            "snapshot_sha256": source_metadata["sha256"],
            "reopen_receipt": cad.receipt.model_dump(mode="json", by_alias=True),
        }
        source_metadata["format"] = "autocad_snapshot"
    return manifest, warnings


def _artifact(
    project_id: str,
    release_id: str,
    kind: ReleaseArtifactKind,
    filename: str,
    media_type: str,
    content: bytes,
) -> ReleaseArtifact:
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


def _release_package(
    project: Project,
    request: ReleaseCreateRequest,
    release_id: str,
    warnings: list[str],
    files: dict[ReleaseArtifactKind, ArtifactContent],
) -> tuple[ReleasePackage, dict[str, bytes]]:
    """Создаёт описание артефактов из того же состава байтов, который будет сохранён."""

    assert project.plan is not None
    artifacts = [
        _artifact(project.id, release_id, kind, filename, media_type, content)
        for kind, (filename, media_type, content) in files.items()
    ]
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
        planning_horizon_year=DEFAULT_PLANNING_HORIZON_YEAR,
        warnings=warnings,
        artifacts=artifacts,
    )
    return package, {artifact.id: files[artifact.kind][2] for artifact in artifacts}
