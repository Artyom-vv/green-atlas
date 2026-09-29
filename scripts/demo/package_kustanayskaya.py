"""Package the verified Kustanayskaya CAD source without generated snapshots.

Only the ten original CAD files from the fixture manifest are included. This
script does not parse or modify their geometry. It also checks the ZIP entries
after writing, including Unicode paths and file hashes.
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "fixtures/cad/kustanayskaya/manifest.json"
SOURCE = ROOT / ".runtime/kustanayskaya-demo/Кустанайская"
OUTPUT = ROOT / "submission/prototype/Kustanayskaya-CAD.zip"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items = manifest["files"]
    verified: list[tuple[str, bytes]] = []
    for item in items:
        relative = Path(item["path"]).relative_to("source")
        source = SOURCE / relative
        if not source.is_file():
            raise FileNotFoundError(source)
        data = source.read_bytes()
        if len(data) != item["bytes"] or sha256(data) != item["sha256"]:
            raise ValueError(f"CAD checksum mismatch: {relative}")
        verified.append((f"Кустанайская/{relative.as_posix()}", data))

    temporary = OUTPUT.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=False) as archive:
        for name, data in verified:
            archive.writestr(name, data)
    with zipfile.ZipFile(temporary) as archive:
        if archive.namelist() != [name for name, _ in verified]:
            raise ValueError("Archive directory differs from verified source")
        for name, data in verified:
            if archive.read(name) != data:
                raise ValueError(f"Archive verification failed: {name}")
    temporary.replace(OUTPUT)
    print(f"{OUTPUT}: {len(verified)} verified CAD files, SHA-256 {sha256(OUTPUT.read_bytes())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
