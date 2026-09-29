"""Verify the files shipped with this presentation package."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parent
manifest = json.loads((root / "manifest.json").read_text())
for record in manifest["files"]:
    path = root / record["path"]
    if not path.is_file():
        raise SystemExit(f"Missing: {record['path']}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != record["sha256"] or path.stat().st_size != record["bytes"]:
        raise SystemExit(f"Changed: {record['path']}")
print(f"Verified {len(manifest['files'])} files")
