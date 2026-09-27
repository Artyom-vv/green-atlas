"""Keep a bounded failed conversion for diagnosis without publishing it as valid."""

import json
from pathlib import Path


def retain_failure(folder: Path, work: Path, message: str) -> Path:
    for name in ("drawing.dxf", "converter.log", "inspection.log"):
        candidate = work / name
        if candidate.is_file():
            candidate.replace(folder / f"rejected-{name}")
    manifest = folder / "failure.json"
    pending = work / "failure.json"
    pending.write_text(
        json.dumps(
            {"status": "rejected", "message": message}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    pending.replace(manifest)
    return manifest
