from __future__ import annotations

import base64
import csv
import json
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from html import escape
from io import BytesIO, StringIO
from pathlib import Path
from posixpath import normpath
from typing import TYPE_CHECKING, Any
from app.exporting.cad_contracts import CadRelease
from app.exporting.evidence import UnavailableReleaseEvidence, UNAVAILABLE_CHECKS

from app.contracts import (
    GeometrySnapshot,
    Plan,
    PlantingZoneAssignment,
    Project,
    ReleaseArtifact,
    ReleaseCreateRequest,
    ReleasePackage,
    SceneSnapshot,
)
from app.data_passport import build_data_passport
from app.dxf_import.limits import MAX_DXF_CONTENT_BYTES, validate_dxf_filename
from app.geometry.rule_trace import project_rule_traces
from app.regulations.network_summary import network_review_reason
from app.regulations.profiles import LCT_REQUIREMENT_PROFILE, requirement_profile
from app.regulations.registry import (
    REGISTRY_REVISION,
    applied_record_ids,
    registry_snapshot,
)
from app.species.catalog import forecast_at, get_species

if TYPE_CHECKING:
    from app.geometry.ports import GeometryEnginePort

RULE_SET_REVISION = "green-atlas-spatial-draft@2026-08-28.1"
SPECIES_CATALOG_REVISION = "green-atlas-species@2026-08-28.1"
FIXED_ZIP_TIME = (2026, 8, 28, 0, 0, 0)

# A release bundle may contain the source DXF, a derived DXF and a full map
# snapshot.  Keep the archive bounded independently of the 50 MB source DXF
# limit so a malformed upload cannot turn ZIP extraction into an unbounded
# memory allocation.
MAX_RELEASE_BUNDLE_BYTES = 120 * 1024 * 1024
MAX_RELEASE_BUNDLE_ENTRIES = 32
MAX_RELEASE_BUNDLE_UNCOMPRESSED_BYTES = 180 * 1024 * 1024


@dataclass(frozen=True)
class ParsedReleaseBundle:
    """Validated bytes and editable state extracted from a release ZIP."""

    manifest: dict[str, Any]
    source_filename: str
    source_path: str
    source_content: bytes
    source_components: dict[str, bytes]
    dxf_content: bytes | None
    plan: Plan | None
    geometry: GeometrySnapshot | None
    planting_zones: list[PlantingZoneAssignment]
    editable: bool
    read_only_reason: str | None = None


def _safe_archive_name(name: str) -> bool:
    """Reject traversal, absolute paths and platform-specific separators."""

    if not name or "\\" in name or "\x00" in name:
        return False
    # A ZIP is read in memory today, but rejecting dot segments keeps the
    # contract safe if a future adapter materialises an entry on disk. Empty
    # segments are harmless for lookup, while ``.`` and ``..`` are ambiguous
    # and therefore never accepted.
    if any(part in {".", ".."} for part in name.split("/")):
        return False
    normalized = normpath(name)
    return not name.startswith(("/", "\\")) and normalized not in {".", ".."} and not normalized.startswith("../") and "/../" not in f"/{normalized}/"


def _read_release_entries(content: bytes | bytearray) -> dict[str, bytes]:
    if len(content) > MAX_RELEASE_BUNDLE_BYTES:
        raise ValueError(f"Пакет выпуска должен быть не больше {MAX_RELEASE_BUNDLE_BYTES // 1024 // 1024} МБ")
    try:
        archive = zipfile.ZipFile(BytesIO(bytes(content)))
    except (OSError, zipfile.BadZipFile) as error:
        raise ValueError("Пакет выпуска повреждён или не является ZIP-файлом") from error
    infos = archive.infolist()
    if not infos:
        raise ValueError("Пакет выпуска пуст")
    if len(infos) > MAX_RELEASE_BUNDLE_ENTRIES:
        raise ValueError("Пакет выпуска содержит слишком много файлов")
    names: set[str] = set()
    total_size = 0
    entries: dict[str, bytes] = {}
    try:
        for info in infos:
            if not _safe_archive_name(info.filename) or info.filename in names:
                raise ValueError("Пакет выпуска содержит небезопасное имя файла")
            # POSIX symlinks are represented by the upper mode bits in the
            # external attributes.  They must not be followed during import.
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError("Пакет выпуска содержит символическую ссылку")
            if info.is_dir():
                raise ValueError("Пакет выпуска содержит каталог вместо файла")
            if info.file_size > MAX_RELEASE_BUNDLE_UNCOMPRESSED_BYTES:
                raise ValueError("Файл внутри пакета выпуска слишком велик")
            total_size += info.file_size
            if total_size > MAX_RELEASE_BUNDLE_UNCOMPRESSED_BYTES:
                raise ValueError("Распакованный пакет выпуска слишком велик")
            with archive.open(info, "r") as stream:
                value = stream.read(MAX_RELEASE_BUNDLE_UNCOMPRESSED_BYTES + 1)
            if len(value) != info.file_size:
                raise ValueError("Пакет выпуска содержит повреждённый файл")
            names.add(info.filename)
            entries[info.filename] = value
    except ValueError:
        raise
    except (OSError, EOFError, RuntimeError, zipfile.BadZipFile) as error:
        raise ValueError("Не удалось прочитать пакет выпуска") from error
    finally:
        archive.close()
    return entries


