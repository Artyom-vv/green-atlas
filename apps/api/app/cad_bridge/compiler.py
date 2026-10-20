"""Допуск и нормализация native-снимков AutoCAD с проверкой версий, происхождения и
полноты перечня объектов.
"""

from __future__ import annotations

import hashlib
import io
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Literal, NamedTuple, NotRequired, TypedDict, cast

import ijson
from pydantic import TypeAdapter, ValidationError

from .config import (
    BLOCKING_DIAGNOSTICS,
    PRODUCER_CAPABILITIES,
    PROPOSAL_GAP_EPSILON_UNITS,
    REGION_PROBE_SCHEMA,
    SNAPSHOT_SCHEMA,
    TRAVERSAL_DIAGNOSTICS,
    XREF_DIAGNOSTICS,
    ProbeCapability,
)
from .contracts import (
    CadGeometry,
    CadSnapshot,
    CadSnapshotAdmissionError,
    CoverageRecord,
    NativeAreaProposal,
    PathGeometry,
)
from .face_inputs import FaceInputPublication, stage_face_inputs
from .geometry_admission import (
    QuarantinedPath,
    admit_native_path,
    admit_native_point,
    admit_native_region,
)
from .geometry_admission import is_finite_number as _is_finite_number
from .geometry_admission import native_geometry_id as _geometry_id
from .geometry_admission import native_identity as _identity
from .geometry_admission import native_identity_key as _identity_key
from .integrity import UNSIGNED_CONTENT_SHA256 as _UNSIGNED_CONTENT_SHA256
from .integrity import canonical_record_sha256 as _canonical_sha256
from .integrity import snapshot_payload_sha256
from .probe_contracts import (
    NativeXrefFileClaim,
    NativeXrefMetadata,
    NativeXrefPayload,
    NativeXrefStatus,
    XrefDiagnostic,
)

_NativeRecord = dict[str, Any]
_NormalizedGeometry = _NativeRecord | PathGeometry
_IdentityKey = tuple[str, tuple[str, ...]]
_PROBE_RECORD_ADAPTERS: tuple[
    tuple[Literal["coverage", "geometry", "area_proposals"], TypeAdapter[Any]], ...
] = (
    ("coverage", TypeAdapter(CoverageRecord)),
    ("geometry", TypeAdapter(CadGeometry)),
    ("area_proposals", TypeAdapter(NativeAreaProposal)),
)


class _NormalizedProbePayload(TypedDict):
    schema: str
    source: dict[str, Any]
    extraction: dict[str, Any]
    coverage: list[_NativeRecord]
    geometry: list[_NormalizedGeometry]
    summary: NotRequired[dict[str, Any]]
    area_proposals: NotRequired[list[_NativeRecord]]
    dependencies: NotRequired[list[NativeXrefPayload]]
    live_references: NotRequired[list[NativeXrefPayload]]


class _AdmittedNativeRecords(NamedTuple):
    coverage: list[_NativeRecord]
    regions: list[_NativeRecord]
    paths: list[_NativeRecord]
    points: list[_NativeRecord]
    area_proposals: list[_NativeRecord]


class _NativeGeometryPolicy(NamedTuple):
    plugin_version: str
    metres_per_unit: float
    requested_tolerance_m: float
    capabilities: frozenset[ProbeCapability]


class _XrefAdmission(NamedTuple):
    references: list[NativeXrefPayload] | None
    resolved_ids: set[str]
    unresolved_ids: set[str]


def compile_region_probe(
    probe: dict[str, Any],
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    package_root: Path | None = None,
) -> CadSnapshot:
    payload, maximum_achieved_tolerance_m = _normalize_probe_payload(
        probe,
        autocad_version=autocad_version,
        target=target,
        package_root=package_root,
    )
    return _admit_probe_payload(payload, maximum_achieved_tolerance_m)


def compile_live_document(
    content: bytes | bytearray,
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
) -> CadSnapshot:
    """Источником служит неизменяемый native-захват, а не прежний DXF на диске."""
    probe = json.loads(content)
    if not isinstance(probe, dict):
        raise CadSnapshotAdmissionError("native capture must be an object")
    payload, maximum_achieved_tolerance_m = _normalize_probe_payload(
        probe,
        autocad_version=autocad_version,
        target=target,
        live_source_sha256=hashlib.sha256(content).hexdigest(),
    )
    # При допуске нормализованной формы Pydantic исходный native-граф уже не нужен.
    del probe
    return _admit_probe_payload(payload, maximum_achieved_tolerance_m)


