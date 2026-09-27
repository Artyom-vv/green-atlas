"""Capture a real map control before/after UI execution without sending writes."""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from shapely.geometry import shape
from evaluate_autonomous_runtime import call_json


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def verify_completion(run, before, project):
    state = run["state"]
    assert state["status"] == "finished" and state["project_id"] == project["id"]
    assert project == before, "A camera command changed the project"
    command, receipt = state["control_command"], state["control_result"]
    assert command["project_id"] == project["id"] and command["run_id"] == state["run_id"]
    assert command["execution_attempt_id"] == state["execution_attempt_id"]
    assert state["intent"]["control"]["zone_id"] == command["zone_id"]
    assert state["intent"]["explicit_zone_ids"] == [command["zone_id"]]
    assert not state["intent"]["control"]["unsupported_requirements"]
    assert state["pending_approval"] is None and state["outcome_ref"] == f"control:{command['id']}"
    assert receipt["command_id"] == command["id"] and receipt["status"] == "completed" and receipt.get("error_code") is None
    for key in ("project_id", "run_id", "execution_attempt_id", "zone_id", "geometry_version", "geometry_digest"):
        assert receipt[key] == command[key]
    zones = [zone for zone in project["planting_zones"] if zone["id"] == command["zone_id"]]
    assert len(zones) == 1 and zones[0]["label"] == command["zone_label"]
    assert digest(zones[0]["geometry"]) == command["geometry_digest"]
    assert list(shape(zones[0]["geometry"]).bounds) == command["bounds"]
    assert command["state_version"] == project["state_version"] and command["geometry_version"] == project["geometry_version"]
    issued = [event for event in run["events"] if event["kind"] == "control_issued"]
    completed = [event for event in run["events"] if event["kind"] == "control_completed"]
    assert len(issued) == len(completed) == 1
    assert issued[0]["payload"]["command"] == command and completed[0]["payload"] == receipt
    assert not any(event["kind"] in {"commit_started", "commit_applied", "approval_requested"} for event in run["events"])
    prepared = [event["payload"] for event in run["events"] if event["kind"] == "tool_result" and event["payload"]["name"] == "focus_zone"]
    assert len(prepared) == 1 and prepared[0]["status"] == "succeeded"
    assert prepared[0]["verification"]["status"] == "verified"
    for key in ("zone_id", "zone_label", "state_version", "geometry_version", "geometry_digest", "bounds"):
        assert prepared[0]["data"][key] == command[key]
    return {"full_project_unchanged": True, "one_command_and_matching_ui_receipt": True,
            "full_zone_geometry_and_bounds": True, "gateway_preparation_verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--run-id")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--stage", choices=["before", "completed", "restored"], required=True)
    args = parser.parse_args()
    project_path = f"/api/projects/{args.project_id}"
    project = call_json(args.base_url, project_path)
    if args.stage == "before":
        assert not args.evidence.exists(), "Use a new capture file; keep earlier evidence"
        evidence = {"project_id": args.project_id, "before": project}
    else:
        assert args.run_id, "Completion capture needs a run ID"
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        assert evidence["project_id"] == args.project_id
        run = call_json(args.base_url, f"{project_path}/agent-runs/{args.run_id}")
        checks = verify_completion(run, evidence["before"], project)
        if args.stage == "completed":
            assert "completed_run" not in evidence, "This capture was already verified"
            evidence.update(run_id=args.run_id, completed_run=run, after=project, verified=checks)
        else:
            assert evidence["run_id"] == args.run_id and evidence["completed_run"] == run
            evidence["verified"]["checkpoint_restored_unchanged"] = True
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
