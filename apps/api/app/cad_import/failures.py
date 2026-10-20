"""Ограниченные материалы сбоя CAD-конвертации для диагностики; ошибочный результат не
публикуется как пригодный.
"""

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
