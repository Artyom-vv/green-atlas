"""Small review geometry from an admitted project; no CAD parser or new capture."""

from __future__ import annotations

import math
from typing import Any

from shapely import STRtree
from shapely.geometry import Point, mapping, shape

from app.dxf_import.units import DXF_UNIT_FACTORS
from app.location.alignment import normalize_address
from app.native_query.capture.capture_store import (
    NativeCaptureStore,
    read_capture_receipt,
)
from app.native_query.capture.semantic_evidence import read_semantics
from app.projects.contracts import Project

MAX_REVIEW_FEATURES = 5000
MAX_REVIEW_POINTS = 150_000


def _point_count(value: Any) -> int:
    if not isinstance(value, (list, tuple)):
        return 0
    if value and isinstance(value[0], (int, float)):
        return 1
    return sum(_point_count(item) for item in value)


def cad_review(
    project: Project, capture_store: NativeCaptureStore | None
) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    if project.geometry is None:
        return (
            {"type": "FeatureCollection", "features": []},
            None,
            {"status": "not_prepared", "message": "Сначала подготовьте геометрию CAD"},
        )
    rows: list[dict[str, Any]] = []
    points = omitted = 0
    raw = project.geometry.feature_collection.get("features", [])
    # Building vertices are the primary alignment surface; preserve their exact XY.
    ordered = sorted(
        raw, key=lambda row: row.get("properties", {}).get("kind") != "building"
    )
    for index, feature in enumerate(ordered):
        properties = feature.get("properties", {})
        kind = properties.get("kind")
        if kind not in {
            "building",
            "road",
            "site",
            "lawn",
            "water",
            "boundary",
            "planting_area",
        }:
            continue
        geometry_data = feature.get("geometry", {})
        try:
            geometry = shape(geometry_data)
            if (
                geometry.is_empty
                or not geometry.is_valid
                or not all(math.isfinite(v) for v in geometry.bounds)
            ):
                omitted += 1
                continue
            count = _point_count(geometry_data.get("coordinates"))
            if len(rows) >= MAX_REVIEW_FEATURES or points + count > MAX_REVIEW_POINTS:
                omitted += 1
                continue
        except (ValueError, TypeError, KeyError):
            omitted += 1
            continue
        points += count
        identifier = str(
            feature.get("id") or properties.get("source_handle") or f"cad-{index}"
        )
        props: dict[str, Any] = {"kind": kind, "source_id": identifier}
        # Explicit native property evidence remains local and is never sent to DaData.
        if properties.get("source_native_geometry") is True:
            address = normalize_address(properties.get("address"))
            if address:
                props["address"] = address
        rows.append(
            {
                "type": "Feature",
                "id": identifier,
                "geometry": mapping(geometry),
                "properties": props,
            }
        )
    polygons = [
        (i, shape(row["geometry"]))
        for i, row in enumerate(rows)
        if row["properties"]["kind"] == "building"
        and row["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    ]
    semantic_status = "unavailable"
    semantic_sha = None
    source_sha = project.source_file.content_sha256 if project.source_file else None
    if polygons and source_sha and capture_store is not None:
        try:
            package = capture_store.package(project.id, source_sha)
            if package is not None:
                receipt = read_capture_receipt(package.root)
                if receipt.schema_ == "green-atlas.native-session/3":
                    semantics, _ = read_semantics(package.root, receipt, lambda: None)
                    factor = DXF_UNIT_FACTORS[receipt.units_code][1]
                    tree = STRtree([polygon for _, polygon in polygons])
                    labels: dict[int, set[str]] = {}
                    for row in semantics.rows:
                        address = normalize_address(row.value)
                        if (
                            not address
                            or row.status != "captured"
                            or row.invisible
                            or row.origin == "attribute_definition_default"
                            or row.anchor_wcs is None
                        ):
                            continue
                        point = Point(
                            row.anchor_wcs[0] * factor, row.anchor_wcs[1] * factor
                        )
                        targets = [
                            int(i)
                            for i in tree.query(point)
                            if polygons[int(i)][1].covers(point)
                        ]
                        if len(targets) == 1:
                            labels.setdefault(polygons[targets[0]][0], set()).add(
                                address
                            )
                    for index, values in labels.items():
                        if len(values) == 1:
                            rows[index]["properties"]["address"] = next(iter(values))
                    semantic_status = "loaded" if semantics.complete else "partial"
                    semantic_sha = receipt.semantics_sha256
        except (ValueError, OSError, KeyError):
            semantic_status = "invalid"
    bounds = None
    if rows:
        extents = [shape(row["geometry"]).bounds for row in rows]
        bounds = (
            min(b[0] for b in extents),
            min(b[1] for b in extents),
            max(b[2] for b in extents),
            max(b[3] for b in extents),
        )
        if bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
            bounds = None
    return (
        {"type": "FeatureCollection", "features": rows},
        bounds,
        {
            "status": "prepared_display",
            "coordinate_space": "cad_wcs_metres",
            "feature_count": len(rows),
            "omitted_review_features": omitted,
            "semantic_status": semantic_status,
            "semantics_sha256": semantic_sha,
            "address_policy": "unique native label inside one prepared building; illustrative centroid pairing",
        },
    )