def compile_live_document_file(
    path: Path,
    *,
    expected_source_sha256: str,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    face_inputs: FaceInputPublication | None = None,
) -> tuple[CadSnapshot, int]:
    """Допускать тот же native-граф без удержания исходного закодированного файла."""
    if face_inputs is not None:
        face_inputs.sha256 = None
    probe, source_size = (
        _read_live_probe(path, expected_source_sha256, face_inputs=True)
        if face_inputs is not None
        else _read_live_probe(path, expected_source_sha256)
    )
    staged = (
        stage_face_inputs(face_inputs.path, probe, expected_source_sha256)
        if face_inputs is not None
        and isinstance(probe, _FaceInputProbe)
        and probe.face_input_compatible
        else None
    )
    try:
        payload, maximum_achieved_tolerance_m = _normalize_probe_payload(
            probe,
            autocad_version=autocad_version,
            target=target,
            live_source_sha256=expected_source_sha256,
            consume_owned=True,
        )
        del probe
        snapshot = _admit_probe_payload(payload, maximum_achieved_tolerance_m)
        if staged is not None and face_inputs is not None:
            face_inputs.sha256 = staged.publish()
        return snapshot, source_size
    finally:
        if staged is not None:
            staged.discard()


def _compile_xref_dependencies(
    probe: dict[str, Any],
    package_root: Path | None,
    *,
    live: bool,
    capabilities: frozenset[ProbeCapability],
) -> _XrefAdmission:
    """Допускать реестр XREF без повторного чтения файлов live-захвата.

    Форма, уникальность и заявленные байты проверяются в прежнем порядке;
    принадлежность ссылок геометрии проверяется при сборке покрытия снимка.
    """
    summary = probe.get("summary") or {}
    xref_references = summary.get("xref_block_references")
    if not isinstance(xref_references, int) or xref_references < 0:
        raise CadSnapshotAdmissionError(XREF_DIAGNOSTICS[XrefDiagnostic.COUNT_MISSING])
    raw_dependencies = probe.get("xref_dependencies")
    if xref_references == 0:
        if raw_dependencies not in (None, []):
            raise CadSnapshotAdmissionError(
                XREF_DIAGNOSTICS[XrefDiagnostic.LEDGER_WITHOUT_REFERENCES]
            )
        return _XrefAdmission(None, set(), set())
    if ProbeCapability.XREF_DEPENDENCIES not in capabilities:
        raise CadSnapshotAdmissionError(
            XREF_DIAGNOSTICS[XrefDiagnostic.PRODUCER_UNQUALIFIED]
        )
    if not isinstance(raw_dependencies, list) or not raw_dependencies:
        raise CadSnapshotAdmissionError(XREF_DIAGNOSTICS[XrefDiagnostic.LEDGER_MISSING])
    if summary.get("xref_dependency_records") != len(raw_dependencies):
        raise CadSnapshotAdmissionError(XREF_DIAGNOSTICS[XrefDiagnostic.COUNT_MISMATCH])

    dependencies: list[NativeXrefPayload] = []
    dependency_ids: set[str] = set()
    unresolved_dependency_ids: set[str] = set()
    record_handles: set[str] = set()
    canonical_root: Path | None = None
    for raw in raw_dependencies:
        metadata = _admit_xref_metadata(raw)
        dependency_id = metadata.dependency_id
        if metadata.record_handle in record_handles:
            raise CadSnapshotAdmissionError(
                XREF_DIAGNOSTICS[XrefDiagnostic.RECORD_DUPLICATE]
            )
        record_handles.add(metadata.record_handle)
        if metadata.status == NativeXrefStatus.UNRESOLVED:
            unresolved_dependency_ids.add(dependency_id)
            continue

        reference: NativeXrefPayload = {
            "id": dependency_id,
            "record_handle": metadata.record_handle,
            "block_name": metadata.block_name,
            "stored_path": metadata.stored_path,
        }
        if not live:
            claim = _admit_xref_file_claim(raw)
            if canonical_root is None:
                canonical_root = _canonical_package_root(package_root)
            reference = _verify_xref_file(reference, claim, canonical_root)
        dependency_ids.add(dependency_id)
        dependencies.append(reference)
    return _XrefAdmission(
        dependencies or None, dependency_ids, unresolved_dependency_ids
    )


def _admit_xref_metadata(raw: object) -> NativeXrefMetadata:
    if not isinstance(raw, dict):
        raise CadSnapshotAdmissionError(XREF_DIAGNOSTICS[XrefDiagnostic.RECORD_INVALID])
    try:
        return NativeXrefMetadata.model_validate(raw)
    except ValidationError as error:
        raise CadSnapshotAdmissionError(
            XREF_DIAGNOSTICS[XrefDiagnostic.METADATA_INCOMPLETE]
        ) from error


