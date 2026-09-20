from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from .contracts import CadSnapshot, PathGeometry, PointGeometry, RegionGeometry

REGION_PROBE_SCHEMA = "green-atlas.autocad-region-topology-probe/1"
SNAPSHOT_SCHEMA = "green-atlas.autocad-snapshot/1"
SUPPORTED_PLUGIN_VERSIONS = {"0.1.4", "0.1.5", "0.1.6", "0.1.7", "0.1.8"}
XREF_DEPENDENCY_PLUGIN_VERSIONS = {"0.1.6", "0.1.7", "0.1.8"}
BLOCKING_DIAGNOSTICS = (
    "cyclic_block_references",
    "unloaded_xref_block_references",
    "unresolved_xref_block_references",
    "unexpanded_minsert_blocks",
    "unreadable_block_records",
    "unreadable_entities",
)


class CadSnapshotAdmissionError(ValueError):
    """The native evidence is incomplete or internally inconsistent."""


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _identity(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "handle": record["handle"],
        "instance_chain": list(record.get("instance_chain", [])),
    }


def _identity_key(record: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    identity = _identity(record)
    return identity["handle"], tuple(identity["instance_chain"])


def _geometry_id(record: dict[str, Any], kind: str = "region") -> str:
    identity = _identity(record)
    path = "/".join([*identity["instance_chain"], identity["handle"]])
    return f"{kind}/{path}"


def _signed_xy_area(coordinates: list[list[float]]) -> float:
    return 0.5 * sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(coordinates, coordinates[1:], strict=False)
    )


def _length_3d(coordinates: list[list[float]]) -> float:
    return sum(
        math.dist(left, right)
        for left, right in zip(coordinates, coordinates[1:], strict=False)
    )


def _compile_xref_dependencies(
    probe: dict[str, Any], package_root: Path | None
) -> tuple[list[dict[str, Any]] | None, set[str]]:
    summary = probe.get("summary") or {}
    xref_references = summary.get("xref_block_references")
    if not isinstance(xref_references, int) or xref_references < 0:
        raise CadSnapshotAdmissionError("native summary misses XREF reference count")
    raw_dependencies = probe.get("xref_dependencies")
    if xref_references == 0:
        if raw_dependencies not in (None, []):
            raise CadSnapshotAdmissionError(
                "native probe declares XREF dependencies without XREF references"
            )
        return None, set()
    if probe.get("plugin_version") not in XREF_DEPENDENCY_PLUGIN_VERSIONS:
        raise CadSnapshotAdmissionError(
            "native probe cannot prove the exact XREF dependency files"
        )
    if package_root is None:
        raise CadSnapshotAdmissionError(
            "package root is required to verify native XREF dependencies"
        )
    if not isinstance(raw_dependencies, list) or not raw_dependencies:
        raise CadSnapshotAdmissionError("native probe has no XREF dependency ledger")
    if summary.get("xref_dependency_records") != len(raw_dependencies):
        raise CadSnapshotAdmissionError(
            "native XREF dependency count differs from its ledger"
        )

    try:
        canonical_root = package_root.resolve(strict=True)
    except OSError as error:
        raise CadSnapshotAdmissionError("CAD package root is unavailable") from error
    dependencies: list[dict[str, Any]] = []
    dependency_ids: set[str] = set()
    record_handles: set[str] = set()
    for raw in raw_dependencies:
        if not isinstance(raw, dict) or raw.get("status") != "resolved":
            raise CadSnapshotAdmissionError("native XREF dependency is not resolved")
        record_handle = raw.get("record_handle")
        block_name = raw.get("block_name")
        stored_path = raw.get("stored_path")
        resolved_path = raw.get("resolved_path")
        claimed_sha256 = raw.get("sha256")
        claimed_bytes = raw.get("bytes")
        if (
            not isinstance(record_handle, str)
            or not record_handle
            or not isinstance(block_name, str)
            or not block_name
            or not isinstance(stored_path, str)
            or not stored_path
            or not isinstance(resolved_path, str)
            or not resolved_path
            or not isinstance(claimed_sha256, str)
            or not isinstance(claimed_bytes, int)
            or claimed_bytes <= 0
        ):
            raise CadSnapshotAdmissionError(
                "native XREF dependency metadata is incomplete"
            )
        candidate = Path(resolved_path)
        if candidate.is_symlink():
            raise CadSnapshotAdmissionError(
                "native XREF dependency cannot be a symlink"
            )
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as error:
            raise CadSnapshotAdmissionError(
                "native XREF dependency file is unavailable"
            ) from error
        if not resolved.is_file() or not resolved.is_relative_to(canonical_root):
            raise CadSnapshotAdmissionError(
                "native XREF dependency is outside the admitted CAD package"
            )
        actual_bytes = resolved.stat().st_size
        actual_sha256 = _file_sha256(resolved)
        if actual_bytes != claimed_bytes or actual_sha256 != claimed_sha256:
            raise CadSnapshotAdmissionError(
                "native XREF dependency differs from the file AutoCAD reported"
            )
        dependency_id = f"xref/{record_handle}"
        if dependency_id in dependency_ids or record_handle in record_handles:
            raise CadSnapshotAdmissionError("duplicate native XREF dependency record")
        dependency_ids.add(dependency_id)
        record_handles.add(record_handle)
        dependencies.append(
            {
                "id": dependency_id,
                "kind": "xref",
                "path": resolved.relative_to(canonical_root).as_posix(),
                "sha256": actual_sha256,
                "bytes": actual_bytes,
                "record_handle": record_handle,
                "block_name": block_name,
                "stored_path": stored_path,
            }
        )
    return dependencies, dependency_ids


