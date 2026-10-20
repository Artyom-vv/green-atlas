"""Pinned released124 -> current wizard, reusing the prepared-project lifecycle.

This is a QA entry point for an isolated ordinary Windows account. It does not
weaken source seals, SID checks, child ownership or the underlying installer.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps/api"))
sys.path.insert(0, str(REPO / "apps/api/tests"))

from scripts.desktop import windows_upgrade121_smoke as prepared
from scripts.desktop import windows_upgrade_smoke as upgrade

PREVIOUS = {
    "version": "0.1.124",
    "asset": "GreenAtlas-0.1.124-Windows-x64-Setup-2.exe",
    "sha256": "39d5c4388b4a27ef06bb092d59aa8342c33f1cf17d48ad64207a5bf3321c5c51",
    "release_tag": "v0.1.124-windows-candidate-39d5c438",
    "source_commit": "845b94a617efd9b3a1cd8a84e47237d169185bbe",
}
PHASE_NAMES = {
    "previous121": "previous124",
    "updated122": "updated-current",
    "restarted122": "restarted-current",
}


def prepared_project_probe(package, data, environment, *, create):
    """Keep the original positive fixture, writes, checks and restart unchanged."""
    start = len(prepared.PHASES)
    try:
        return prepared.prepared_project_probe(
            package, data, environment, create=create
        )
    finally:
        for phase in prepared.PHASES[start:]:
            phase["phase"] = PHASE_NAMES[phase["phase"]]


def main():
    prepared.PHASES.clear()
    prepared.EXPECTED.clear()
    code = upgrade.main(
        upgrade_control=PREVIOUS, project_probe_fn=prepared_project_probe
    )
    output = Path(sys.argv[sys.argv.index("--output") + 1]) / "install-ci-receipt.json"
    report = json.loads(output.read_text(encoding="utf-8"))
    phases = prepared.PHASES
    complete = (
        code == 0
        and report.get("passed") is True
        and [phase.get("phase") for phase in phases] == list(PHASE_NAMES.values())
        and all(
            phase.get("objects", 0) > 0
            and phase.get("undo_redo_passed") is True
            for phase in phases
        )
    )
    report.update(
        synthetic_prepared_lifecycle=phases,
        synthetic_prepared_project_upgrade_tested=complete,
        old_executable_created_plantings=complete,
        real_prepared_cad_project_migration_tested=False,
        previous_release_tag=PREVIOUS["release_tag"],
        previous_source_commit=PREVIOUS["source_commit"],
    )
    if code == 0 and not complete:
        report.update(passed=False, error="Incomplete positive prepared124 lifecycle")
        code = 1
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
