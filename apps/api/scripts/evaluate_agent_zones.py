"""Real-model zone acceptance through saved public previews, without approval."""
from __future__ import annotations

import argparse
import json
import math
import sys
from urllib.parse import urlencode

from shapely.geometry import shape

from evaluate_autonomous_runtime import call_json, wait_for_preview


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--zone-id", default="acceptance-zone-3")
    parser.add_argument("--occupied-zone-id", default="acceptance-zone-1")
    parser.add_argument("--feature-id", help="Exact full-project contour for creation; required with --scenario create")
    parser.add_argument("--label", default="Тестовый участок агента")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--scenario", action="append", choices=["create", "rename", "delete-empty", "delete-occupied"])
    args = parser.parse_args()
    if any(character in args.label for character in '\n\r"«»'):
        parser.error("The acceptance label must be one quoted name")
    scenarios = args.scenario or ["rename", "delete-empty", "delete-occupied"]
    if "create" in scenarios and not args.feature_id:
        parser.error("--scenario create requires --feature-id from the full project")
    path = f"/api/projects/{args.project_id}"
    original = call_json(args.base_url, path + "?include_geometry=true")
    original_zones = original["planting_zones"]
    zones = {zone["id"]: zone for zone in original_zones}
    objects = (original.get("plan") or {}).get("objects", [])
    plan_version = (original.get("plan") or {}).get("version")
    features = {str(feature.get("id")): feature for feature in original["geometry"]["feature_collection"]["features"]}
    for name in scenarios:
        feature = None
        target = None
        if name == "create":
            feature = features[args.feature_id]
            assert feature.get("properties", {}).get("kind") in {"allowed", "site_border", "planting_area"}
            prompt = f'Создай участок "{args.label}" по контуру {args.feature_id}.'
            operation = "create"
        else:
            target = zones[args.occupied_zone_id if name == "delete-occupied" else args.zone_id]
            population = [obj for obj in objects if obj.get("planting_zone_id") == target["id"]]
            if name == "delete-occupied":
                assert population, "Blocked deletion acceptance needs an occupied zone"
            elif name == "delete-empty":
                assert not population and len(zones) > 1, "Empty deletion requires a non-last unoccupied zone"
            if name == "rename":
                assert args.label != target["label"], "Rename acceptance needs a different name"
                prompt = f'Переименуй участок {target["label"]} в "{args.label}".'
                operation = "update"
            else:
                prompt = f'Удали участок {target["label"]}.'
                operation = "delete"
        run = call_json(args.base_url, f"{path}/agent-runs", method="POST", payload={"text": prompt})
        run_id = run["state"]["run_id"]
        print(json.dumps({"started": name, "run_id": run_id, "project_id": args.project_id}, ensure_ascii=False), flush=True)
        call_json(args.base_url, f"{path}/agent-runs/{run_id}/run", method="POST", payload={})
        run = wait_for_preview(args.base_url, args.project_id, run_id, args.timeout)
        state = run["state"]
        last = state.get("last_result") or {}
        assert state["intent"]["goal"]["operation"] == "zones", state["intent"]
        assert last.get("name") == "prepare_zone_change", state.get("pending_question") or last
        assert not any(event["kind"] == "commit_applied" for event in run["events"])
        if name == "delete-occupied":
            assert state["status"] == "waiting_question" and not state.get("pending_approval"), state
            assert (last.get("error") or {}).get("code") == "ZONE_CHANGE_BLOCKED", last
            assert state.get("pending_question", {}).get("slot") == "zone", state
            assert (last.get("data") or {}).get("blockers"), last
        else:
            assert state["status"] == "waiting_approval", state.get("pending_question") or state.get("failure")
            approval = state["pending_approval"]
            assert approval.get("kind") == "planting_zones", approval
            preview = call_json(args.base_url, f"{path}/agent-runs/{run_id}/zone-preview?" + urlencode({"preview_ref": approval["preview_ref"]}))
            assert preview["can_apply"] and not preview["blockers"] and not preview["affected_planting_ids"]
            assert preview["project_id"] == args.project_id and preview["operation"] == operation
            assert preview["base_state_version"] == original["state_version"]
            assert preview["base_geometry_version"] == original["geometry_version"] and preview["base_plan_version"] == plan_version
            assert preview["before_zones"] == original_zones, "Preview substituted the original zone population"
            if name == "create":
                assert preview["target_zone_id"] not in zones
                expected = [*original_zones, {"id": preview["target_zone_id"], "label": args.label, "geometry": feature["geometry"]}]
                assert preview["before_area_m2"] is None
                assert math.isclose(preview["after_area_m2"], shape(feature["geometry"]).area, rel_tol=1e-9)
            elif name == "rename":
                assert preview["target_zone_id"] == target["id"]
                expected = [{**zone, "label": args.label} if zone["id"] == target["id"] else zone for zone in original_zones]
                assert preview["before_area_m2"] == preview["after_area_m2"]
            else:
                assert preview["target_zone_id"] == target["id"] and preview["after_area_m2"] is None
                expected = [zone for zone in original_zones if zone["id"] != target["id"]]
            assert preview["after_zones"] == expected, "Full proposed contours/names/IDs differ from the requested change"
        after = call_json(args.base_url, path)
        assert after["state_version"] == original["state_version"] and after["geometry_version"] == original["geometry_version"]
        assert after["planting_zones"] == original_zones and after.get("plan") == original.get("plan"), "Zone preview mutated the project"
        print(json.dumps({"pass": True, "scenario": name, "run_id": run_id, "project_id": args.project_id,
                          "status": state["status"], "operation": operation}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
