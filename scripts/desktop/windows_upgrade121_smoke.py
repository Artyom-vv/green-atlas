"""Pinned released121 to immutable122, with a synthetic prepared planting plan."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps/api"))
sys.path.insert(0, str(REPO / "apps/api/tests"))

from scripts.desktop import windows_upgrade_smoke as upgrade
from scripts.desktop.windows_product_install_smoke import verify_web_http
from scripts.desktop.windows_qualification import ready_record, wait_clean_exit
from scripts.desktop.windows_wizard_smoke import require

PREVIOUS = {
    "version": "0.1.121",
    "asset": "GreenAtlas-0.1.121-Windows-x64-Setup-2.exe",
    "sha256": "b79b86ce4f2ba83d14d77ac3d5fe2b363f13f42b1a833ba1220b645b19a724ef",
}
PHASES = []
EXPECTED = {}


def canonical(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def probe(package, data, environment, phase):
    from app.shared.windows_process import launch_owned
    from test_desktop_project_lifecycle import immutable_sources

    windows = package / "Contents/Windows"
    child = launch_owned(
        [
            str(windows / "Runtime/GreenAtlasRuntime.exe"),
            "--data-dir",
            str(data),
            "--web-dir",
            str(windows / "Web"),
        ],
        cwd=windows,
        environment=environment,
    )
    try:
        ready = ready_record(child)
        opener = build_opener(ProxyHandler({}))

        def api(path, payload=None):
            request = Request(
                str(ready["origin"]) + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={
                    "X-Green-Atlas-Local-Session": str(ready["session_token"]),
                    "Content-Type": "application/json",
                },
            )
            with opener.open(request, timeout=90) as response:
                return json.load(response)

        web = verify_web_http(
            str(ready["origin"]), str(ready["session_token"]), windows / "Web"
        )
        projects = api("/api/projects")
        require(len(projects) == 1, "Synthetic project catalogue changed")
        path = "/api/projects/" + projects[0]["id"]
        project = api(path)
        require(
            project["map_ready"] and project["prepared_state"] == "available",
            "Prepared fixture unavailable",
        )
        if phase == "previous121":
            require(
                api(path + "/plan/placement-check", {"kind": "tree", "x": 60, "y": 50})[
                    "allowed"
                ],
                "Positive placement failed",
            )
            preview = api(
                path + "/plan/automatic/preview",
                {
                    "base_plan_version": project["plan"]["version"],
                    "zone_id": "work",
                    "near": "area",
                    "plant_kind": "auto",
                    "target_count": 3,
                },
            )
            require(
                preview["found"] > 0 and preview["change_set"]["can_apply"],
                "No applicable positive preview",
            )
            change = preview["change_set"]
            saved = api(
                path + "/plan/change-sets/apply",
                {
                    "preview_id": change["id"],
                    "digest": change["digest"],
                    "base_plan_version": change["base_plan_version"],
                },
            )
            EXPECTED.update(
                objects=saved["plan"]["objects"],
                change_id=change["id"],
                project_id=project["id"],
            )
            require(
                len(EXPECTED["objects"]) == preview["found"],
                "Not all preview plants saved",
            )
        else:
            require(
                project["id"] == EXPECTED["project_id"]
                and project["plan"]["objects"] == EXPECTED["objects"],
                "Saved plants changed during upgrade/restart",
            )
        require(
            api(path + "/plan/history")["undo_target_id"] == EXPECTED["change_id"],
            "Undo target changed",
        )
        require(
            api(path + "/plan/history/undo", {})["plan"]["objects"] == [], "Undo failed"
        )
        require(
            api(path + "/plan/history/redo", {})["plan"]["objects"]
            == EXPECTED["objects"],
            "Redo failed",
        )
        result = api(path) if phase == "previous121" else project
        require(
            immutable_sources(data) == EXPECTED["sources"],
            "Authoritative prepared source changed",
        )
        require(
            api("/_desktop/shutdown", {}) == {"state": "stopping"},
            "No graceful shutdown",
        )
        shutdown = wait_clean_exit(
            child, timeout=20, label="Synthetic prepared lifecycle " + phase
        )
        PHASES.append(
            {
                "phase": phase,
                "objects": len(EXPECTED["objects"]),
                "objects_sha256": canonical(EXPECTED["objects"]),
                "prepared_source_sha256": canonical(EXPECTED["sources"]),
                "undo_redo_passed": True,
                "shutdown": shutdown,
            }
        )
        return result, web, shutdown
    finally:
        child.close()


def prepared_project_probe(package, data, environment, *, create):
    if create:
        from test_desktop_project_lifecycle import immutable_sources, seed_project

        # Explicit synthetic fixture, never a user profile or CAD-parser fallback.
        fixture = Path(environment["TEMP"]) / "prepared-upgrade-fixture"
        seed_project(fixture)
        require(not (data / "projects.sqlite3").exists(), "Need an empty owned profile")
        shutil.copytree(fixture, data, dirs_exist_ok=True)
        EXPECTED["sources"] = immutable_sources(data)
        require(EXPECTED["sources"], "Missing immutable source fixture")
        return probe(package, data, environment, "previous121")
    result = probe(package, data, environment, "updated122")
    probe(package, data, environment, "restarted122")
    return result


def main():
    code = upgrade.main(
        upgrade_control=PREVIOUS, project_probe_fn=prepared_project_probe
    )
    output = Path(sys.argv[sys.argv.index("--output") + 1]) / "install-ci-receipt.json"
    report = json.loads(output.read_text())
    report.update(
        synthetic_prepared_lifecycle=PHASES,
        synthetic_prepared_project_upgrade_tested=code == 0,
        old_executable_created_plantings=code == 0,
        real_prepared_cad_project_migration_tested=False,
    )
    if code == 0:
        require(
            len(PHASES) == 3 and all(p["objects"] > 0 for p in PHASES),
            "Incomplete positive lifecycle",
        )
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