def _manifest_entry(entries: dict[str, bytes]) -> tuple[str, dict[str, Any]]:
    candidates = [
        (name, data)
        for name, data in entries.items()
        if Path(name).name.lower() == "manifest.json" or Path(name).name.lower().endswith("-manifest.json")
    ]
    if len(candidates) != 1:
        raise ValueError("В пакете выпуска должен быть один manifest.json")
    name, content = candidates[0]
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Manifest выпуска повреждён") from error
    if not isinstance(value, dict):
        raise ValueError("Manifest выпуска имеет неверную структуру")
    return name, value


def _decode_inline_source(source: dict[str, Any]) -> bytes | None:
    encoded = source.get("content_base64")
    if encoded is None:
        return None
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("Исходный DXF в manifest имеет неверный формат")
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise ValueError("Исходный DXF в manifest повреждён") from error
    if len(decoded) > MAX_DXF_CONTENT_BYTES:
        raise ValueError("Исходный DXF внутри пакета выпуска слишком велик")
    return decoded


def _plan_from_manifest(manifest: dict[str, Any]) -> Plan | None:
    payload = manifest.get("plan")
    if payload is None and isinstance(manifest.get("project"), dict):
        payload = manifest["project"].get("plan")
    if payload is None:
        return None
    try:
        plan = Plan.model_validate(payload)
    except Exception as error:
        raise ValueError("План в manifest имеет неверную структуру") from error
    ids = [item.id for item in plan.objects]
    if any(not item_id for item_id in ids) or len(set(ids)) != len(ids):
        raise ValueError("План в manifest содержит неуникальные идентификаторы посадок")
    # A release manifest's compact object index is an audit aid.  If present,
    # it must agree with the complete object records before we allow editing.
    listed = manifest.get("objects")
    if listed is not None:
        if not isinstance(listed, list):
            raise ValueError("Список объектов в manifest имеет неверную структуру")
        if sorted(str(item) for item in listed) != sorted(ids):
            raise ValueError("Список объектов в manifest не совпадает с планом")
    return plan


