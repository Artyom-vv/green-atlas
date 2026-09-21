from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "api"))
sys.path.insert(0, str(ROOT / "apps" / "api" / "tests"))

from app.cad_bridge import CadSnapshot
from test_cad_bridge_compiler import valid_probe


def test_default_output_is_adjacent_to_the_probe_source(tmp_path: Path) -> None:
    source = tmp_path / "street.dxf"
    probe = valid_probe()
    probe["source"]["path"] = str(source)
    probe_path = tmp_path / "raw-regions.json"
    probe_path.write_text(json.dumps(probe), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "cad-lab" / "compile_autocad_region_probe.py"),
            str(probe_path),
            "--autocad-version",
            "2027.0.1",
            "--target",
            "macos-arm64",
        ],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "apps" / "api")},
        capture_output=True,
        text=True,
        check=True,
    )

    output = source.with_name(f"{source.name}.green-atlas.snapshot.json")
    snapshot = CadSnapshot.model_validate_json(output.read_bytes())
    receipt = json.loads(result.stdout)
    assert receipt["output"] == str(output)
    assert receipt["payload_sha256"] == snapshot.summary.payload_sha256
