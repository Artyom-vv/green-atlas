"""Measure stability of a saved cartographic candidate alignment.

This is a diagnostic on existing control pairs, not a survey validation or a
new CAD reader. A fit with no independent controls remains a candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from assemble_metric_scene import fit_similarity


def audit(data: dict) -> dict:
    controls = data.get("controls") or []
    inliers = [row for row in controls if row.get("inlier") is True]
    if len(inliers) < 4:
        return {"status": "insufficient_inliers_for_leave_one_out",
                "controls": len(controls), "inliers": len(inliers)}
    source = np.array([row["osm_tangent_centroid"] for row in inliers], dtype=float)
    target = np.array([row["dxf_centroid"] for row in inliers], dtype=float)
    if source.shape != (len(inliers), 2) or target.shape != source.shape:
        raise ValueError("Control coordinates must be XY pairs")
    if not np.isfinite(source).all() or not np.isfinite(target).all():
        raise ValueError("Non-finite control coordinates")
    errors = []
    for index, row in enumerate(inliers):
        training = [item for item in range(len(inliers)) if item != index]
        scale, rotation, translation = fit_similarity(source[training], target[training])
        estimate = scale * source[index] @ rotation + translation
        errors.append({"address": row.get("address"),
                       "left_out_error_m": round(float(np.linalg.norm(estimate-target[index])), 6),
                       "training_controls": len(training)})
    values = np.array([row["left_out_error_m"] for row in errors])
    return {"status": "candidate_without_independent_survey_checkpoints",
            "controls": len(controls), "inliers": len(inliers),
            "reported_fit_rmse_m": data.get("ransac", {}).get("rmse_inliers_m"),
            "leave_one_out": errors,
            "leave_one_out_rmse_m": round(float(np.sqrt(np.mean(values**2))), 6),
            "leave_one_out_max_m": round(float(values.max()), 6),
            "interpretation": "Internal stability of map-derived ties only; not surveyed positioning accuracy"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.alignment.read_bytes()
    data = json.loads(raw)
    if data.get("schema") != "green-atlas.candidate-osm-alignment.v1":
        parser.error("Unsupported candidate alignment schema")
    result = {"schema": "green-atlas.render-alignment-audit.v1",
              "alignment_sha256": hashlib.sha256(raw).hexdigest(), **audit(data)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