def _admit_xref_file_claim(raw: object) -> NativeXrefFileClaim:
    try:
        return NativeXrefFileClaim.model_validate(raw)
    except ValidationError as error:
        raise CadSnapshotAdmissionError(
            XREF_DIAGNOSTICS[XrefDiagnostic.METADATA_INCOMPLETE]
        ) from error


def _canonical_package_root(package_root: Path | None) -> Path:
    if package_root is None:
        raise CadSnapshotAdmissionError(XREF_DIAGNOSTICS[XrefDiagnostic.ROOT_REQUIRED])
    try:
        return package_root.resolve(strict=True)
    except OSError as error:
        raise CadSnapshotAdmissionError(
            XREF_DIAGNOSTICS[XrefDiagnostic.ROOT_UNAVAILABLE]
        ) from error


def _verify_xref_file(
    reference: NativeXrefPayload, claim: NativeXrefFileClaim, canonical_root: Path
) -> NativeXrefPayload:
    """Добавлять файловое свидетельство только после границы каталога и точного SHA."""
    candidate = Path(claim.resolved_path)
    if candidate.is_symlink():
        raise CadSnapshotAdmissionError(XREF_DIAGNOSTICS[XrefDiagnostic.SYMLINK])
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CadSnapshotAdmissionError(
            XREF_DIAGNOSTICS[XrefDiagnostic.FILE_UNAVAILABLE]
        ) from error
    if not resolved.is_file() or not resolved.is_relative_to(canonical_root):
        raise CadSnapshotAdmissionError(
            XREF_DIAGNOSTICS[XrefDiagnostic.OUTSIDE_PACKAGE]
        )
    actual_bytes = resolved.stat().st_size
    with resolved.open("rb") as stream:
        actual_sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual_bytes != claim.bytes or actual_sha256 != claim.sha256:
        raise CadSnapshotAdmissionError(XREF_DIAGNOSTICS[XrefDiagnostic.FILE_MISMATCH])
    return {
        **reference,
        "kind": "xref",
        "path": resolved.relative_to(canonical_root).as_posix(),
        "sha256": actual_sha256,
        "bytes": actual_bytes,
    }


class _HashedCaptureReader(io.BufferedReader):
    """Хешировать ровно те ограниченные порции, которые читает JSON-парсер."""

    def __init__(self, stream: io.RawIOBase) -> None:
        super().__init__(stream)
        self.digest = hashlib.sha256()
        self.bytes_read = 0
        self.first_byte: int | None = None

    def _observe(self, content: bytes | memoryview) -> None:
        if self.first_byte is None:
            for value in content:
                if value not in (32, 9, 10, 13):
                    self.first_byte = value
                    break

    def read(self, size: int | None = -1) -> bytes:
        if size is None or size < 0:
            raise ValueError("native capture reads must be bounded")
        content = super().read(size)
        self._observe(content)
        self.digest.update(content)
        self.bytes_read += len(content)
        return content

    def readinto(self, buffer: Any) -> int:
        count = super().readinto(buffer)
        self._observe(memoryview(buffer)[:count])
        self.digest.update(memoryview(buffer)[:count])
        self.bytes_read += count
        return count


class _FaceInputProbe(dict[str, Any]):
    # Только корневой словарь: вложенные записи остаются обычными dict.
    face_input_compatible = True


def _read_live_probe(
    path: Path, expected_source_sha256: str, *, face_inputs: bool = False
) -> tuple[dict[str, Any], int]:
    with io.FileIO(path, "r") as stream:
        reader = _HashedCaptureReader(stream)
        try:
            if face_inputs:
                probe = _FaceInputProbe()
                # C-парсер собирает те же значения, но сохраняет признаки неоднозначного
                # корня. Для него остаются прежние prefix-сканы перед Core.
                for name, value in ijson.kvitems(reader, "", use_float=True):
                    if name in probe or not name or "." in name:
                        probe.face_input_compatible = False
                    probe[name] = value
                if reader.first_byte != ord("{"):
                    raise CadSnapshotAdmissionError("native capture must be an object")
            else:
                items = ijson.items(reader, "", use_float=True)
                probe = next(items)
                # До доверия хешу дочитываем парсер, включая завершающие байты.
                if next(items, None) is not None:
                    raise CadSnapshotAdmissionError("native capture must be one object")
        except (StopIteration, ijson.JSONError) as error:
            raise CadSnapshotAdmissionError("invalid native capture JSON") from error
        if not isinstance(probe, dict):
            raise CadSnapshotAdmissionError("native capture must be an object")
        if reader.digest.hexdigest() != expected_source_sha256:
            raise CadSnapshotAdmissionError("native capture changed before import")
        return probe, reader.bytes_read


