"""Проверка исходного seal до дорогостоящей сборки, без CAD и готового payload."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[3] / "scripts/desktop/package_preflight.py"


@pytest.mark.parametrize("damage", [None, "bytes", "missing", "duplicate"])
def test_check_source_reports_only_verified_receipt(tmp_path, damage):
    root = tmp_path / "Исходники"
    root.mkdir()
    file = root / "module.py"
    file.write_bytes(b"VALUE = 42\n")
    row = {"path": file.name, "bytes": file.stat().st_size,
           "sha256": hashlib.sha256(file.read_bytes()).hexdigest()}
    manifest = tmp_path / "source-manifest.json"
    manifest.write_text(json.dumps({"files": [row, row] if damage == "duplicate" else [row]}))
    if damage == "bytes":
        file.write_bytes(b"VALUE = 43\n")
    elif damage == "missing":
        file.unlink()
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    result = subprocess.run(
        [sys.executable, str(CLI), "check-source", "--source-root", str(root),
         "--source-manifest", str(manifest)], capture_output=True, text=True,
        encoding="utf-8", timeout=15,
    )
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before
    if damage:
        assert result.returncode == 1 and not result.stdout
        assert json.loads(result.stderr)["payload_valid"] is False
    else:
        assert result.returncode == 0 and not result.stderr
        assert json.loads(result.stdout) == {
            "source_valid": True, "verified_files": 1,
            "source_manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "windows_execution_qualified": False,
        }