def parse_release_bundle(content: bytes | bytearray) -> ParsedReleaseBundle:
    """Parse a release ZIP without mutating a project.

    New bundles carry every exact source DXF as a hashed ZIP entry. Bundles
    created before that contract may carry the primary source inline as
    base64. Older bundles containing only a derived planting DXF remain
    importable for inspection, but are explicitly read-only because their
    project semantics cannot be reconstructed safely.
    """

    entries = _read_release_entries(content)
    _manifest_name, manifest = _manifest_entry(entries)
    schema = manifest.get("schema")
    if schema not in {"green-atlas-release:1", "green-atlas-release:2"}:
        raise ValueError("Версия manifest выпуска не поддерживается")
    declared_editable = manifest.get("editable")
    if declared_editable is not None and not isinstance(declared_editable, bool):
        raise ValueError("Manifest содержит неверный признак editable")
    listed_files = manifest.get("files")
    if listed_files is not None:
        if not isinstance(listed_files, dict):
            raise ValueError("Manifest содержит неверный реестр файлов")
        for name, expected_hash in listed_files.items():
            if not isinstance(name, str) or not _safe_archive_name(name) or name not in entries:
                raise ValueError("Manifest ссылается на отсутствующий файл")
            if not isinstance(expected_hash, str) or sha256(entries[name]).hexdigest() != expected_hash:
                raise ValueError(f"Хеш файла выпуска не совпадает с manifest: {name}")
    source = manifest.get("source")
    if not isinstance(source, dict):
        source = {}
    raw_source_filename = str(source.get("filename") or "source.dxf")
    if not _safe_archive_name(raw_source_filename) or "/" in raw_source_filename:
        raise ValueError("Manifest не содержит корректного имени исходного DXF")
    source_filename = Path(raw_source_filename).name
    try:
        validate_dxf_filename(source_filename)
    except ValueError as error:
        raise ValueError("Manifest не содержит корректного имени исходного DXF") from error
    inline_source = _decode_inline_source(source)
    source_path = source.get("path")
    source_content = inline_source
    source_is_exact = inline_source is not None
    if source_content is None and isinstance(source_path, str):
        if not _safe_archive_name(source_path):
            raise ValueError("Manifest ссылается на небезопасный исходный файл")
        source_content = entries.get(source_path)
        source_is_exact = source_content is not None
    dxf_candidates = [
        (name, value)
        for name, value in entries.items()
        if name.lower().endswith(".dxf")
    ]
    dxf_name: str | None = None
    dxf_content: bytes | None = None
    for name, value in dxf_candidates:
        if "planting-plan" in Path(name).stem.lower() or "plan" in Path(name).stem.lower():
            dxf_name, dxf_content = name, value
            break
    if dxf_content is None and dxf_candidates:
        dxf_name, dxf_content = dxf_candidates[0]
    if source_content is None:
        # A v1 package has no source entry. Reusing its derived DXF would turn
        # project trees into source context, so permit it only as a preview.
        if dxf_content is None:
            raise ValueError("В пакете выпуска не найден исходный DXF")
        source_content = dxf_content
        source_filename = Path(dxf_name or source_filename).name
        if not source_filename.lower().endswith(".dxf"):
            source_filename = "release-plan.dxf"
    if len(source_content) > MAX_DXF_CONTENT_BYTES:
        raise ValueError("Исходный DXF внутри пакета выпуска слишком велик")
    source_size = source.get("size")
    if source_is_exact and source_size is not None and (isinstance(source_size, bool) or not isinstance(source_size, int) or source_size != len(source_content)):
        raise ValueError("Размер исходного DXF не совпадает с manifest")
    expected_hash = source.get("sha256")
    if source_is_exact and expected_hash and (not isinstance(expected_hash, str) or sha256(source_content).hexdigest() != expected_hash):
        raise ValueError("Хеш исходного DXF не совпадает с manifest")
    source_path = source_filename
    prepared = source.get("prepared_provenance")
    if isinstance(prepared, dict) and isinstance(prepared.get("entry"), str):
        source_path = prepared["entry"]
    drawing_records = source.get("drawings")
    source_components: dict[str, bytes] = {}
    if drawing_records is not None:
        if not isinstance(drawing_records, list) or not drawing_records:
            raise ValueError("Manifest содержит неверный список исходных DXF")
        seen: set[str] = set()
        primary_recorded = False
        for record in drawing_records:
            if not isinstance(record, dict):
                raise ValueError("Manifest содержит неверный исходный DXF")
            path = record.get("path")
            archive_path = record.get("archive_path")
            if (
                not isinstance(path, str)
                or not _safe_archive_name(path)
                or not path.lower().endswith(".dxf")
                or path in seen
                or not isinstance(archive_path, str)
                or not _safe_archive_name(archive_path)
            ):
                raise ValueError("Manifest содержит небезопасный исходный DXF")
            seen.add(path)
            drawing_content = entries.get(archive_path)
            if drawing_content is None:
                raise ValueError("Исходный DXF отсутствует в пакете выпуска")
            size = record.get("size")
            digest = record.get("sha256")
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or size != len(drawing_content)
                or not isinstance(digest, str)
                or sha256(drawing_content).hexdigest() != digest
            ):
                raise ValueError("Исходный DXF не совпадает с manifest")
            if path == source_path:
                if drawing_content != source_content:
                    raise ValueError("Основной DXF в пакете не совпадает с manifest")
                primary_recorded = True
            else:
                source_components[path] = bytes(drawing_content)
        if not primary_recorded:
            raise ValueError("Основной DXF отсутствует в списке исходников")
    plan = _plan_from_manifest(manifest)
    geometry_payload = manifest.get("geometry")
    if geometry_payload is None and isinstance(manifest.get("project"), dict):
        geometry_payload = manifest["project"].get("geometry")
    geometry = None
    if geometry_payload is not None:
        try:
            geometry = GeometrySnapshot.model_validate(geometry_payload)
        except Exception as error:
            raise ValueError("Геометрия в manifest имеет неверную структуру") from error
    zone_payload: Any = manifest.get("planting_zones")
    project_payload = manifest.get("project")
    if zone_payload is None and isinstance(project_payload, dict):
        zone_payload = project_payload.get("planting_zones", [])
    if zone_payload is None:
        zone_payload = []
    if not isinstance(zone_payload, list):
        raise ValueError("Участки в manifest имеют неверную структуру")
    try:
        planting_zones = [PlantingZoneAssignment.model_validate(item) for item in zone_payload]
    except Exception as error:
        raise ValueError("Участки в manifest имеют неверную структуру") from error
    editable = source_is_exact and plan is not None and declared_editable is not False
    reason = None if editable else (
        "Manifest помечает пакет как доступный только для просмотра."
        if declared_editable is False
        else "Пакет не содержит полной семантики проекта; открыт только для просмотра."
    )
    return ParsedReleaseBundle(
        manifest=manifest,
        source_filename=source_filename,
        source_path=source_path,
        source_content=bytes(source_content),
        source_components=source_components,
        dxf_content=dxf_content,
        plan=plan,
        geometry=geometry,
        planting_zones=planting_zones,
        editable=editable,
        read_only_reason=reason,
    )