def _admit_probe_metadata(
    probe: _NativeRecord, *, live: bool
) -> tuple[_NativeRecord, _NativeRecord, _NativeGeometryPolicy]:
    """Последовательно допускать производителя, исходник, единицы и свидетельства
    обхода.
    """
    if probe.get("schema") != REGION_PROBE_SCHEMA:
        raise CadSnapshotAdmissionError("unsupported native probe schema")
    if probe.get("plugin_version") not in PRODUCER_CAPABILITIES:
        raise CadSnapshotAdmissionError("unsupported native probe plugin version")
    expected_mode = "live_document" if live else "side_database_dxf"
    if probe.get("capture_mode") != expected_mode:
        label = "live_document" if live else "side-database"
        raise CadSnapshotAdmissionError(f"only {label} capture can be admitted")
    source = probe.get("source") or {}
    if not live and source.get("database_modified_flags") != 0:
        raise CadSnapshotAdmissionError("native database reports unsaved modifications")
    if not live and source.get("live_database_matches_disk") is not True:
        raise CadSnapshotAdmissionError(
            "native database is not proven equal to source DXF"
        )
    metres_per_unit = source.get("metres_per_unit")
    if not _is_finite_number(metres_per_unit):
        raise CadSnapshotAdmissionError("source unit conversion is missing")
    if metres_per_unit <= 0:
        raise CadSnapshotAdmissionError("source unit conversion must be positive")
    requested_tolerance_m = probe.get("requested_tolerance_m")
    if not _is_finite_number(requested_tolerance_m) or requested_tolerance_m <= 0:
        raise CadSnapshotAdmissionError("requested tolerance is invalid")

    summary = probe.get("summary") or {}
    missing_diagnostics = [
        field for field in TRAVERSAL_DIAGNOSTICS if field not in summary
    ]
    if missing_diagnostics:
        raise CadSnapshotAdmissionError(
            f"native summary misses traversal diagnostics: {missing_diagnostics}"
        )
    blockers = {
        field: summary.get(field)
        for field in BLOCKING_DIAGNOSTICS
        if summary.get(field) != 0
    }
    if blockers:
        raise CadSnapshotAdmissionError(f"native traversal is incomplete: {blockers}")

    return (
        source,
        summary,
        _NativeGeometryPolicy(
            probe["plugin_version"],
            metres_per_unit,
            requested_tolerance_m,
            PRODUCER_CAPABILITIES[probe["plugin_version"]],
        ),
    )


