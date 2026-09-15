"""Retain bounded preparation diagnostics; partial DXF output is never published."""

import hashlib
import json
from pathlib import Path

from app.cad_import.aoi_contracts import AoiRequest
from app.cad_import.aoi_policy import AoiPolicy


def log_tail(path: Path, limit: int) -> str:
    if not path.exists():
        return ""
    with path.open("rb") as stream:
        stream.seek(max(0, path.stat().st_size - limit))
        return stream.read(limit).decode("utf-8", errors="replace")


def retain_aoi_failure(
    root: Path,
    request: AoiRequest,
    log: Path,
    policy: AoiPolicy,
    message: str,
) -> Path:
    content = {
        "status": "rejected",
        "message": message,
        "request": request.model_dump(mode="json"),
        "memory_budget_bytes": policy.max_memory_bytes,
        "timeout_seconds": policy.timeout_seconds,
        "log_tail": log_tail(log, policy.max_log_bytes),
    }
    encoded = json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
    folder = root / "failures"
    folder.mkdir(exist_ok=True)
    path = folder / f"{hashlib.sha256(encoded).hexdigest()}.json"
    path.write_bytes(encoded)
    return path
