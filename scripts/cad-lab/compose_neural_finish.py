"""Prepare masks and safely composite an optional neural appearance pass.

The model may return an arbitrary full-frame image.  Only pixels admitted by
the renderer class masks are copied into the deterministic base render.  This
keeps camera, silhouettes, roads, curbs and every other protected pixel byte
identical to the renderer output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any


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
    width, height = run(
        [magick, "identify", "-format", "%w %h", str(path)], capture=True
    ).split()
    return int(width), int(height)


def image_ref(path: Path) -> dict[str, Any]:
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def changed_pixels(magick: str, first: Path, second: Path) -> int:
    result = subprocess.run(
        [magick, "compare", "-metric", "AE", str(first), str(second), "null:"],
        text=True,
        capture_output=True,
    )
    metric = (result.stderr or result.stdout).strip().split()[0]
    return int(float(metric))


def build_contract(base: Path, masks: list[Path], output: Path,
                   request: Path | None = None, erosion_px: int = 1,
                   feather_px: float = 0.0) -> dict[str, Any]:
    magick = shutil.which("magick")
    if not magick:
        raise RuntimeError("ImageMagick `magick` is required")
    if not masks:
        raise ValueError("At least one renderer class mask is required")
    if erosion_px < 0:
        raise ValueError("erosion_px must not be negative")
    if feather_px < 0:
        raise ValueError("feather_px must not be negative")
    base_size = dimensions(magick, base)
    for mask in masks:
        if dimensions(magick, mask) != base_size:
            raise ValueError(f"Mask dimensions differ from base image: {mask}")
    output.mkdir(parents=True, exist_ok=True)
    normalized = []
    support_masks = []
    for index, source in enumerate(masks):
        support = output / f"editable-support-class-{index + 1:02d}.png"
        run([
            magick, str(source), "-colorspace", "Gray", "-threshold", "50%",
            "-type", "Bilevel", *STABLE_PNG, str(support),
        ])
        support_masks.append(support)
        target = output / f"editable-class-{index + 1:02d}.png"
        morphology = ["-morphology", "Erode", f"Disk:{erosion_px}"] if erosion_px else []
        if feather_px:
            # Blur an eroded copy, then clip it by the original binary support.
            # The alpha ramp therefore exists only inside the admitted class;
            # every outside/protected pixel remains exactly zero.
            run([
                magick, str(support), *morphology, "-blur", f"0x{feather_px}",
                str(support), "-compose", "Multiply", "-composite",
                *STABLE_PNG, str(target),
            ])
        else:
            run([
                magick, str(support), *morphology, "-type", "Bilevel",
                *STABLE_PNG, str(target),
            ])
        normalized.append(target)
    editable = output / "editable-mask.png"
    run([
        magick, *map(str, normalized), "-evaluate-sequence", "Max",
        "-type", "Bilevel", *STABLE_PNG, str(editable),
    ])
    protected = output / "protected-mask.png"
    editable_support = output / "editable-support-mask.png"
    run([
        magick, *map(str, support_masks), "-evaluate-sequence", "Max",
        "-type", "Bilevel", *STABLE_PNG, str(editable_support),
    ])
    run([
        magick, str(editable_support), "-negate", "-type", "Bilevel",
        *STABLE_PNG, str(protected),
    ])
    editable_fraction = float(run(
        [magick, str(editable), "-format", "%[fx:mean]", "info:"], capture=True
    ))
    contract = {
        "schema": "green-atlas.masked-neural-composite-contract.v1",
        "status": "prepared_candidate_pending",
        "base_image": {**image_ref(base), "dimensions": list(base_size)},
        "source_masks": [image_ref(path) for path in masks],
        "editable_mask": image_ref(editable),
        "editable_support_mask": image_ref(editable_support),
        "protected_mask": image_ref(protected),
        "editable_pixel_fraction": round(editable_fraction, 9),
        "editable_edge_erosion_px": erosion_px,
        "editable_inner_feather_px": feather_px,
        "rules": {
            "model_output_is_geometry_authority": False,
            "copy_candidate_only_inside_editable_mask": True,
            "protected_pixels_must_be_byte_identical": True,
            "manual_rectangle_required": False,
        },
    }
    if request:
        contract["neural_finish_request"] = image_ref(request)
    contract_path = output / "composite-contract.json"
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return contract


def composite_candidate(contract: dict[str, Any], candidate: Path, output: Path,
                        model_id: str, model_revision: str, seed: int) -> dict[str, Any]:
    magick = shutil.which("magick")
    assert magick
    base = Path(contract["base_image"]["path"])
    editable = Path(contract["editable_mask"]["path"])
    editable_support = Path(contract["editable_support_mask"]["path"])
    if dimensions(magick, candidate) != tuple(contract["base_image"]["dimensions"]):
        raise ValueError("Candidate dimensions differ from base image")
    final = output / "neural-finish-controlled.png"
    # ImageMagick mask compositing selects candidate where mask is white and
    # base where it is black.  No generated pixel can escape the class masks.
    run([
        magick, str(base), str(candidate), str(editable), "-composite",
        *STABLE_PNG, str(final),
    ])
    with tempfile.TemporaryDirectory(prefix="green-atlas-protected-check-") as temporary:
        normalized = Path(temporary) / "protected-only.png"
        # Replace editable pixels in final with base, leaving only protected
        # pixels available for a byte-level comparison.
        run([magick, str(final), str(base), str(editable_support), "-composite", str(normalized)])
        changed = changed_pixels(magick, base, normalized)
    if changed:
        raise ValueError(f"Controlled composite changed {changed} protected pixels")
    metadata_complete = seed >= 0 and not model_revision.startswith("unreported")
    result = {
        "schema": "green-atlas.masked-neural-composite-result.v1",
        "status": "passed",
        "contract_sha256": sha256(output / "composite-contract.json"),
        "candidate": image_ref(candidate),
        "final": image_ref(final),
        "model": {
            "id": model_id,
            "revision": model_revision,
            "seed": seed if seed >= 0 else None,
            "seed_status": "reported" if seed >= 0 else "unreported_by_tool",
        },
        "generation_reproducibility": "complete" if metadata_complete else "incomplete_tool_metadata",
        "protected_changed_pixels": 0,
    }
    (output / "composite-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--mask", type=Path, action="append", required=True)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--erosion-px", type=int, default=1)
    parser.add_argument("--feather-px", type=float, default=0.0)
    parser.add_argument("--model-id")
    parser.add_argument("--model-revision")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    contract = build_contract(
        args.base.resolve(), [path.resolve() for path in args.mask], output,
        args.request.resolve() if args.request else None, args.erosion_px, args.feather_px,
    )
    result: dict[str, Any] = {"contract": contract}
    if args.candidate:
        missing = [name for name, value in (
            ("model-id", args.model_id), ("model-revision", args.model_revision), ("seed", args.seed)
        ) if value is None or value == ""]
        if missing:
            raise ValueError("Candidate metadata required: " + ", ".join(missing))
        result["result"] = composite_candidate(
            contract, args.candidate.resolve(), output,
            str(args.model_id), str(args.model_revision), int(args.seed),
        )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
