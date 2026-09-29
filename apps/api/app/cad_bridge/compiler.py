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
SUPPORTED_PLUGIN_VERSIONS = {
    "0.1.4",
    "0.1.5",
    "0.1.6",
    "0.1.7",
    "0.1.8",
    "0.1.9",
    "0.1.10",
    "0.1.11",
    "0.1.12",
    "0.1.13",
    "0.1.14",
    "0.1.15",
    "0.1.16",
    "0.1.17",
    "0.1.19",
    "0.1.20",
    "0.1.21",
    "0.1.22",
    "0.1.23",
    "0.1.24",
    "0.1.25",
    "0.1.26",
    "0.1.27",
    "0.1.28",
    "0.1.30",
    "0.1.33",
    "0.1.34",
    "0.1.35",
    "0.1.36",
    "0.1.37",
    "0.1.38",
    "0.1.40", "0.1.41", "0.1.42", "0.1.43", "0.1.44",
}
XREF_DEPENDENCY_PLUGIN_VERSIONS = {
    "0.1.6",
    "0.1.7",
    "0.1.8",
    "0.1.9",
    "0.1.10",
    "0.1.11",
    "0.1.12",
    "0.1.13",
    "0.1.14",
    "0.1.15",
    "0.1.16",
    "0.1.17",
    "0.1.19",
    "0.1.20",
    "0.1.21",
    "0.1.22",
    "0.1.23",
    "0.1.24",
    "0.1.25",
    "0.1.26",
    "0.1.27",
    "0.1.28",
    "0.1.30",
    "0.1.33",
    "0.1.34",
    "0.1.35",
    "0.1.36",
    "0.1.37",
    "0.1.38",
    "0.1.40", "0.1.41", "0.1.42", "0.1.43", "0.1.44",
}
AREA_PROPOSAL_PLUGIN_VERSIONS = {"0.1.36", "0.1.37", "0.1.38", "0.1.40", "0.1.41", "0.1.42", "0.1.43", "0.1.44"}
MULTI_OUTER_PLUGIN_VERSIONS = AREA_PROPOSAL_PLUGIN_VERSIONS | {"0.1.35"}
DERIVED_REGION_PLUGIN_VERSIONS = MULTI_OUTER_PLUGIN_VERSIONS | {"0.1.34"}
TRAVERSAL_DIAGNOSTICS = (
    "cyclic_block_references",
    "unloaded_xref_block_references",
    "unresolved_xref_block_references",
    "unexpanded_minsert_blocks",
    "unreadable_block_records",
    "unreadable_entities",
)
BLOCKING_DIAGNOSTICS = (
    "cyclic_block_references",
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
    # City-scale WCS offsets can be orders of magnitude larger than a small
    # planting/building patch. Translate before summing to avoid cancellation.
    origin_x, origin_y = coordinates[0][:2]
    return 0.5 * math.fsum(
        (left[0] - origin_x) * (right[1] - origin_y)
        - (right[0] - origin_x) * (left[1] - origin_y)
        for left, right in zip(coordinates, coordinates[1:], strict=False)
    )


def _length_3d(coordinates: list[list[float]]) -> float:
    return sum(
        math.dist(left, right)
        for left, right in zip(coordinates, coordinates[1:], strict=False)
    )


def _compile_xref_dependencies(
    probe: dict[str, Any], package_root: Path | None, *, live: bool = False
) -> tuple[list[dict[str, Any]] | None, set[str], set[str]]:
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
        return None, set(), set()
    if probe.get("plugin_version") not in XREF_DEPENDENCY_PLUGIN_VERSIONS:
        raise CadSnapshotAdmissionError(
            "native probe cannot prove the exact XREF dependency files"
        )
    if not isinstance(raw_dependencies, list) or not raw_dependencies:
        raise CadSnapshotAdmissionError("native probe has no XREF dependency ledger")
    if summary.get("xref_dependency_records") != len(raw_dependencies):
        raise CadSnapshotAdmissionError(
            "native XREF dependency count differs from its ledger"
        )

    dependencies: list[dict[str, Any]] = []
    dependency_ids: set[str] = set()
    unresolved_dependency_ids: set[str] = set()
    record_handles: set[str] = set()
    canonical_root: Path | None = None
    for raw in raw_dependencies:
        if not isinstance(raw, dict):
            raise CadSnapshotAdmissionError("native XREF dependency is invalid")
        record_handle = raw.get("record_handle")
        block_name = raw.get("block_name")
        stored_path = raw.get("stored_path")
        status = raw.get("status")
        if (
            not isinstance(record_handle, str)
            or not record_handle
            or not isinstance(block_name, str)
            or not block_name
            or not isinstance(stored_path, str)
            or not stored_path
            or status not in {"resolved", "unresolved"}
        ):
            raise CadSnapshotAdmissionError(
                "native XREF dependency metadata is incomplete"
            )
        dependency_id = f"xref/{record_handle}"
        if (
            dependency_id in dependency_ids
            or dependency_id in unresolved_dependency_ids
            or record_handle in record_handles
        ):
            raise CadSnapshotAdmissionError("duplicate native XREF dependency record")
        record_handles.add(record_handle)
        if status == "unresolved":
            unresolved_dependency_ids.add(dependency_id)
            continue

        if live:
            # The source is captured geometry, not another read of an XREF file.
            dependency_ids.add(dependency_id)
            dependencies.append(
                {
                    "id": dependency_id,
                    "record_handle": record_handle,
                    "block_name": block_name,
                    "stored_path": stored_path,
                }
            )
            continue

        resolved_path = raw.get("resolved_path")
        claimed_sha256 = raw.get("sha256")
        claimed_bytes = raw.get("bytes")
        if (
            not isinstance(resolved_path, str)
            or not resolved_path
            or not isinstance(claimed_sha256, str)
            or not isinstance(claimed_bytes, int)
            or claimed_bytes <= 0
        ):
            raise CadSnapshotAdmissionError(
                "native XREF dependency metadata is incomplete"
            )
        if package_root is None:
            raise CadSnapshotAdmissionError(
                "package root is required to verify native XREF dependencies"
            )
        if canonical_root is None:
            try:
                canonical_root = package_root.resolve(strict=True)
            except OSError as error:
                raise CadSnapshotAdmissionError(
                    "CAD package root is unavailable"
                ) from error
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
        dependency_ids.add(dependency_id)
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
    return dependencies or None, dependency_ids, unresolved_dependency_ids


def compile_region_probe(
    probe: dict[str, Any],
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    package_root: Path | None = None,
) -> CadSnapshot:
    return _compile_probe(
        probe,
        autocad_version=autocad_version,
        target=target,
        package_root=package_root,
    )


def compile_live_document(
    content: bytes | bytearray,
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
) -> CadSnapshot:
    """The immutable native capture is the source, not the older disk DXF."""
    probe = json.loads(content)
    if not isinstance(probe, dict):
        raise CadSnapshotAdmissionError("native capture must be an object")
    return _compile_probe(
        probe,
        autocad_version=autocad_version,
        target=target,
        live_source_sha256=hashlib.sha256(content).hexdigest(),
    )


def _compile_probe(
    probe: dict[str, Any],
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    package_root: Path | None = None,
    live_source_sha256: str | None = None,
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
    live = live_source_sha256 is not None
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

    dependencies, dependency_ids, unresolved_dependency_ids = (
        _compile_xref_dependencies(probe, package_root, live=live)
    )

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
    if probe.get("plugin_version") not in DERIVED_REGION_PLUGIN_VERSIONS and any(
        region.get("source_handles") for region in raw_regions
    ):
        raise CadSnapshotAdmissionError(
            "derived AutoCAD regions require native producer 0.1.34 or later"
        )
    # A failed native extraction is a coverage gap, not geometry. Keep its
    # ledger entry and admit healthy siblings; never relabel failed data native.
    failed_regions = [
        region for region in raw_regions if region.get("status") == "unresolved"
    ]
    coverage_by_key = {_identity_key(record): record for record in raw_coverage}
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
    if raw_area_proposals:
        if probe.get("plugin_version") not in AREA_PROPOSAL_PLUGIN_VERSIONS:
            raise CadSnapshotAdmissionError(
                "native area proposals require producer 0.1.36"
            )
    if probe.get("plugin_version") in AREA_PROPOSAL_PLUGIN_VERSIONS and summary.get(
        "area_proposals"
    ) != len(raw_area_proposals):
        raise CadSnapshotAdmissionError("native area proposal count differs from geometry")
    if probe.get("plugin_version") in AREA_PROPOSAL_PLUGIN_VERSIONS:
        if (
            summary.get("area_proposal_candidates")
            != len(raw_area_proposals) + len(raw_area_proposal_rejections)
            or summary.get("area_proposal_rejected")
            != len(raw_area_proposal_rejections)
        ):
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
    raw_regions = [
        region for region in raw_regions if region.get("status") != "unresolved"
    ]
    authored_regions = [
        region for region in raw_regions if not region.get("source_handles")
    ]
    derived_regions = [region for region in raw_regions if region.get("source_handles")]
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
    if probe.get("plugin_version") in {
        "0.1.7",
        "0.1.8",
        "0.1.9",
        "0.1.10",
        "0.1.11",
        "0.1.12",
        "0.1.13",
        "0.1.14",
        "0.1.15",
        "0.1.16",
        "0.1.17",
        "0.1.19",
        "0.1.20",
        "0.1.21",
        "0.1.22",
        "0.1.23",
        "0.1.24",
        "0.1.25",
        "0.1.26",
        "0.1.27",
        "0.1.28",
        "0.1.30",
        "0.1.33",
        "0.1.34",
        "0.1.35",
        "0.1.36",
        "0.1.37",
        "0.1.38",
        "0.1.40", "0.1.41", "0.1.42", "0.1.43", "0.1.44",
    }:
        if summary.get("paths") != len(raw_paths):
            raise CadSnapshotAdmissionError("native path count differs from geometry")
        if summary.get("points") != len(raw_points):
            raise CadSnapshotAdmissionError("native point count differs from geometry")

    geometry: list[dict[str, Any]] = []
    proposal_previews: list[tuple[dict[str, Any], dict[str, Any]]] = []
    maximum_achieved_tolerance_m = 0.0
    tolerance_units = requested_tolerance_m / metres_per_unit
    for region, is_proposal in [
        *((item, False) for item in raw_regions),
        *((item, True) for item in raw_area_proposals),
    ]:
        source_handles = region.get("source_handles") or []
        if is_proposal:
            if source_handles or region.get("proposal_method") != "explicit-chord-closure":
                raise CadSnapshotAdmissionError("area proposal has invalid native method")
            key = _identity_key(region)
            path = raw_paths_by_key.get(key)
            record = coverage_by_key.get(key)
            gap = region.get("closure_gap_wcs_xy_units")
            if (
                path is None or record is None
                or record.get("status") != "native"
                or path.get("closed") is not False
                or path.get("layer") != region.get("layer")
                or record.get("layer") != region.get("layer")
                or not isinstance(gap, (int, float))
                or not math.isfinite(gap) or gap <= 0
            ):
                raise CadSnapshotAdmissionError(
                    "area proposal lacks an open same-layer native path"
                )
            coordinates = path.get("coordinates")
            if not isinstance(coordinates, list) or len(coordinates) < 2:
                raise CadSnapshotAdmissionError("area proposal source path is invalid")
            actual_gap = math.dist(coordinates[0][:2], coordinates[-1][:2])
            if abs(actual_gap - gap) > max(1e-9, requested_tolerance_m / metres_per_unit):
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
                raise CadSnapshotAdmissionError(
                    "derived REGION has invalid source handles"
                )
            for handle in source_handles:
                member_key = (handle, tuple(region.get("instance_chain", [])))
                member_path = raw_paths_by_key.get(member_key)
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
        if outer_count > 1 and probe.get("plugin_version") not in MULTI_OUTER_PLUGIN_VERSIONS:
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} has multiple outer loops "
                "without native producer 0.1.35"
            )
        if outer_count < 1:
            raise CadSnapshotAdmissionError(
                f"REGION {_geometry_id(region)} has no outer loop"
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
        proposal_id = _geometry_id(region, "area-proposal")
        geometry_payload = {
            "id": (
                f"{proposal_id}/region" if is_proposal else
                _geometry_id(region, "derived-region" if source_handles else "region")
            ),
            "identity": _identity(region),
            "kind": "region",
            "loops": loops,
            "native_area_units2": area,
            "native_perimeter_units": perimeter,
            "achieved_tolerance_m": achieved_tolerance_m,
        }
        if source_handles:
            geometry_payload["derived_from"] = [
                {
                    "handle": handle,
                    "instance_chain": list(region.get("instance_chain", [])),
                }
                for handle in source_handles
            ]
        geometry_payload["content_sha256"] = "0" * 64
        normalized_geometry = RegionGeometry.model_validate(
            geometry_payload
        ).model_dump(mode="json", exclude_none=True)
        normalized_geometry["content_sha256"] = _canonical_sha256(
            {
                key: value
                for key, value in normalized_geometry.items()
                if key != "content_sha256"
            }
        )
        if is_proposal:
            proposal_previews.append((region, normalized_geometry))
        else:
            geometry.append(normalized_geometry)

    admitted_paths: list[dict[str, Any]] = []
    quarantined_path_reasons: dict[tuple[str, tuple[str, ...]], str] = {}
    for path in raw_paths:
        geometry_id = _geometry_id(path, "path")
        identity_key = _identity_key(path)
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
            quarantined_path_reasons[identity_key] = (
                "AutoCAD path has no usable WCS XY length at the requested "
                "tolerance; excluded from calculation geometry"
            )
            continue
        if closed and abs(_signed_xy_area(coordinates)) * metres_per_unit**2 <= (
            requested_tolerance_m**2
        ):
            quarantined_path_reasons[identity_key] = (
                "closed AutoCAD path has no usable WCS XY area at the requested "
                "tolerance; excluded from calculation geometry"
            )
            continue
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
        admitted_paths.append(path)

    path_payloads_by_id = {
        item["id"]: item for item in geometry if item["kind"] == "path"
    }
    path_geometry_by_key = {
        _identity_key(item): path_payloads_by_id[_geometry_id(item, "path")]
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
            "source_path_content_sha256": source_path["content_sha256"],
            "layer": raw["layer"],
            "method": raw["proposal_method"],
            "closure_gap_wcs_xy_units": raw["closure_gap_wcs_xy_units"],
            "preview": preview,
        }
        proposal["content_sha256"] = _canonical_sha256(proposal)
        area_proposals.append(proposal)

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
        **{_identity_key(region): _geometry_id(region) for region in authored_regions},
        **{_identity_key(path): _geometry_id(path, "path") for path in admitted_paths},
        **{_identity_key(point): _geometry_id(point, "point") for point in raw_points},
    }
    derived_ids_by_key = {
        _identity_key(region): _geometry_id(region, "derived-region")
        for region in derived_regions
    }
    coverage: list[dict[str, Any]] = []
    referenced_dependency_ids: set[str] = set()
    xref_coverage_count = 0
    for record in raw_coverage:
        status = record.get("status")
        quarantined_reason = quarantined_path_reasons.get(_identity_key(record))
        method = record["method"]
        reason = record.get("reason")
        if quarantined_reason is not None:
            status = "unresolved"
            method = "autocad-wcs-xy-quarantined"
            reason = quarantined_reason
        if status not in {"native", "converted", "context", "unresolved"}:
            raise CadSnapshotAdmissionError(f"unsupported coverage status: {status}")
        geometry_id = geometry_ids_by_key.get(_identity_key(record))
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
        coverage_record = {
            "identity": _identity(record),
            "entity_type": record["entity_type"],
            "layer": record.get("layer", ""),
            "status": status,
            "method": method,
            "reason": reason,
            "geometry_ids": (
                ([geometry_id] if geometry_id else [])
                + (
                    [derived_ids_by_key[_identity_key(record)]]
                    if _identity_key(record) in derived_ids_by_key
                    else []
                )
            ),
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
    if dependency_ids or unresolved_dependency_ids:
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
            "sha256": live_source_sha256 or source["sha256"],
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
    if area_proposals:
        snapshot_without_hash["area_proposals"] = area_proposals
    if live:
        snapshot_without_hash["source"]["live_capture"] = {
            "mode": "live_document",
            "original_path": source["path"],
            "original_disk_sha256": source["sha256"],
            "database_modified_flags": source["database_modified_flags"],
        }
    if dependencies is not None:
        snapshot_without_hash["live_references" if live else "dependencies"] = (
            dependencies
        )
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
