#!/usr/bin/env python3
"""Build a deterministic JSON request and prompt for a masked neural finish.

The scene packet remains the authority for geometry and semantics.  This tool
turns source-backed scene facts into one of a small number of fixed rendering
scenarios.  It deliberately protects hard surfaces in the default production
scenario so a generative model cannot add the high-frequency road/grass noise
seen in whole-frame experiments.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCENE = ROOT / ".runtime/kustanayskaya-controlled-scene-20260920/scene-packet.json"
DEFAULT_RENDER = ROOT / ".runtime/kustanayskaya-controlled-scene-20260920/render-v3/street-geometry-prototype.png"
DEFAULT_SEMANTIC = ROOT / ".runtime/kustanayskaya-controlled-scene-20260920/render-v3/street-semantic-diagnostic.png"
DEFAULT_OUTPUT = ROOT / ".runtime/kustanayskaya-controlled-scene-20260920/neural-finish-request-v2"


SCENARIOS: dict[str, dict[str, Any]] = {
    "facade_vegetation": {
        "description": "Production default: improve facades and admitted vegetation only.",
        "editable_classes": ["building", "vegetation"],
        "protected_classes": ["road", "sidewalk", "lawn", "curb", "marking", "street_furniture", "unknown"],
        "surface_policy": "pixel_protected_deterministic_renderer",
        "human_review_required": False,
    },
    "facade_only": {
        "description": "Improve facade appearance while keeping vegetation and every surface unchanged.",
        "editable_classes": ["building"],
        "protected_classes": ["vegetation", "road", "sidewalk", "lawn", "curb", "marking", "street_furniture", "unknown"],
        "surface_policy": "pixel_protected_deterministic_renderer",
        "human_review_required": False,
    },
    "supervised_full_finish": {
        "description": "Review mode: allow material finish on all admitted classes; requires a person to accept the result.",
        "editable_classes": ["building", "vegetation", "road", "sidewalk", "lawn", "curb"],
        "protected_classes": ["marking", "street_furniture", "unknown"],
        "surface_policy": "masked_material_edit_with_geometry_validation",
        "human_review_required": True,
    },
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_ref(path: Path) -> dict[str, Any]:
    path = path.resolve()
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size}


def load_world(scene: dict[str, Any]) -> dict[str, Any]:
    path = Path(scene["source"]["world"])
    if sha256(path) != scene["source"]["world_sha256"]:
        raise ValueError("World packet hash differs from the scene-packet receipt")
    return json.loads(path.read_text())


def vegetation_facts(scene: dict[str, Any], world: dict[str, Any]) -> dict[str, Any]:
    source_by_id = {int(row["inventory_id"]): row for row in world["inventory_vegetation"]}
    records = []
    for row in scene["vegetation"]:
        source = source_by_id[int(row["inventory_id"])]
        records.append({
            "inventory_id": int(row["inventory_id"]),
            "species": row["source_species"],
            "asset_species": row["asset_species"],
            "height_m": float(row["height_m"]),
            "diameter_cm": source.get("diameter_cm"),
            "condition_description": row.get("condition_description"),
            "position_status": row["position_status"],
            "age_years": None,
            "maturity_rule": "visual class from measured height/diameter and recorded condition only",
        })
    species = Counter(row["species"] for row in records)
    return {
        "count": len(records),
        "species_counts": dict(sorted(species.items())),
        "height_range_m": [min(row["height_m"] for row in records), max(row["height_m"] for row in records)] if records else None,
        "age_policy": "unknown_not_inferred",
        "records": records,
    }


def building_facts(scene: dict[str, Any]) -> dict[str, Any]:
    classes = Counter((row.get("class") or "building") for row in scene["buildings"])
    height_status = Counter((row.get("height_status") or "unknown") for row in scene["buildings"])
    return {
        "count": len(scene["buildings"]),
        "class_counts": dict(sorted(classes.items())),
        "height_status_counts": dict(sorted(height_status.items())),
        "unknown_height_skipped": int(scene["audit"]["buildings_skipped"].get("unknown_height", 0)),
        "geometry_rule": "map footprint and admitted height own position and silhouette",
        "facade_rule": "appearance proxy inside the admitted silhouette; clean maintained occupied ordinary facade with only subtle broad tonal variation",
        "completeness_gate": {
            "required_before_lawn_or_planting_admission": True,
            "rule": "subtract every conflated map building footprint from candidate lawn and planting areas",
            "small_buildings_required": True,
            "reason": "a missing shop, kiosk or service building can falsely create plantable lawn",
        },
    }


def render_prompt(request: dict[str, Any]) -> str:
    scenario = request["scenario"]
    vegetation = request["facts"]["vegetation"]
    buildings = request["facts"]["buildings"]
    editable = ", ".join(scenario["editable_classes"])
    protected = ", ".join(scenario["protected_classes"])
    species = ", ".join(f"{name}: {count}" for name, count in vegetation["species_counts"].items()) or "none"
    return "\n".join([
        "Use case: masked photorealistic finish of a deterministic geospatial street render.",
        "The base render and semantic map are the sole geometry authority.",
        "No location-specific facade photograph or chat image is supplied. Use only generic class-appropriate appearance priors and the machine-readable scene facts.",
        f"Scenario: {request['scenario_id']}. Editable semantic classes: {editable}.",
        f"Protected semantic classes: {protected}. Protected pixels must remain identical to the base render.",
        "Do not change the camera, horizon, perspective, object position, footprint, height, curb, path, road or lawn boundary.",
        f"Admitted vegetation: {vegetation['count']} records; {species}. Preserve every admitted trunk position and measured height.",
        "Tree age in years is absent and must not be invented. Use only maturity implied by measured height/diameter and condition.",
        f"Admitted buildings: {buildings['count']}; types {json.dumps(buildings['class_counts'], ensure_ascii=False, sort_keys=True)}.",
        "Facade appearance must look clean, maintained, occupied and ordinary, with only subtle broad tonal unevenness. No grime streaks, damaged concrete, patchwork repairs, abandoned surfaces, boarded openings or post-apocalyptic/game-map styling.",
        "Facade editing is texture-only: do not create entrances, stairs, ramps, porches, canopies, balconies, projections, recesses or openings that are absent from the base render.",
        "Glazing is an opaque appearance proxy: do not depict reflected buildings, trees, cars, people or sky. Use only subtle generic dark tonal shading and soft non-specific highlights.",
        "Road, sidewalk, curb and lawn are deterministic renderer output in the production scenario. Do not redraw their texture.",
        "Avoid high-frequency microcontrast, texture tiling, embossed patterns, floating leaves, leaf-card halos, oversharpening and noisy shadows.",
        "Do not add cars, people, signs, poles, wires, fences, shrubs, buildings, skyline or any object absent from the semantic scene.",
        "Unknown space stays neutral and empty. Do not fill it for composition.",
        "Use natural Moscow summer daylight and neutral photographic color without bloom, fog, vignette, depth of field or cinematic grading.",
        "Return a lossless image with the same dimensions as the base render.",
    ]) + "\n"


def build_request(scene_path: Path, render_path: Path, semantic_path: Path,
                  scenario_id: str, references: list[Path],
                  masks: dict[str, Path] | None = None) -> dict[str, Any]:
    if scenario_id not in SCENARIOS:
        raise ValueError(f"Unknown scenario: {scenario_id}")
    if references and scenario_id != "supervised_full_finish":
        raise ValueError(
            "Production scenarios reject external appearance references. "
            "Only scene/world data and renderer outputs may shape the result."
        )
    scene = json.loads(scene_path.read_text())
    world = load_world(scene)
    scenario = SCENARIOS[scenario_id]
    masks = masks or {}
    required_masks = set(scenario["editable_classes"])
    admitted_masks = {
        name: source_ref(path) for name, path in sorted(masks.items())
        if name in required_masks and path.is_file()
    }
    request = {
        "schema": "green-atlas.neural-finish-request.v2",
        "status": (
            "ready_for_masked_model_adapter"
            if required_masks <= set(admitted_masks) else "spec_ready_renderer_masks_pending"
        ),
        "scene_id": scene["scene_id"],
        "scenario_id": scenario_id,
        "scenario": scenario,
        "inputs": {
            "scene_packet": source_ref(scene_path),
            "base_render": source_ref(render_path),
            "semantic_render": source_ref(semantic_path),
            "references": [source_ref(path) for path in references],
            "reference_policy": (
                "human_supervised_external_references"
                if references else "no_external_or_chat_context_references"
            ),
            "editable_masks": admitted_masks,
        },
        "facts": {
            "vegetation": vegetation_facts(scene, world),
            "buildings": building_facts(scene),
            "surfaces": {
                "area_m2": {key: float(value["area_m2"]) for key, value in scene["audit"]["surface_stats"].items()},
                "lawn_type": "project-authored restored/maintained municipal lawn; seed mixture unknown",
                "hard_surface_finish": "deterministic renderer; neural redraw forbidden in production scenario",
            },
            "vehicles": {"count": 0, "reason": "no source-backed vehicle positions"},
            "optional_clutter": {
                "render": False,
                "excluded": ["utility poles", "overhead wires", "traffic signs", "incidental parked cars"],
                "always_keep_when_admitted": ["bench", "waste basket", "transit shelter"],
            },
        },
        "validation": {
            "same_dimensions": True,
            "protected_pixels_identical": True,
            "semantic_boundary_drift_px_max": 0,
            "whole_frame_regeneration": False,
            "manual_acceptance": bool(scenario["human_review_required"]),
        },
    }
    request["prompt"] = render_prompt(request)
    return request


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scene", type=Path, default=DEFAULT_SCENE)
    parser.add_argument("--render", type=Path, default=DEFAULT_RENDER)
    parser.add_argument("--semantic", type=Path, default=DEFAULT_SEMANTIC)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="facade_vegetation")
    parser.add_argument("--reference", type=Path, action="append", default=[])
    parser.add_argument("--building-mask", type=Path)
    parser.add_argument("--vegetation-mask", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    automatic_masks = {
        "building": args.building_mask.resolve() if args.building_mask else args.render.resolve().parent / "mask-building.png",
        "vegetation": args.vegetation_mask.resolve() if args.vegetation_mask else args.render.resolve().parent / "mask-vegetation.png",
    }
    request = build_request(
        args.scene.resolve(), args.render.resolve(), args.semantic.resolve(),
        args.scenario, [path.resolve() for path in args.reference], automatic_masks,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    request_path = args.output / "neural-finish-request.json"
    prompt_path = args.output / "prompt.txt"
    request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    prompt_path.write_text(request["prompt"])
    print(json.dumps({
        "request": str(request_path.resolve()),
        "prompt": str(prompt_path.resolve()),
        "scenario": args.scenario,
        "editable_classes": request["scenario"]["editable_classes"],
        "protected_classes": request["scenario"]["protected_classes"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
