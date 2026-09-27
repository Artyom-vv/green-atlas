"""Real-model selection transport acceptance; no proposal is ever applied."""
from __future__ import annotations

import argparse
import json
import sys
from urllib.parse import urlencode

from evaluate_autonomous_runtime import call_json, wait_for_preview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--zone-id", default="acceptance-zone-8")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--scenario", action="append", choices=["object", "zone-read", "empty", "mixed", "stale", "explicit"])
    args = parser.parse_args()
    path = f"/api/projects/{args.project_id}"
    original = call_json(args.base_url, path)
    zone = next(item for item in original["planting_zones"] if item["id"] == args.zone_id)
    targets = [item for item in original["plan"]["objects"] if item.get("planting_zone_id") == zone["id"] and item["kind"] == "tree"]
    target = next(item for item in targets if not item["locked"])
    snapshot = {"project_id": args.project_id, "state_version": original["state_version"],
                "plan_version": original["plan"]["version"], "zone_ids": [item["id"] for item in original["planting_zones"]],
                "object_ids": [target["id"]]}
    scenarios = [
        ("object", "Закрепи выделенное дерево.", snapshot, None),
        ("zone-read", "Подбери подходящие породы деревьев для выделенного участка.",
         {**snapshot, "zone_ids": [zone["id"]]}, None),
        ("empty", "Закрепи выделенное дерево.", {**snapshot, "object_ids": []}, "SELECTION_EMPTY"),
        ("mixed", "Закрепи выделенное.", snapshot, "SELECTION_AMBIGUOUS"),
        ("stale", "Закрепи выделенное дерево.", {**snapshot, "state_version": snapshot["state_version"] - 1}, "SELECTION_STALE"),
        ("explicit", f"Закрепи все {len(targets)} дерева на участке {zone['label']}.",
         {**snapshot, "zone_ids": [], "object_ids": []}, None),
    ]
    for name, prompt, context, expected_issue in scenarios:
        if args.scenario and name not in args.scenario:
            continue
        run = call_json(args.base_url, f"{path}/agent-runs", method="POST", payload={"text": prompt, "selection_context": context})
        run_id = run["state"]["run_id"]
        print(json.dumps({"started": name, "run_id": run_id}, ensure_ascii=False), flush=True)
        call_json(args.base_url, f"{path}/agent-runs/{run_id}/run", method="POST", payload={})
        run = wait_for_preview(args.base_url, args.project_id, run_id, args.timeout)
        state = run["state"]
        intent = state["intent"]
        assert not any(event["kind"] == "commit_applied" for event in run["events"])
        if expected_issue:
            assert state["status"] == "waiting_question" and state["pending_question"] and not state["pending_approval"], state
            assert (intent.get("selection_issue") or {}).get("code") == expected_issue, intent
        else:
            scope = state.get("resolved_scope") or {}
            assert scope.get("basis") == ("user" if name == "explicit" else "selection"), scope
            assert scope["source_revision"] == original["state_version"]
            if name != "explicit":
                assert intent["selection_context"] == context, "Snapshot was silently replaced"
            if name == "zone-read":
                outcome = state.get("read_outcome") or {}
                assert state["status"] == "finished" and outcome.get("status") == "complete", state
                assert outcome["zone_ids"] == [zone["id"]] and not outcome["object_ids"]
                assert scope["zone_ids"] == [zone["id"]] and not scope["object_ids"]
            else:
                assert state["status"] == "waiting_approval", state.get("pending_question") or state.get("failure")
                reference = state["pending_approval"]["preview_ref"]
                preview = call_json(args.base_url, f"{path}/agent-runs/{run_id}/preview?" + urlencode({"preview_ref": reference}))
                assert preview["can_apply"] and not preview["additions"] and not preview["deletion_ids"]
                expected = [target] if name == "object" else targets
                assert {item["id"] for item in preview["updates"]} == {item["id"] for item in expected}
                before = {item["id"]: item for item in expected}
                assert all(item == {**before[item["id"]], "locked": True} for item in preview["updates"])
                if name == "object":
                    assert scope["object_ids"] == [target["id"]] and not scope["zone_ids"]
        after = call_json(args.base_url, path)
        assert after["state_version"] == original["state_version"] and after["plan"] == original["plan"], "Selection acceptance changed the project"
        print(json.dumps({"pass": True, "scenario": name, "run_id": run_id, "status": state["status"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