def _admit_geometry_records(
    probe: _NativeRecord,
    summary: _NativeRecord,
    capabilities: frozenset[ProbeCapability],
) -> _AdmittedNativeRecords:
    """Проверять счётчики и native-покрытие записей до нормализации геометрии."""
    raw_coverage = probe.get("coverage")
    raw_regions = probe.get("regions")
    raw_paths = probe.get("paths", [])
    raw_points = probe.get("points", [])
    raw_area_proposals = probe.get("area_proposals", [])
    raw_area_proposal_rejections = probe.get("area_proposal_rejections", [])
    if (
        not isinstance(raw_coverage, list)
        or not isinstance(raw_regions, list)
        or not isinstance(raw_paths, list)
        or not isinstance(raw_points, list)
        or not isinstance(raw_area_proposals, list)
        or not isinstance(raw_area_proposal_rejections, list)
    ):
        raise CadSnapshotAdmissionError(
            "coverage, regions, paths and points must be arrays"
        )
    coverage_by_key = {_identity_key(record): record for record in raw_coverage}
    if len(coverage_by_key) != len(raw_coverage):
        raise CadSnapshotAdmissionError("duplicate source instance in native coverage")
    if summary.get("source_instances") != len(raw_coverage):
        raise CadSnapshotAdmissionError(
            "native source instance count differs from coverage"
        )

    raw_regions_by_key = {_identity_key(region): region for region in raw_regions}
    if len(raw_regions_by_key) != len(raw_regions):
        raise CadSnapshotAdmissionError("duplicate REGION instance in native geometry")
    if ProbeCapability.DERIVED_REGIONS not in capabilities and any(
        region.get("source_handles") for region in raw_regions
    ):
        raise CadSnapshotAdmissionError(
            "derived AutoCAD regions require native producer 0.1.34 or later"
        )
    # Ошибка native-извлечения остаётся записью о пропуске. Принимаем исправные соседние
    # объекты, не переименовывая ошибочные данные в native-геометрию.
    failed_regions = [
        region for region in raw_regions if region.get("status") == "unresolved"
    ]
    for region in failed_regions:
        record = coverage_by_key.get(_identity_key(region))
        if (
            not record
            or record.get("status") != "unresolved"
            or not record.get("reason")
        ):
            raise CadSnapshotAdmissionError(
                "failed REGION lacks explicit unresolved coverage"
            )
    if summary.get("regions") != len(raw_regions):
        raise CadSnapshotAdmissionError("native REGION count differs from geometry")
    if raw_area_proposals and ProbeCapability.AREA_PROPOSALS not in capabilities:
        raise CadSnapshotAdmissionError("native area proposals require producer 0.1.36")
    if ProbeCapability.AREA_PROPOSALS in capabilities:
        if summary.get("area_proposals") != len(raw_area_proposals):
            raise CadSnapshotAdmissionError(
                "native area proposal count differs from geometry"
            )
        if summary.get("area_proposal_candidates") != len(raw_area_proposals) + len(
            raw_area_proposal_rejections
        ) or summary.get("area_proposal_rejected") != len(raw_area_proposal_rejections):
            raise CadSnapshotAdmissionError(
                "native area proposal outcomes differ from candidates"
            )
    elif raw_area_proposal_rejections:
        raise CadSnapshotAdmissionError(
            "native area proposal diagnostics require producer 0.1.36"
        )
    if summary.get("resolved") != len(raw_regions) - len(failed_regions) or summary.get(
        "unresolved"
    ) != len(failed_regions):
        raise CadSnapshotAdmissionError(
            "native REGION outcome counts differ from geometry"
        )
    del failed_regions
    raw_regions = [
        region for region in raw_regions if region.get("status") != "unresolved"
    ]
    authored_regions = [
        region for region in raw_regions if not region.get("source_handles")
    ]
    raw_regions_by_key = {_identity_key(region): region for region in authored_regions}
    raw_paths_by_key = {_identity_key(path): path for path in raw_paths}
    raw_points_by_key = {_identity_key(point): point for point in raw_points}
    if len(raw_paths_by_key) != len(raw_paths):
        raise CadSnapshotAdmissionError("duplicate path instance in native geometry")
    if len(raw_points_by_key) != len(raw_points):
        raise CadSnapshotAdmissionError("duplicate point instance in native geometry")
    geometry_keys = (
        set(raw_regions_by_key) | set(raw_paths_by_key) | set(raw_points_by_key)
    )
    if len(geometry_keys) != len(raw_regions_by_key) + len(raw_paths_by_key) + len(
        raw_points_by_key
    ):
        raise CadSnapshotAdmissionError(
            "one source instance emitted multiple native geometry records"
        )
    native_coverage_keys = {
        _identity_key(record)
        for record in raw_coverage
        if record.get("status") == "native"
    }
    if native_coverage_keys != geometry_keys:
        raise CadSnapshotAdmissionError(
            "native coverage does not match emitted AutoCAD geometry"
        )
    del raw_regions_by_key, raw_points_by_key, geometry_keys, native_coverage_keys
    if ProbeCapability.PATHS_AND_POINTS in capabilities:
        if summary.get("paths") != len(raw_paths):
            raise CadSnapshotAdmissionError("native path count differs from geometry")
        if summary.get("points") != len(raw_points):
            raise CadSnapshotAdmissionError("native point count differs from geometry")

    return _AdmittedNativeRecords(
        raw_coverage, raw_regions, raw_paths, raw_points, raw_area_proposals
    )


def _admit_region_sources(
    region: _NativeRecord,
    *,
    is_proposal: bool,
    paths_by_key: dict[_IdentityKey, _NativeRecord],
    coverage_by_key: dict[_IdentityKey, _NativeRecord],
    tolerance_units: float,
) -> list[str]:
    """Связывать предложения и производные области с путями того же native-исходника."""
    source_handles = region.get("source_handles") or []
    if is_proposal:
        if source_handles or region.get("proposal_method") != "explicit-chord-closure":
            raise CadSnapshotAdmissionError("area proposal has invalid native method")
        key = _identity_key(region)
        path = paths_by_key.get(key)
        record = coverage_by_key.get(key)
        gap = region.get("closure_gap_wcs_xy_units")
        if (
            path is None
            or record is None
            or record.get("status") != "native"
            or path.get("closed") is not False
            or path.get("layer") != region.get("layer")
            or record.get("layer") != region.get("layer")
            or not _is_finite_number(gap)
            or gap <= 0
        ):
            raise CadSnapshotAdmissionError(
                "area proposal lacks an open same-layer native path"
            )
        coordinates = path.get("coordinates")
        if not isinstance(coordinates, list) or len(coordinates) < 2:
            raise CadSnapshotAdmissionError("area proposal source path is invalid")
        actual_gap = math.dist(coordinates[0][:2], coordinates[-1][:2])
        if abs(actual_gap - gap) > max(PROPOSAL_GAP_EPSILON_UNITS, tolerance_units):
            raise CadSnapshotAdmissionError(
                "area proposal gap disagrees with native source path"
            )
    if source_handles:
        if (
            not isinstance(source_handles, list)
            or len(source_handles) != 2
            or source_handles != sorted(set(source_handles))
            or region.get("handle") != source_handles[0]
        ):
            raise CadSnapshotAdmissionError("derived REGION has invalid source handles")
        for handle in source_handles:
            member_key = (handle, tuple(region.get("instance_chain", [])))
            member_path = paths_by_key.get(member_key)
            member_coverage = coverage_by_key.get(member_key)
            if (
                member_path is None
                or member_coverage is None
                or member_coverage.get("status") != "native"
                or member_path.get("layer") != region.get("layer")
                or member_coverage.get("layer") != region.get("layer")
            ):
                raise CadSnapshotAdmissionError(
                    "derived REGION member lacks same-layer native path"
                )
    return source_handles


