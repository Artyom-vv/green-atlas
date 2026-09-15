"""Capture and verify a zone change applied through the UI; never sends a write."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from urllib.parse import urlencode

from evaluate_autonomous_runtime import call_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--stage", choices=["preview", "applied"], required=True)
    args = parser.parse_args()
    project_path = f"/api/projects/{args.project_id}"
    run_path = f"{project_path}/agent-runs/{args.run_id}"
    run = call_json(args.base_url, run_path)
    project = call_json(args.base_url, project_path)
    state = run["state"]
    assert state["project_id"] == args.project_id and state["run_id"] == args.run_id
    if args.stage == "preview":
        assert not args.evidence.exists(), "Use a new evidence file; keep the original capture"
        assert state["status"] == "waiting_approval"
        approval = state["pending_approval"]
        assert approval["kind"] == "planting_zones"
        preview = call_json(args.base_url, run_path + "/zone-preview?" + urlencode({"preview_ref": approval["preview_ref"]}))
        assert preview["project_id"] == args.project_id and preview["can_apply"]
        assert not preview["blockers"] and not preview["affected_planting_ids"]
        assert preview["before_zones"] == project["planting_zones"]
        assert preview["base_state_version"] == project["state_version"]
        assert preview["base_geometry_version"] == project["geometry_version"]
        assert preview["base_plan_version"] == (project.get("plan") or {}).get("version")
        assert not any(event["kind"] == "commit_applied" for event in run["events"])
        evidence = {"project_id": args.project_id, "run_id": args.run_id,
                    "before": project, "preview": preview, "preview_run": run}
    else:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        assert evidence["project_id"] == args.project_id and evidence["run_id"] == args.run_id
        assert "after" not in evidence, "This capture has already been verified"
        before, preview = evidence["before"], evidence["preview"]
        assert state["status"] == "finished" and not state.get("pending_approval")
        assert state["outcome_ref"] == f"zone-change:{preview['id']}"
        assert project["planting_zones"] == preview["after_zones"], "Applied contours differ from the approved preview"
        expected_plan = deepcopy(before.get("plan"))
        if expected_plan is not None:
            basis = expected_plan.get("validation_basis")
            assert basis is not None, "Use a current validation baseline; legacy random issue IDs cannot prove unchanged findings"
            assert basis["geometry_version"] == before["geometry_version"]
            # Validation explicitly records the new geometry revision. Every
            # object, issue ID/value and other plan field must remain identical.
            basis["geometry_version"] = project["geometry_version"]
        assert project.get("plan") == expected_plan, "Zone change modified plan objects or validation findings"
        assert project["state_version"] == before["state_version"] + 1
        assert project["geometry_version"] == before["geometry_version"] + 1
        commits = [event for event in run["events"] if event["kind"] == "commit_applied"]
        assert len(commits) == 1, "The run must have one verified commit event"
        receipt = commits[0]["payload"]
        assert receipt["kind"] == "planting_zones" and receipt["preview_id"] == preview["id"]
        assert receipt["digest"] == preview["digest"] and receipt["plantings_unchanged"] is True
        assert receipt["state_version"] == project["state_version"]
        assert receipt["geometry_version"] == project["geometry_version"]
        assert receipt["plan_version"] == (project.get("plan") or {}).get("version")
        evidence.update(after=project, applied_run=run, verified={
            "full_zones_match": True, "full_plan_except_validation_geometry_version_unchanged": True,
            "stable_issue_ids_and_values": True, "single_commit": True,
            "state_increment": 1, "geometry_increment": 1,
        })
    args.evidence.parent.mkdir(parents=True, exist_ok=True)
    args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pass": True, "stage": args.stage, "run_id": args.run_id,
                      "state_version": project["state_version"], "geometry_version": project["geometry_version"],
                      "plan_version": (project.get("plan") or {}).get("version"), "evidence": str(args.evidence)}, ensure_ascii=False))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
