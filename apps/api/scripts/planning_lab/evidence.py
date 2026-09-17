"""Canonical identity and atomic, non-overwriting publication of lab evidence."""

import json
import os
import platform
import tempfile
from hashlib import sha256
from importlib.metadata import version
from pathlib import Path

from shapely import geos_version_string

from app.regulations.profiles import requirement_profile
from app.regulations.registry import registry_snapshot
from app.species.catalog import list_species


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(value: object) -> str:
    return sha256(canonical_bytes(value)).hexdigest()


def implementation_basis() -> dict:
    api_root = Path(__file__).resolve().parents[2]
    files = sorted(
        [*(api_root / "app").rglob("*.py"), *Path(__file__).parent.glob("*.py")]
    )
    # Hash actual working-tree code, including uncommitted edits. Normalize
    # checkout line endings so a Windows clone and a Mac clone agree.
    sources = {
        path.relative_to(api_root).as_posix(): sha256(
            path.read_bytes().replace(b"\r\n", b"\n")
        ).hexdigest()
        for path in files
    }
    return {
        "source_files": sources,
        "source_tree_sha256": digest(sources),
        "runtime": {
            "python": platform.python_version(),
            "geos": geos_version_string,
            **{name: version(name) for name in ("shapely", "pydantic", "ezdxf")},
        },
        "requirement_profile": requirement_profile().model_dump(mode="json"),
        "registry": registry_snapshot([]),
        "species_catalog": [item.model_dump(mode="json") for item in list_species()],
    }


def publish_new(path: Path, value: dict) -> None:
    """Publish complete bytes or nothing; never replace an existing report."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".planning-", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        # Same-volume hard link is atomic and fails if the destination exists.
        # Unlike replace(), it cannot silently overwrite previous evidence.
        os.link(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
