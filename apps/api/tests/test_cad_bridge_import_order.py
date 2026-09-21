"""Public contracts must load independently of provider import order."""

import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize(
    "first", ["app.dxf_import.contracts", "app.cad_bridge.provider", "app.contracts"]
)
def test_bridge_contracts_and_public_exports_load_in_fresh_process(first):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            f"import {first}; "
            "from app.cad_bridge import CadSnapshotProviderError, verify_cad_snapshot_integrity; "
            "from app.dxf_import.contracts import DxfImportResult",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
