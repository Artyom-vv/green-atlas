"""Mac-runnable contract checks of the Windows124 QA adapter, no installation."""
import json
import sys
from pathlib import Path

import pytest

from scripts.desktop import windows_upgrade124_smoke as smoke


def test_previous_installer_is_the_published124_pin():
    assert smoke.PREVIOUS == {
        "version": "0.1.124",
        "asset": "GreenAtlas-0.1.124-Windows-x64-Setup-2.exe",
        "sha256": "39d5c4388b4a27ef06bb092d59aa8342c33f1cf17d48ad64207a5bf3321c5c51",
        "release_tag": "v0.1.124-windows-candidate-39d5c438",
        "source_commit": "845b94a617efd9b3a1cd8a84e47237d169185bbe",
    }


@pytest.mark.parametrize("create,phases", [
    (True, ["previous121"]), (False, ["updated122", "restarted122"]),
])
def test_adapter_preserves_probe_arguments_and_only_relabels_evidence(monkeypatch, create, phases):
    smoke.prepared.PHASES.clear()
    calls = []
    expected = object()
    def probe(package, data, environment, *, create):
        calls.append((package, data, environment, create))
        smoke.prepared.PHASES.extend({"phase": phase, "objects": 3} for phase in phases)
        return expected
    monkeypatch.setattr(smoke.prepared, "prepared_project_probe", probe)
    assert smoke.prepared_project_probe("package", "data", "env", create=create) is expected
    assert calls == [("package", "data", "env", create)]
    assert smoke.prepared.PHASES == [
        {"phase": smoke.PHASE_NAMES[phase], "objects": 3} for phase in phases
    ]
    smoke.prepared.PHASES.clear()


@pytest.mark.parametrize("code,objects,undo,expected", [(0, 3, True, 0), (0, 0, True, 1),
    (0, 3, False, 1), (2, 3, True, 2)])
def test_adapter_never_marks_incomplete_or_failed_lifecycle_passed(tmp_path, monkeypatch,
    code, objects, undo, expected):
    monkeypatch.setattr(sys, "argv", ["upgrade124", "--output", str(tmp_path)])
    def upgrade(**options):
        assert options["upgrade_control"] == smoke.PREVIOUS
        assert options["project_probe_fn"] is smoke.prepared_project_probe
        smoke.prepared.PHASES.extend({"phase": phase, "objects": objects,
            "undo_redo_passed": undo} for phase in smoke.PHASE_NAMES.values())
        (tmp_path / "install-ci-receipt.json").write_text(json.dumps({"passed": code == 0}))
        return code
    monkeypatch.setattr(smoke.upgrade, "main", upgrade)
    assert smoke.main() == expected
    report = json.loads((tmp_path / "install-ci-receipt.json").read_text())
    assert report["passed"] is (expected == 0)
    assert report["synthetic_prepared_project_upgrade_tested"] is (expected == 0)
    assert report["old_executable_created_plantings"] is (expected == 0)
    assert report["real_prepared_cad_project_migration_tested"] is False
    assert report["previous_source_commit"] == smoke.PREVIOUS["source_commit"]
    smoke.prepared.PHASES.clear()


def test_local_harness_allows_exact_adapter_without_loosening_scope():
    root = Path(__file__).resolve().parents[2]
    text = (root / "tools/autocad-bridge/local-windows-product-install.ps1").read_text(encoding="utf-8-sig")
    assert "(Join-Path $repository 'scripts/desktop/windows_upgrade124_smoke.py')" in text
    assert "if (-not $LocalOwnerApproved)" in text
    assert "HelperScript -notin" in text
    assert "require_windows_user" in (
        root / "scripts/desktop/windows_upgrade_smoke.py"
    ).read_text(encoding="utf-8")