def compile_region_probe(
    probe: dict[str, Any],
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    package_root: Path | None = None,
) -> CadSnapshot:
    """Compile admitted native AutoCAD evidence into the stable API contract.

    ``complete`` in snapshot-v1 means that every reachable source instance is
    represented in the coverage ledger.  It does not pretend that every entity
    has calculation geometry: unsupported entities remain ``unresolved`` and
    cannot be silently delegated to another parser.
    """

    if probe.get("schema") != REGION_PROBE_SCHEMA:
        raise CadSnapshotAdmissionError("unsupported native probe schema")
    if probe.get("plugin_version") not in SUPPORTED_PLUGIN_VERSIONS:
        raise CadSnapshotAdmissionError("unsupported native probe plugin version")
    if probe.get("capture_mode") != "side_database_dxf":
        raise CadSnapshotAdmissionError("only side-database capture can be admitted")
    source = probe.get("source") or {}
    if source.get("database_modified_flags") != 0:
        raise CadSnapshotAdmissionError("native database reports unsaved modifications")
    if source.get("live_database_matches_disk") is not True:
        raise CadSnapshotAdmissionError(
            "native database is not proven equal to source DXF"
        )
    metres_per_unit = source.get("metres_per_unit")
    if not isinstance(metres_per_unit, (int, float)) or not math.isfinite(
        metres_per_unit
    ):
        raise CadSnapshotAdmissionError("source unit conversion is missing")
    if metres_per_unit <= 0:
        raise CadSnapshotAdmissionError("source unit conversion must be positive")
    requested_tolerance_m = probe.get("requested_tolerance_m")
    if (
        not isinstance(requested_tolerance_m, (int, float))
        or not math.isfinite(requested_tolerance_m)
        or requested_tolerance_m <= 0
    ):
        raise CadSnapshotAdmissionError("requested tolerance is invalid")

    summary = probe.get("summary") or {}
    missing_diagnostics = [
        field for field in BLOCKING_DIAGNOSTICS if field not in summary
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

    dependencies, dependency_ids = _compile_xref_dependencies(probe, package_root)

    raw_coverage = probe.get("coverage")
    raw_regions = probe.get("regions")
    raw_paths = probe.get("paths", [])
    raw_points = probe.get("points", [])
    if (
        not isinstance(raw_coverage, list)
        or not isinstance(raw_regions, list)
        or not isinstance(raw_paths, list)
        or not isinstance(raw_points, list)
    ):
        raise CadSnapshotAdmissionError(
            "coverage, regions, paths and points must be arrays"
        )
    coverage_keys = [_identity_key(record) for record in raw_coverage]
    if len(coverage_keys) != len(set(coverage_keys)):
        raise CadSnapshotAdmissionError("duplicate source instance in native coverage")
    if summary.get("source_instances") != len(raw_coverage):
        raise CadSnapshotAdmissionError(
            "native source instance count differs from coverage"
        )

    raw_regions_by_key = {_identity_key(region): region for region in raw_regions}
    if len(raw_regions_by_key) != len(raw_regions):
        raise CadSnapshotAdmissionError("duplicate REGION instance in native geometry")
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
    if summary.get("regions") != len(raw_regions):
        raise CadSnapshotAdmissionError("native REGION count differs from geometry")
    if summary.get("resolved") != len(raw_regions) or summary.get("unresolved") != 0:
        raise CadSnapshotAdmissionError("not every REGION instance was resolved")
    if probe.get("plugin_version") in {"0.1.7", "0.1.8"}:
        if summary.get("paths") != len(raw_paths):
            raise CadSnapshotAdmissionError("native path count differs from geometry")
        if summary.get("points") != len(raw_points):
            raise CadSnapshotAdmissionError("native point count differs from geometry")

    geometry: list[dict[str, Any]] = []
    maximum_achieved_tolerance_m = 0.0
    tolerance_units = requested_tolerance_m / metres_per_unit
    for region in raw_regions:
        if region.get("status") != "native" or region.get("error_status") is not None:
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} is unresolved"
            )
        area = region.get("native_area_units2")
        perimeter = region.get("native_perimeter_units")
        if not isinstance(area, (int, float)) or not math.isfinite(area) or area <= 0:
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} has invalid area"
            )
        if (
            not isinstance(perimeter, (int, float))
            or not math.isfinite(perimeter)
            or perimeter <= 0
        ):
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} has invalid perimeter"
            )

        loops: list[dict[str, Any]] = []
        achieved_tolerance_m = 0.0
        reconstructed_outer_area = 0.0
        reconstructed_hole_area = 0.0
        reconstructed_perimeter = 0.0
        outer_count = 0
        for loop in region.get("loops", []):
            coordinates = loop.get("coordinates")
            if not isinstance(coordinates, list) or len(coordinates) < 4:
                raise CadSnapshotAdmissionError(
                    f"REGION {_geometry_id(region)} has a short loop"
                )
            if coordinates[0] != coordinates[-1]:
                raise CadSnapshotAdmissionError(
                    f"REGION {_geometry_id(region)} has an open loop"
                )
            for point in coordinates:
                if len(point) != 3 or not all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for value in point
                ):
                    raise CadSnapshotAdmissionError(
                        f"REGION {_geometry_id(region)} has invalid coordinates"
                    )
            sampled_deviation = loop.get("sampled_max_deviation_units")
            if (
                not isinstance(sampled_deviation, (int, float))
                or not math.isfinite(sampled_deviation)
                or sampled_deviation < 0
            ):
                raise CadSnapshotAdmissionError(
                    f"REGION {_geometry_id(region)} has invalid deviation"
                )
            loop_tolerance_m = sampled_deviation * metres_per_unit
            achieved_tolerance_m = max(achieved_tolerance_m, loop_tolerance_m)
            loop_area = abs(_signed_xy_area(coordinates))
            if loop.get("role") == "outer":
                outer_count += 1
                reconstructed_outer_area += loop_area
            elif loop.get("role") == "hole":
                reconstructed_hole_area += loop_area
            else:
                raise CadSnapshotAdmissionError(
                    f"REGION {_geometry_id(region)} has an unknown loop role"
                )
            reconstructed_perimeter += _length_3d(coordinates)
            loops.append(
                {
                    "role": loop["role"],
                    "closed": True,
                    "coordinates": coordinates,
                }
            )
        if not loops:
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} has no loops"
            )
        if outer_count != 1:
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} must have exactly one outer loop"
            )
        reconstructed_area = reconstructed_outer_area - reconstructed_hole_area
        area_error = abs(reconstructed_area - area)
        perimeter_error = abs(reconstructed_perimeter - perimeter)
        area_envelope = max(1e-10, perimeter * tolerance_units * 2.0)
        perimeter_envelope = max(1e-10, perimeter * 0.01)
        if area_error > area_envelope:
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} area disagrees with emitted loops"
            )
        if perimeter_error > perimeter_envelope:
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} perimeter disagrees with emitted loops"
            )
        if achieved_tolerance_m > requested_tolerance_m * (1 + 1e-12):
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} exceeds requested sampling tolerance"
            )
        maximum_achieved_tolerance_m = max(
            maximum_achieved_tolerance_m, achieved_tolerance_m
        )
        geometry_payload = {
            "id": _geometry_id(region),
            "identity": _identity(region),
            "kind": "region",
            "loops": loops,
            "native_area_units2": area,
            "native_perimeter_units": perimeter,
            "achieved_tolerance_m": achieved_tolerance_m,
        }
        geometry_payload["content_sha256"] = "0" * 64
        normalized_geometry = RegionGeometry.model_validate(
            geometry_payload
        ).model_dump(mode="json")
        normalized_geometry["content_sha256"] = _canonical_sha256(
            {
                key: value
                for key, value in normalized_geometry.items()
                if key != "content_sha256"
            }
        )
        geometry.append(normalized_geometry)

    for path in raw_paths:
        geometry_id = _geometry_id(path, "path")
        coordinates = path.get("coordinates")
        closed = path.get("closed")
        if path.get("status") != "native" or path.get("error_status") is not None:
            raise CadSnapshotAdmissionError(f"path {geometry_id} is unresolved")
        if not isinstance(closed, bool):
            raise CadSnapshotAdmissionError(f"path {geometry_id} misses closure state")
        if not isinstance(coordinates, list) or len(coordinates) < (4 if closed else 2):
            raise CadSnapshotAdmissionError(
                f"path {geometry_id} has too few coordinates"
            )
        if closed and coordinates[0] != coordinates[-1]:
            raise CadSnapshotAdmissionError(f"path {geometry_id} is not closed")
        if not closed and coordinates[0] == coordinates[-1]:
            raise CadSnapshotAdmissionError(
                f"path {geometry_id} has equal open endpoints"
            )
        for point in coordinates:
            if (
                not isinstance(point, list)
                or len(point) != 3
                or not all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for value in point
                )
            ):
                raise CadSnapshotAdmissionError(
                    f"path {geometry_id} has invalid coordinates"
                )
        projected_length = sum(
            math.dist(left[:2], right[:2])
            for left, right in zip(coordinates, coordinates[1:], strict=False)
        )
        if projected_length * metres_per_unit <= requested_tolerance_m:
            raise CadSnapshotAdmissionError(
                f"path {geometry_id} degenerates under WCS XY projection"
            )
        if closed and abs(_signed_xy_area(coordinates)) * metres_per_unit**2 <= (
            requested_tolerance_m**2
        ):
            raise CadSnapshotAdmissionError(
                f"closed path {geometry_id} has no WCS XY area"
            )
        sampled_deviation = path.get("sampled_max_deviation_units")
        if (
            not isinstance(sampled_deviation, (int, float))
            or not math.isfinite(sampled_deviation)
            or sampled_deviation < 0
        ):
            raise CadSnapshotAdmissionError(f"path {geometry_id} has invalid deviation")
        achieved_tolerance_m = sampled_deviation * metres_per_unit
        if achieved_tolerance_m > requested_tolerance_m * (1 + 1e-12):
            raise CadSnapshotAdmissionError(
                f"path {geometry_id} exceeds requested sampling tolerance"
            )
        maximum_achieved_tolerance_m = max(
            maximum_achieved_tolerance_m, achieved_tolerance_m
        )
        geometry_payload = {
            "id": geometry_id,
            "identity": _identity(path),
            "kind": "path",
            "closed": closed,
            "coordinates": coordinates,
            "achieved_tolerance_m": achieved_tolerance_m,
            "content_sha256": "0" * 64,
        }
        normalized_geometry = PathGeometry.model_validate(geometry_payload).model_dump(
            mode="json"
        )
        normalized_geometry["content_sha256"] = _canonical_sha256(
            {
                key: value
                for key, value in normalized_geometry.items()
                if key != "content_sha256"
            }
        )
        geometry.append(normalized_geometry)

    for point in raw_points:
        geometry_id = _geometry_id(point, "point")
        coordinates = point.get("coordinates")
        if point.get("status") != "native" or point.get("error_status") is not None:
            raise CadSnapshotAdmissionError(f"point {geometry_id} is unresolved")
        if (
            not isinstance(coordinates, list)
            or len(coordinates) != 3
            or not all(
                isinstance(value, (int, float)) and math.isfinite(value)
                for value in coordinates
            )
        ):
            raise CadSnapshotAdmissionError(
                f"point {geometry_id} has invalid coordinates"
            )
        geometry_payload = {
            "id": geometry_id,
            "identity": _identity(point),
            "kind": "point",
            "coordinates": coordinates,
            "content_sha256": "0" * 64,
        }
        normalized_geometry = PointGeometry.model_validate(geometry_payload).model_dump(
            mode="json"
        )
        normalized_geometry["content_sha256"] = _canonical_sha256(
            {
                key: value
                for key, value in normalized_geometry.items()
                if key != "content_sha256"
            }
        )
        geometry.append(normalized_geometry)

    geometry_ids_by_key = {
        **{_identity_key(region): _geometry_id(region) for region in raw_regions},
        **{_identity_key(path): _geometry_id(path, "path") for path in raw_paths},
        **{_identity_key(point): _geometry_id(point, "point") for point in raw_points},
    }
    coverage: list[dict[str, Any]] = []
    referenced_dependency_ids: set[str] = set()
    xref_coverage_count = 0
    for record in raw_coverage:
        status = record.get("status")
        if status not in {"native", "converted", "context", "unresolved"}:
            raise CadSnapshotAdmissionError(f"unsupported coverage status: {status}")
        geometry_id = geometry_ids_by_key.get(_identity_key(record))
        dependency_id = record.get("xref_dependency_id")
        dependency_refs = None
        if dependency_id is not None:
            xref_coverage_count += 1
            if dependency_id not in dependency_ids:
                raise CadSnapshotAdmissionError(
                    "XREF coverage references an unknown dependency"
                )
            dependency_refs = [dependency_id]
            referenced_dependency_ids.add(dependency_id)
        coverage_record = {
            "identity": _identity(record),
            "entity_type": record["entity_type"],
            "layer": record.get("layer", ""),
            "status": status,
            "method": record["method"],
            "reason": record.get("reason"),
            "geometry_ids": [geometry_id] if geometry_id else [],
        }
        if dependency_refs is not None:
            coverage_record["dependency_ids"] = dependency_refs
        coverage.append(coverage_record)
    if dependencies is not None:
        if xref_coverage_count != summary.get("xref_block_references"):
            raise CadSnapshotAdmissionError(
                "native XREF reference count differs from dependency coverage"
            )
        if referenced_dependency_ids != dependency_ids:
            raise CadSnapshotAdmissionError(
                "native XREF dependency is not referenced by source coverage"
            )

    counts = Counter(record["status"] for record in coverage)
    snapshot_without_hash = {
        "schema": SNAPSHOT_SCHEMA,
        "source": {
            "sha256": source["sha256"],
            "saved": True,
            "units_code": source["units_code"],
            "document_revision": source["document_revision"],
        },
        "extraction": {
            "autocad_version": autocad_version,
            "plugin_version": probe["plugin_version"],
            "target": target,
            "projection": "wcs-xy-planar",
            "requested_tolerance_m": requested_tolerance_m,
        },
        "coverage": coverage,
        "geometry": geometry,
    }
    if dependencies is not None:
        snapshot_without_hash["dependencies"] = dependencies
    snapshot = {
        **snapshot_without_hash,
        "summary": {
            "source_instances": len(coverage),
            "native": counts["native"],
            "converted": counts["converted"],
            "context": counts["context"],
            "unresolved": counts["unresolved"],
            "payload_sha256": "0" * 64,
            "complete": True,
        },
    }
    normalized_snapshot = CadSnapshot.model_validate(snapshot).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    normalized_snapshot["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in normalized_snapshot.items() if key != "summary"}
    )
    admitted = CadSnapshot.model_validate(normalized_snapshot)
    if maximum_achieved_tolerance_m > admitted.extraction.requested_tolerance_m:
        raise CadSnapshotAdmissionError("compiled geometry exceeds requested tolerance")
    return admitted
