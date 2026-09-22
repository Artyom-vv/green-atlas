"""Extract reviewable elevation controls from planar topographic annotations.

The Kustanayskaya topography stores spot heights as TEXT beside circular PIKET
symbols; geometry itself has Z=0.  This extractor does not pretend the drawing
contains a TIN.  It associates a label only when the marker is uniquely close
to the rendered text box, preserves both source handles, and treats a plausible
two-label height difference as an explicit upper/lower curb pair.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import ezdxf
from ezdxf import bbox as dxf_bbox
import numpy as np
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TOPOGRAPHY = next(
    (ROOT / ".runtime/kustanayskaya-mac-ready-20260917/dxf").rglob(
        "00.1_10004141_Топография.dxf"
    )
)
DEFAULT_SURFACES = (
    ROOT / ".runtime/deterministic-render-audit-20260920-expanded/authored-surfaces.geojson"
)
DEFAULT_OUTPUT = ROOT / ".runtime/topographic-elevation-controls-20260920"
NUMBER = re.compile(r"\s*[+-]?\d{2,3}[.,]\d{1,3}\s*")
SURFACE_CLASSES = ("road", "sidewalk", "lawn", "special_surface", "stairs")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def block_number(name: str) -> int:
    if name == "PIKET":
        return 0
    try:
        return int(name.removeprefix("PIKET_"))
    except ValueError as error:
        raise ValueError(f"Unexpected PIKET block name: {name}") from error


def point_box_distance(point: np.ndarray, bounds: np.ndarray) -> float:
    delta = np.maximum(np.maximum(bounds[:2] - point, point - bounds[2:]), 0.0)
    return float(np.linalg.norm(delta))


def semantic_context(point: Point, unions: dict[str, Any], radius: float = 0.4) -> dict[str, Any]:
    disk = point.buffer(radius, quad_segs=8)
    areas = {
        semantic_class: float(disk.intersection(geometry).area)
        for semantic_class, geometry in unions.items()
        if not geometry.is_empty
    }
    total = disk.area
    fractions = {
        key: round(value / total, 6) for key, value in areas.items() if value > 1e-8
    }
    distances = {
        key: round(float(point.distance(geometry)), 6)
        for key, geometry in unions.items()
        if not geometry.is_empty and point.distance(geometry) <= 1.0
    }
    ranked = sorted(fractions.items(), key=lambda row: (-row[1], row[0]))
    dominant = None
    if ranked and ranked[0][1] >= 0.65 and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= 0.20):
        dominant = ranked[0][0]
    return {
        "sample_radius_m": radius,
        "area_fractions": fractions,
        "distance_m": distances,
        "dominant_surface": dominant,
    }


def extract(topography_path: Path, surfaces_path: Path) -> dict[str, Any]:
    document = ezdxf.readfile(topography_path)
    modelspace = document.modelspace()
    markers = sorted(
        [
            entity for entity in modelspace.query("INSERT")
            if entity.dxf.layer == "Горизонтали" and entity.dxf.name.startswith("PIKET")
        ],
        key=lambda entity: block_number(entity.dxf.name),
    )
    labels = sorted(
        [
            entity for entity in modelspace.query("TEXT")
            if entity.dxf.layer == "Горизонтали" and NUMBER.fullmatch(entity.dxf.text or "")
        ],
        key=lambda entity: int(entity.dxf.handle, 16),
    )
    if len(markers) < 2 or not labels:
        raise ValueError("Topography does not contain the expected PIKET/TEXT evidence")

    marker_xy = np.asarray([[row.dxf.insert.x, row.dxf.insert.y] for row in markers], dtype=float)
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    associations: list[dict[str, Any]] = []
    nearest_marker_ordinals = []
    for label_ordinal, label in enumerate(labels):
        extents = dxf_bbox.extents([label])
        bounds = np.asarray([
            extents.extmin.x, extents.extmin.y, extents.extmax.x, extents.extmax.y
        ], dtype=float)
        center = (bounds[:2] + bounds[2:]) / 2.0
        center_distances = np.sum((marker_xy - center) ** 2, axis=1)
        candidate_indices = np.argpartition(center_distances, min(16, len(markers)-1))[:16]
        ranked = sorted(
            (point_box_distance(marker_xy[index], bounds), int(index))
            for index in candidate_indices
        )
        nearest_distance, nearest_index = ranked[0]
        second_distance = ranked[1][0]
        uniqueness_margin = second_distance - nearest_distance
        accepted = nearest_distance <= 0.75 and uniqueness_margin >= 0.50
        row = {
            "label_handle": label.dxf.handle,
            "label_ordinal": label_ordinal,
            "value_m": float(label.dxf.text.replace(",", ".")),
            "text": label.dxf.text,
            "text_rotation_deg": float(label.dxf.get("rotation", 0.0) or 0.0),
            "text_box_xy": [round(float(value), 9) for value in bounds],
            "nearest_marker_handle": markers[nearest_index].dxf.handle,
            "nearest_marker_block": markers[nearest_index].dxf.name,
            "nearest_marker_ordinal": nearest_index,
            "text_box_distance_m": round(nearest_distance, 9),
            "second_marker_margin_m": round(uniqueness_margin, 9),
            "association_status": (
                "accepted_unique_text_box_proximity" if accepted
                else "rejected_ambiguous_or_distant"
            ),
        }
        associations.append(row)
        nearest_marker_ordinals.append(nearest_index)
        if accepted:
            groups[nearest_index].append(row)

    surfaces = json.loads(surfaces_path.read_text())
    unions = {
        semantic_class: unary_union([
            shape(feature["geometry"]) for feature in surfaces["features"]
            if feature.get("properties", {}).get("class") == semantic_class
        ])
        for semantic_class in SURFACE_CLASSES
    }
    controls = []
    marker_reviews = []
    rejection_counts: Counter[str] = Counter()
    for marker_index, marker in enumerate(markers):
        rows = sorted(groups.get(marker_index, []), key=lambda row: row["label_ordinal"])
        point = Point(float(marker.dxf.insert.x), float(marker.dxf.insert.y))
        context = semantic_context(point, unions)
        base = {
            "marker_handle": marker.dxf.handle,
            "marker_block": marker.dxf.name,
            "marker_ordinal": marker_index,
            "xy": [point.x, point.y],
            "semantic_context": context,
            "labels": rows,
        }
        if len(rows) == 1:
            semantic_class = context["dominant_surface"]
            status = (
                "accepted_single_source_height_with_surface_class"
                if semantic_class in SURFACE_CLASSES
                else "accepted_height_unclassified_surface"
            )
            control = {
                **base,
                "id": f"topo:{marker.dxf.handle}:{rows[0]['label_handle']}",
                "kind": "single_spot_elevation",
                "z_m": rows[0]["value_m"],
                "semantic_class": semantic_class,
                "vertical_status": "source_annotation_associated_by_unique_geometry",
                "review_status": status,
            }
            controls.append(control)
            marker_reviews.append({**base, "status": status})
        elif len(rows) == 2:
            difference = abs(rows[0]["value_m"] - rows[1]["value_m"])
            if not 0.08 <= difference <= 0.30:
                rejection_counts["two_labels_implausible_curb_difference"] += 1
                marker_reviews.append({
                    **base, "status": "rejected_two_labels_implausible_curb_difference",
                    "height_difference_m": difference,
                })
                continue
            lower, upper = sorted(rows, key=lambda row: row["value_m"])
            nearby = context["distance_m"]
            lower_class = "road" if nearby.get("road", math.inf) <= 0.10 else None
            upper_candidates = sorted(
                semantic_class for semantic_class in ("sidewalk", "lawn", "special_surface", "stairs")
                if nearby.get(semantic_class, math.inf) <= 0.40
            )
            status = (
                "accepted_curb_pair_with_surface_context"
                if lower_class == "road" and upper_candidates
                else "accepted_curb_pair_surface_role_requires_review"
            )
            for role, label, semantic_class in (
                ("lower", lower, lower_class),
                ("upper", upper, upper_candidates[0] if len(upper_candidates) == 1 else None),
            ):
                controls.append({
                    **base,
                    "id": f"topo:{marker.dxf.handle}:{label['label_handle']}:{role}",
                    "kind": "curb_pair_elevation",
                    "curb_role": role,
                    "z_m": label["value_m"],
                    "paired_height_difference_m": round(difference, 6),
                    "semantic_class": semantic_class,
                    "semantic_candidates": (
                        [lower_class] if role == "lower" and lower_class else upper_candidates
                    ),
                    "vertical_status": "source_annotation_pair_associated_by_unique_geometry",
                    "review_status": status,
                })
            marker_reviews.append({
                **base, "status": status, "height_difference_m": round(difference, 6),
                "lower_semantic": lower_class, "upper_semantic_candidates": upper_candidates,
            })
        elif len(rows) == 0:
            rejection_counts["no_uniquely_associated_label"] += 1
        else:
            rejection_counts[f"ambiguous_label_multiplicity_{len(rows)}"] += 1
            marker_reviews.append({**base, "status": "rejected_ambiguous_label_multiplicity"})

    order_correlation = float(np.corrcoef(
        np.arange(len(labels), dtype=float), np.asarray(nearest_marker_ordinals, dtype=float)
    )[0, 1])
    accepted_associations = sum(
        row["association_status"].startswith("accepted") for row in associations
    )
    classified_controls = sum(row.get("semantic_class") is not None for row in controls)
    return {
        "schema": "green-atlas.topographic-elevation-controls.v1",
        "status": "source_annotation_controls_require_surface_role_review",
        "source": {
            "path": str(topography_path.resolve()),
            "bytes": topography_path.stat().st_size,
            "sha256": sha256(topography_path),
            "drawing_units": int(document.units),
            "vertical_reference_from_project_documents": "Moscow height system",
        },
        "surface_source": {
            "path": str(surfaces_path.resolve()),
            "bytes": surfaces_path.stat().st_size,
            "sha256": sha256(surfaces_path),
        },
        "association_contract": {
            "marker": "top-level INSERT on layer Горизонтали with block name PIKET*",
            "label": "top-level numeric TEXT on layer Горизонтали",
            "text_geometry": "ezdxf text bounding box",
            "maximum_text_box_distance_m": 0.75,
            "minimum_second_marker_margin_m": 0.50,
            "curb_pair_height_difference_m": [0.08, 0.30],
            "unknown_policy": "ambiguous labels and surface roles remain review-only",
        },
        "quality": {
            "markers": len(markers),
            "numeric_labels": len(labels),
            "accepted_label_associations": accepted_associations,
            "accepted_label_ratio": round(accepted_associations / len(labels), 9),
            "marker_label_multiplicity": dict(sorted(Counter(
                len(groups.get(index, [])) for index in range(len(markers))
            ).items())),
            "source_order_correlation": round(order_correlation, 9),
            "controls": len(controls),
            "controls_with_semantic_class": classified_controls,
            "controls_with_semantic_class_ratio": round(classified_controls / len(controls), 9),
            "review_statuses": dict(sorted(Counter(
                row["status"] for row in marker_reviews
            ).items())),
            "rejections": dict(sorted(rejection_counts.items())),
        },
        "controls": controls,
        "marker_reviews": marker_reviews,
        "label_associations": associations,
        "limitations": [
            "The drawing contains source-backed height annotations but no native terrain mesh.",
            "Text-to-marker association is deterministic and geometry-gated, not a survey database relation.",
            "Upper/lower curb roles follow numeric order; semantic side remains explicit when ambiguous.",
            "No interpolation or extrapolation is performed by this extractor.",
        ],
    }


def write_geojson(packet: dict[str, Any], path: Path) -> None:
    features = []
    for row in packet["controls"]:
        features.append({
            "type": "Feature",
            "geometry": mapping(Point(*row["xy"])),
            "properties": {
                key: value for key, value in row.items()
                if key not in {"xy", "labels", "semantic_context"}
            } | {
                "marker_handle": row["marker_handle"],
                "label_handles": [label["label_handle"] for label in row["labels"]],
            },
        })
    path.write_text(json.dumps({
        "type": "FeatureCollection", "features": features
    }, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--topography", type=Path, default=DEFAULT_TOPOGRAPHY)
    parser.add_argument("--surfaces", type=Path, default=DEFAULT_SURFACES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    packet = extract(args.topography, args.surfaces)
    packet_path = args.output / "elevation-controls.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    write_geojson(packet, args.output / "elevation-controls.geojson")
    receipt = {
        "schema": "green-atlas.topographic-elevation-controls-receipt.v1",
        "status": packet["status"],
        "packet": {
            "path": str(packet_path.resolve()), "bytes": packet_path.stat().st_size,
            "sha256": sha256(packet_path),
        },
        "quality": packet["quality"],
    }
    (args.output / "receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
