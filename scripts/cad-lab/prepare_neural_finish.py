"""Prepare and validate a geometry-preserving optional neural finish.

The tool does not run a model. It derives an editable image mask from the
renderer class masks and rejects a candidate image if any protected pixel
differs from the deterministic beauty review.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any


CLASSES = ("road", "sidewalk", "lawn")
STABLE_PNG = ["-strip", "-define", "png:exclude-chunks=date,time"]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(command, check=True, text=True, capture_output=capture)
    return (result.stdout or "").strip()


def dimensions(magick: str, path: Path) -> tuple[int, int]:
    value = run([magick, "identify", "-format", "%w %h", str(path)], capture=True)
    width, height = value.split()
    return int(width), int(height)


def receipt_outputs(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {Path(row["path"]).name: row for row in receipt["outputs"]}


def verify_receipt_file(path: Path, outputs: dict[str, dict[str, Any]]) -> None:
    expected = outputs.get(path.name)
    if not expected:
        raise ValueError(f"File is absent from render receipt: {path.name}")
    if path.stat().st_size != expected["bytes"] or sha256(path) != expected["sha256"]:
        raise ValueError(f"Render output hash/size mismatch: {path}")


def prepare(render_dir: Path, output: Path, erosion_px: int | None) -> dict[str, Any]:
    magick = shutil.which("magick")
    if not magick:
        raise SystemExit("ImageMagick `magick` is required")
    receipt_path = render_dir / "render-receipt.json"
    receipt = json.loads(receipt_path.read_text())
    outputs = receipt_outputs(receipt)
    base = render_dir / "beauty-review.png"
    source_masks = [render_dir / f"neural-{name}-candidate-mask.png" for name in CLASSES]
    for path in [base, *source_masks]:
        verify_receipt_file(path, outputs)
    width, height = dimensions(magick, base)
    if any(dimensions(magick, path) != (width, height) for path in source_masks):
        raise ValueError("Base image and class masks have different dimensions")
    erosion = erosion_px if erosion_px is not None else max(1, round(min(width, height) / 160))
    if erosion < 1:
        raise ValueError("erosion-px must be positive")

    output.mkdir(parents=True, exist_ok=True)
    safe_masks = []
    for semantic_class, source in zip(CLASSES, source_masks):
        target = output / f"editable-{semantic_class}.png"
        run([
            magick, str(source), "-colorspace", "Gray", "-threshold", "50%",
            "-morphology", "Erode", f"Disk:{erosion}", "-type", "Bilevel", *STABLE_PNG, str(target),
        ])
        safe_masks.append(target)
    allowed = output / "editable-material-mask.png"
    run([magick, *map(str, safe_masks), "-evaluate-sequence", "Max", "-type", "Bilevel",
         *STABLE_PNG, str(allowed)])
    protected = output / "protected-geometry-mask.png"
    run([magick, str(allowed), "-negate", "-type", "Bilevel", *STABLE_PNG, str(protected)])
    allowed_fraction = float(run([magick, str(allowed), "-format", "%[fx:mean]", "info:"], capture=True))

    contract = {
        "schema": "green-atlas.neural-material-finish-contract.v1",
        "status": "prepared_model_not_run",
        "render_receipt": {"path": str(receipt_path), "sha256": sha256(receipt_path)},
        "scene_packet_sha256": receipt["scene_packet_sha256"],
        "base_image": {"path": str(base), "sha256": sha256(base), "dimensions": [width, height]},
        "editable_mask": {"path": str(allowed), "sha256": sha256(allowed)},
        "protected_mask": {"path": str(protected), "sha256": sha256(protected)},
        "editable_classes": list(CLASSES),
        "protected_content": [
            "unknown/background pixels",
            "existing tree silhouette",
            "semantic class boundaries",
            "all pixels outside rendered source-backed surfaces",
        ],
        "edge_erosion_px": erosion,
        "editable_pixel_fraction": round(allowed_fraction, 9),
        "candidate_requirements": {
            "format": "lossless PNG",
            "same_dimensions": True,
            "protected_pixels": "pixel-identical RGBA",
            "geometry_or_object_generation": False,
            "model_id_required": True,
            "model_revision_required": True,
            "seed_required": True,
            "scheduler_required": True,
        },
    }
    contract_path = output / "contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return contract


def validate_candidate(contract: dict[str, Any], candidate: Path, output: Path,
                       model_id: str | None, model_revision: str | None,
                       seed: int | None, scheduler: str | None) -> dict[str, Any]:
    metadata = {
        "model_id": model_id,
        "model_revision": model_revision,
        "seed": seed,
        "scheduler": scheduler,
    }
    missing = [key for key, value in metadata.items() if value is None or value == ""]
    if missing:
        raise ValueError("Candidate validation requires: " + ", ".join(missing))
    if candidate.suffix.lower() != ".png":
        raise ValueError("Candidate must be a lossless PNG")
    magick = shutil.which("magick")
    assert magick
    base = Path(contract["base_image"]["path"])
    allowed = Path(contract["editable_mask"]["path"])
    if dimensions(magick, candidate) != tuple(contract["base_image"]["dimensions"]):
        raise ValueError("Candidate dimensions differ from base image")

    # Replace candidate pixels inside the allowed mask with the base pixels.
    # Any remaining difference therefore lies in the protected region.
    with tempfile.TemporaryDirectory(prefix="green-atlas-neural-validate-") as temporary:
        normalized = Path(temporary) / "candidate-protected.png"
        run([magick, str(candidate), str(base), str(allowed), "-composite", str(normalized)])
        compared = subprocess.run(
            [magick, "compare", "-metric", "AE", str(base), str(normalized), "null:"],
            text=True, capture_output=True,
        )
        metric = (compared.stderr or compared.stdout).strip()
        # ImageMagick Q16 may report `65535 (1)` for one differing pixel.
        normalized = re.findall(r"\(([^)]+)\)", metric)
        protected_changed_pixels = int(float(normalized[-1] if normalized else metric.split()[0]))
    passed = protected_changed_pixels == 0
    result = {
        "schema": "green-atlas.neural-material-finish-validation.v1",
        "status": "passed" if passed else "rejected_protected_pixels_changed",
        "contract_sha256": sha256(output / "contract.json"),
        "candidate": {"path": str(candidate), "sha256": sha256(candidate)},
        "model": metadata,
        "protected_changed_pixels": protected_changed_pixels,
        "geometry_preservation_passed": passed,
    }
    (output / "validation.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    if not passed:
        raise ValueError(f"Candidate changed {protected_changed_pixels} protected pixels")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--erosion-px", type=int)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--model-id")
    parser.add_argument("--model-revision")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--scheduler")
    args = parser.parse_args()
    render_dir = args.render_dir.resolve()
    output = args.output.resolve() if args.output else render_dir / "neural-finish"
    contract = prepare(render_dir, output, args.erosion_px)
    result: dict[str, Any] = {"contract": contract}
    if args.candidate:
        result["validation"] = validate_candidate(
            contract, args.candidate.resolve(), output,
            args.model_id, args.model_revision, args.seed, args.scheduler,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