def _normalize_regions(
    records: _AdmittedNativeRecords,
    policy: _NativeGeometryPolicy,
) -> tuple[list[_NormalizedGeometry], list[tuple[_NativeRecord, _NativeRecord]], float]:
    geometry: list[_NormalizedGeometry] = []
    proposal_previews: list[tuple[_NativeRecord, _NativeRecord]] = []
    maximum_tolerance_m = 0.0
    paths_by_key = {_identity_key(path): path for path in records.paths}
    coverage_by_key = {_identity_key(record): record for record in records.coverage}
    for region, is_proposal in [
        *((item, False) for item in records.regions),
        *((item, True) for item in records.area_proposals),
    ]:
        source_handles = _admit_region_sources(
            region,
            is_proposal=is_proposal,
            paths_by_key=paths_by_key,
            coverage_by_key=coverage_by_key,
            tolerance_units=policy.requested_tolerance_m / policy.metres_per_unit,
        )
        normalized, achieved = admit_native_region(
            region,
            source_handles,
            is_proposal=is_proposal,
            allow_multiple_outer=ProbeCapability.MULTIPLE_OUTER_LOOPS
            in policy.capabilities,
            metres_per_unit=policy.metres_per_unit,
            requested_tolerance_m=policy.requested_tolerance_m,
        )
        maximum_tolerance_m = max(maximum_tolerance_m, achieved)
        if is_proposal:
            proposal_previews.append((region, normalized))
        else:
            geometry.append(normalized)
    # Межобъектные индексы освобождаются до роста нормализованного графа контуров.
    return geometry, proposal_previews, maximum_tolerance_m


def _normalize_paths(
    raw_paths: list[_NativeRecord],
    geometry: list[_NormalizedGeometry],
    policy: _NativeGeometryPolicy,
    maximum_achieved_tolerance_m: float,
    *,
    consume_owned: bool = False,
) -> tuple[list[_NativeRecord], dict[_IdentityKey, str], float]:
    admitted_paths: list[dict[str, Any]] = []
    quarantined_path_reasons: dict[tuple[str, tuple[str, ...]], str] = {}
    owned_paths = cast(list[_NativeRecord | None], raw_paths)
    for index, path in enumerate(raw_paths):
        geometry_id = _geometry_id(path, "path")
        identity_key = _identity_key(path)
        admitted = admit_native_path(
            path,
            geometry_id=geometry_id,
            identity=_identity(path),
            metres_per_unit=policy.metres_per_unit,
            requested_tolerance_m=policy.requested_tolerance_m,
        )
        if consume_owned:
            # Связи областей уже проверены; исходные координаты больше не нужны.
            owned_paths[index] = None
        if isinstance(admitted, QuarantinedPath):
            quarantined_path_reasons[identity_key] = admitted.reason
            continue
        maximum_achieved_tolerance_m = max(
            maximum_achieved_tolerance_m, admitted.achieved_tolerance_m
        )
        # Допуск уже создал независимые типизированные координаты. Сохраняем их
        # до общей проверки снимка, не копируя через промежуточный JSON-словарь.
        geometry.append(admitted)
        admitted_paths.append(_identity(path) if consume_owned else path)

    if consume_owned:
        raw_paths.clear()

    return admitted_paths, quarantined_path_reasons, maximum_achieved_tolerance_m


def _normalize_area_proposals(
    proposal_previews: list[tuple[_NativeRecord, _NativeRecord]],
    admitted_paths: list[_NativeRecord],
    geometry: list[_NormalizedGeometry],
) -> list[_NativeRecord]:
    if not proposal_previews:
        return []
    paths_by_id = {item.id: item for item in geometry if isinstance(item, PathGeometry)}
    path_geometry_by_key = {
        _identity_key(item): paths_by_id[_geometry_id(item, "path")]
        for item in admitted_paths
    }
    area_proposals: list[dict[str, Any]] = []
    for raw, preview in proposal_previews:
        source_path = path_geometry_by_key.get(_identity_key(raw))
        if source_path is None:
            raise CadSnapshotAdmissionError("area proposal source path was quarantined")
        proposal = {
            "id": _geometry_id(raw, "area-proposal"),
            "source": _identity(raw),
            "source_path_content_sha256": source_path.content_sha256,
            "layer": raw["layer"],
            "method": raw["proposal_method"],
            "closure_gap_wcs_xy_units": raw["closure_gap_wcs_xy_units"],
            "preview": preview,
        }
        proposal["content_sha256"] = _canonical_sha256(proposal)
        area_proposals.append(proposal)

    return area_proposals