def release_identity(project: Project, request: ReleaseCreateRequest) -> str:
    if project.plan is None:
        raise ValueError("План ещё не создан")
    basis = {
        "project_id": project.id,
        "plan": project.plan.model_dump(mode="json"),
        "geometry_version": project.geometry_version,
        "mode": request.mode,
        "scene_horizon": request.scene_horizon,
        "regulatory_basis": request.regulatory_basis.model_dump(mode="json") if request.regulatory_basis is not None else None,
        "rules": RULE_SET_REVISION,
        "regulatory_registry": REGISTRY_REVISION,
        "regulatory_requirement_profile": LCT_REQUIREMENT_PROFILE,
        "applied_rule_ids": applied_record_ids([issue.rule_id for issue in project.plan.issues]),
        "catalog": SPECIES_CATALOG_REVISION,
    }
    digest = sha256(json.dumps(basis, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return f"release-{digest[:20]}"


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _forecast(object_: object, field: str, horizon: int) -> tuple[str, str]:
    values = getattr(object_, field)
    item = forecast_at(values, horizon)
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
        '<desc id="desc">Эскизный слой посадок. Подложка сохранена в CAD-чертёже выпуска.</desc>',
        '<g fill="none" stroke="#667085" stroke-width="0.12" vector-effect="non-scaling-stroke">',
    ]
    for object_ in objects:
        forecast = forecast_at(object_.canopy_forecast, horizon)
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
    source_components: dict[str, bytes] | None = None,
    *,
    geometry: GeometryEnginePort | None = None,
    cad: CadRelease | None = None,
) -> tuple[ReleasePackage, dict[str, bytes]]:
    assert project.plan is not None and project.source_file is not None
    stem = Path(project.source_file.name).stem or "green-atlas"
    schedule = planting_schedule(project, request.scene_horizon)
    scene_content = _json_bytes(scene.model_dump(mode="json"))
    dendroplan = dendroplan_svg(project, request.scene_horizon)
    entries = {
        f"{stem}-planting-schedule.csv": schedule,
        f"{stem}-scene-{request.scene_horizon}y.json": scene_content,
        f"{stem}-dendroplan.svg": dendroplan,
    }
    if cad is None:
        entries[f"{stem}-planting-plan.dxf"] = dxf_content
    else:
        for path, content in cad.files.items():
            if not _safe_archive_name(path):
                raise ValueError("Недопустимый путь CAD-результата")
            entries[f"cad/{path}"] = content
    source_components = source_components or {}
    provenance = project.source_file.prepared_provenance
    primary_path = ("autocad-snapshot.json" if cad is not None else
                    provenance.entry if provenance is not None else project.source_file.name)
    if primary_path in source_components:
        raise ValueError("Дополнительный DXF дублирует основной исходник")
    if source_components and (provenance is None or not provenance.drawings):
        raise ValueError(
            "Для комплекта исходных DXF отсутствует проверенное происхождение"
        )
    source_files = {primary_path: source_content, **source_components}
    if provenance is not None and provenance.drawings:
        expected = {item.path: item for item in provenance.drawings}
        if set(source_files) != set(expected) or any(
            len(source_files[path]) != item.source_bytes
            or sha256(source_files[path]).hexdigest() != item.source_sha256
            for path, item in expected.items()
        ):
            raise ValueError("Исходные DXF выпуска не совпадают с подготовленным комплектом")
    for path, content in source_files.items():
        archive_path = f"source/{path}"
        if not _safe_archive_name(archive_path):
            raise ValueError("Путь исходного DXF недопустим для пакета выпуска")
        entries[archive_path] = content
    entry_hashes = {name: sha256(content).hexdigest() for name, content in sorted(entries.items())}
    missing_species = sorted(item.id for item in project.plan.objects if not item.species_revision_id)
    hard_errors = sorted(issue.id for issue in project.plan.issues if issue.severity == "error")
    warnings = [
        "Выпуск является проектным материалом и не означает согласование или выдачу порубочного билета.",
        ("Сведения об основаниях ПП-616 и ПП-1160 внесены в выпуск; сервис не проверяет полный состав административных документов."
         if request.regulatory_basis is not None else
         "Основания ПП-616 и ПП-1160 в этом черновике не указаны."),
        "Почва, влажность, инсоляция, рельеф и высоты зданий не подтверждены исходным чертежом.",
    ]
    if missing_species:
        warnings.append(f"Вид не назначен для {len(missing_species)} посадок.")
    if isinstance(geometry, UnavailableReleaseEvidence):
        warnings.append(UNAVAILABLE_CHECKS)
    if project.source_review is not None:
        warnings.append("Посадки не проверены по ограничениям исходного комплекта: требуется расчёт после уточнения данных.")
    if hard_errors:
        warnings.append(f"В плане осталось ошибок: {len(hard_errors)}.")
    # Exact sources are regular hashed ZIP entries. Do not duplicate a large
    # DXF as base64 inside the manifest: that inflates memory and archive size
    # while adding no evidence beyond the independently verified entry hash.
    plan_payload = project.plan.model_dump(mode="json")
    geometry_payload = project.geometry.model_dump(mode="json") if project.geometry is not None else None
    planting_zone_payload = [zone.model_dump(mode="json") for zone in project.planting_zones]
    groups = sorted({group_id for item in project.plan.objects for group_id in item.group_ids})
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
    manifest = {
        # Version 3 must never be sent to the legacy DXF bundle importer.
        "schema": "green-atlas-release:3" if cad is not None else "green-atlas-release:2",
        "editable": cad is None,
        "release_id": release_id,
        "mode": request.mode,
        "status": "draft" if request.mode == "draft" else "ready",
        "project": {
            "source_review": project.source_review.model_dump(mode="json") if project.source_review is not None else None,
            "id": project.id,
            "name": project.name,
            "state_version": project.state_version,
            "plan_version": project.plan.version,
            "geometry_version": project.geometry_version,
            "status": project.status.value,
            "coordinate_reference": project.coordinate_reference.model_dump(mode="json"),
            "planting_zones": planting_zone_payload,
        },
        "source": {
            "prepared_provenance": project.source_file.prepared_provenance.model_dump(mode="json") if project.source_file.prepared_provenance is not None else None,
            "accept_partial_geometry": project.source_file.accept_partial_geometry,
            "filename": project.source_file.name,
            "path": f"source/{primary_path}",
            "size": len(source_content),
            "sha256": sha256(source_content).hexdigest(),
            "embedded": True,
            "dxf_version": project.source_file.dxf_version,
            "units": project.source_file.units,
            "units_assumed": project.source_file.units_assumed,
            "drawings": [
                {
                    "path": path,
                    "size": len(content),
                    "sha256": sha256(content).hexdigest(),
                    "archive_path": f"source/{path}",
                }
                for path, content in sorted(source_files.items())
            ],
        },
        "layer_mappings": [
            {
                "layer_id": layer.id,
                "source_name": layer.source_name,
                "kind": layer.mapped_kind,
                "suggested_kind": layer.suggested_kind,
                "suggestion_confidence": layer.suggestion_confidence,
                "suggestion_reasons": layer.suggestion_reasons,
                "mapping_review_required": layer.mapping_review_required,
                "mapping_confirmed": bool(
                    layer.mapping_confirmed
                    or not layer.mapping_review_required
                ),
                "parsing_status": "complete" if layer.geometry_complete else "partial",
                "semantic_status": "excluded" if str(layer.mapped_kind) == "LayerKind.IGNORE" or getattr(layer.mapped_kind, "value", layer.mapped_kind) == "ignore" else "classified",
                "used_in_calculation": bool(project.source_review is None and layer.geometry_complete and getattr(layer.mapped_kind, "value", layer.mapped_kind) not in {None, "ignore", "unclassified"}),
                "utility_context": layer.utility_context.model_dump(mode="json") if layer.utility_context is not None else None,
                "utility_axis_bindings": [binding.model_dump(mode="json") for binding in layer.utility_axis_bindings],
            }
            for layer in sorted(project.layers, key=lambda item: item.id)
        ],
        "objects": sorted(item.id for item in project.plan.objects),
        "object_count": len(project.plan.objects),
        "plan": plan_payload,
        "planting_zones": planting_zone_payload,
        "groups": groups,
        "geometry": geometry_payload,
        "views": {
            "scene_horizon": request.scene_horizon,
            "dendroplan": "svg",
        },
        "missing_species_object_ids": missing_species,
        "hard_error_ids": hard_errors,
        "rule_set_revision": RULE_SET_REVISION,
        "regulatory_registry": registry_snapshot([*traced_rule_ids, *[issue.rule_id for issue in project.plan.issues]]),
        "regulatory_requirement_profile": requirement_profile().model_dump(mode="json"),
        "planting_rule_traces": {object_id: trace.model_dump(mode="json") for object_id, trace in traces.items()},
        "species_catalog_revision": SPECIES_CATALOG_REVISION,
        "regulatory_scope": {
            "spatial_draft": "implemented",
            "pp_743": "catalogue context only; specialist review required",
            "pp_616": "release decision gate implemented; full compensation procedure remains specialist-reviewed",
            "pp_1160": "release decision gate implemented; permit service outcome remains external",
        },
        "regulatory_basis": request.regulatory_basis.model_dump(mode="json") if request.regulatory_basis is not None else None,
        "data_passport": build_data_passport(project, geometry=geometry).model_dump(mode="json"),
        "data_gaps": scene.data_gaps,
        "limitations": warnings,
        "files": entry_hashes,
    }
    if cad is not None:
        manifest["cad"] = {
            "entry": f"cad/{cad.entry}",
            "snapshot_sha256": sha256(source_content).hexdigest(),
            "reopen_receipt": cad.receipt.model_dump(mode="json", by_alias=True),
        }
        manifest["source"]["format"] = "autocad_snapshot"
    manifest_content = _json_bytes(manifest)
    entries[f"{stem}-manifest.json"] = manifest_content
    bundle = _zip(entries)
    files = {
        "schedule": (f"{stem}-planting-schedule.csv", "text/csv; charset=utf-8", schedule),
        "manifest": (f"{stem}-manifest.json", "application/json", manifest_content),
        "scene": (f"{stem}-scene-{request.scene_horizon}y.json", "application/json", scene_content),
        "dendroplan": (f"{stem}-dendroplan.svg", "image/svg+xml", dendroplan),
        "bundle": (f"{stem}-release.zip", "application/zip", bundle),
    }
    if cad is None:
        files["dxf"] = (f"{stem}-planting-plan.dxf", "application/dxf", dxf_content)
    else:
        files["cad"] = (f"{stem}-cad.zip", "application/zip", _zip(cad.files))
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