def _normalize_coverage(
    probe: _NativeRecord,
    summary: _NativeRecord,
    geometry_ids_by_key: dict[_IdentityKey, str],
    derived_ids_by_key: dict[_IdentityKey, str],
    quarantined_path_reasons: dict[_IdentityKey, str],
    dependency_ids: set[str],
    unresolved_dependency_ids: set[str],
    *,
    consume_owned: bool = False,
) -> list[_NativeRecord]:
    raw_coverage = probe["coverage"]
    coverage: list[dict[str, Any]] = []
    # Повторные метаданные остаются в моделях и проекции. Пул принадлежит только
    # этому вызову; адреса объектов и свободные пояснения не объединяем.
    metadata_values: dict[str, str] = {}

    def shared_metadata(value: Any) -> Any:
        if type(value) is not str or len(value) > 512:
            return value
        previous = metadata_values.get(value)
        if previous is not None:
            return previous
        if len(metadata_values) < 4096:
            metadata_values[value] = value
        return value

    referenced_dependency_ids: set[str] = set()
    xref_coverage_count = 0
    owned_coverage = cast(list[_NativeRecord | None], raw_coverage)
    for index, record in enumerate(raw_coverage):
        status = record.get("status")
        key = _identity_key(record)
        quarantined_reason = quarantined_path_reasons.get(key)
        method = record["method"]
        reason = record.get("reason")
        if quarantined_reason is not None:
            status = "unresolved"
            method = "autocad-wcs-xy-quarantined"
            reason = quarantined_reason
        if status not in {"native", "converted", "context", "unresolved"}:
            raise CadSnapshotAdmissionError(f"unsupported coverage status: {status}")
        coverage_geometry_id = geometry_ids_by_key.get(key)
        dependency_id = record.get("xref_dependency_id")
        dependency_refs = None
        if dependency_id is not None:
            xref_coverage_count += 1
            if dependency_id in unresolved_dependency_ids:
                if status != "unresolved":
                    raise CadSnapshotAdmissionError(
                        "missing XREF coverage must remain unresolved"
                    )
            elif dependency_id not in dependency_ids:
                raise CadSnapshotAdmissionError(
                    "XREF coverage references an unknown dependency"
                )
            else:
                dependency_refs = [dependency_id]
                referenced_dependency_ids.add(dependency_id)
        geometry_ids = [coverage_geometry_id] if coverage_geometry_id else []
        derived_geometry_id = derived_ids_by_key.get(key)
        if derived_geometry_id is not None:
            geometry_ids.append(derived_geometry_id)
        coverage_record = {
            "identity": _identity(record),
            "entity_type": shared_metadata(record["entity_type"]),
            "layer": shared_metadata(record.get("layer", "")),
            "status": status,
            "method": shared_metadata(method),
            "reason": reason,
            "geometry_ids": geometry_ids,
        }
        if dependency_refs is not None:
            coverage_record["dependency_ids"] = dependency_refs
        if dependency_id in unresolved_dependency_ids:
            raw_dependency = next(
                item
                for item in probe["xref_dependencies"]
                if f"xref/{item['record_handle']}" == dependency_id
            )
            coverage_record["unresolved_reference"] = {
                "block_name": raw_dependency["block_name"],
                "stored_path": raw_dependency["stored_path"],
            }
        coverage.append(coverage_record)
        if consume_owned:
            # Полная нормализованная запись уже сохранена; освобождаем только
            # принадлежащий файловому входу словарь, не архив и не источник.
            owned_coverage[index] = None
    if consume_owned:
        raw_coverage.clear()
    if dependency_ids or unresolved_dependency_ids:
        if xref_coverage_count != summary.get("xref_block_references"):
            raise CadSnapshotAdmissionError(
                "native XREF reference count differs from dependency coverage"
            )
        if referenced_dependency_ids != dependency_ids:
            raise CadSnapshotAdmissionError(
                "native XREF dependency is not referenced by source coverage"
            )

    return coverage


def _normalize_probe_payload(
    probe: _NativeRecord,
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    package_root: Path | None = None,
    live_source_sha256: str | None = None,
    consume_owned: bool = False,
) -> tuple[_NormalizedProbePayload, float]:
    """Нормализовать допущенные свидетельства в установленном порядке записей и отказов.

    Полный учёт не означает расчётную геометрию каждой сущности: нерешённые записи
    остаются явными без передачи другому парсеру. Только файловый вход передаёт
    собственный JSON-граф для освобождения обработанных записей.
    """
    live = live_source_sha256 is not None
    source, summary, policy = _admit_probe_metadata(probe, live=live)
    dependencies, dependency_ids, unresolved_dependency_ids = (
        _compile_xref_dependencies(
            probe, package_root, live=live, capabilities=policy.capabilities
        )
    )
    records = _admit_geometry_records(probe, summary, policy.capabilities)
    geometry, proposal_previews, maximum_achieved_tolerance_m = _normalize_regions(
        records, policy
    )
    admitted_paths, quarantined_path_reasons, maximum_achieved_tolerance_m = (
        _normalize_paths(
            records.paths,
            geometry,
            policy,
            maximum_achieved_tolerance_m,
            consume_owned=consume_owned,
        )
    )
    area_proposals = _normalize_area_proposals(
        proposal_previews, admitted_paths, geometry
    )
    for point in records.points:
        geometry.append(admit_native_point(point))
    geometry_ids_by_key = {
        **{
            _identity_key(region): _geometry_id(region)
            for region in records.regions
            if not region.get("source_handles")
        },
        **{_identity_key(path): _geometry_id(path, "path") for path in admitted_paths},
        **{
            _identity_key(point): _geometry_id(point, "point")
            for point in records.points
        },
    }
    derived_ids_by_key = {
        _identity_key(region): _geometry_id(region, "derived-region")
        for region in records.regions
        if region.get("source_handles")
    }
    if consume_owned:
        # Идентификаторы и привязки предложений уже зафиксированы. У файлового
        # входа нет внешних владельцев этих записей, у публичного dict-входа есть.
        records.regions.clear()
        probe.pop("regions", None)
        if records.points:
            del point
        records.points.clear()
        records.area_proposals.clear()
        del proposal_previews, admitted_paths
    coverage = _normalize_coverage(
        probe,
        summary,
        geometry_ids_by_key,
        derived_ids_by_key,
        quarantined_path_reasons,
        dependency_ids,
        unresolved_dependency_ids,
        consume_owned=consume_owned,
    )
    counts = Counter(record["status"] for record in coverage)
    snapshot: _NormalizedProbePayload = {
        "schema": SNAPSHOT_SCHEMA,
        "source": {
            "sha256": live_source_sha256 or source["sha256"],
            "saved": True,
            "units_code": source["units_code"],
            "document_revision": source["document_revision"],
        },
        "extraction": {
            "autocad_version": autocad_version,
            "plugin_version": policy.plugin_version,
            "target": target,
            "projection": "wcs-xy-planar",
            "requested_tolerance_m": policy.requested_tolerance_m,
        },
        "coverage": coverage,
        "geometry": geometry,
    }
    if area_proposals:
        snapshot["area_proposals"] = area_proposals
    if live:
        snapshot["source"]["live_capture"] = {
            "mode": "live_document",
            "original_path": source["path"],
            "original_disk_sha256": source["sha256"],
            "database_modified_flags": source["database_modified_flags"],
        }
    if dependencies is not None:
        snapshot["live_references" if live else "dependencies"] = dependencies
    snapshot["summary"] = {
        "source_instances": len(coverage),
        "native": counts["native"],
        "converted": counts["converted"],
        "context": counts["context"],
        "unresolved": counts["unresolved"],
        "payload_sha256": _UNSIGNED_CONTENT_SHA256,
        "complete": True,
    }
    return snapshot, maximum_achieved_tolerance_m


def _admit_probe_payload(
    payload: _NormalizedProbePayload, maximum_achieved_tolerance_m: float
) -> CadSnapshot:
    """Проверять и хешировать нормализованный контракт до выхода из компилятора."""
    # Список принадлежит нормализации: заменяем записи по одной, чтобы не держать
    # одновременно весь словарный граф и его типизированную копию.
    for field, adapter in _PROBE_RECORD_ADAPTERS:
        records = cast(list[object], payload.get(field, []))
        for index in range(len(records)):
            try:
                records[index] = adapter.validate_python(records[index])
            except ValidationError:
                # Общая проверка сохраняет адрес поля, индекс и порядок ошибок.
                pass
    admitted = CadSnapshot.model_validate(payload)
    admitted.summary.payload_sha256 = snapshot_payload_sha256(admitted)
    if maximum_achieved_tolerance_m > admitted.extraction.requested_tolerance_m:
        raise CadSnapshotAdmissionError("compiled geometry exceeds requested tolerance")
    return admitted
