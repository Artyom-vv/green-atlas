"""Real-model read/edit/delete acceptance against an isolated populated project.

All changes remain previews. Use a seeded project with unlocked trees in
the named zone, for example the fixture after the UI placement smoke.
The complete zone population is requested so choosing a subset is not implied.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from urllib.parse import urlencode

from evaluate_autonomous_runtime import call_json, wait_for_preview


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--zone-id", default="acceptance-zone-8")
    parser.add_argument("--zone-name", default="Допустимая область 8")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--scenario", action="append", choices=["inspect", "species", "edit-lock", "edit-unlock", "edit-move", "edit-species", "edit-species-blocked", "delete", "ambiguous-selection"])
    parser.add_argument("--replacement-species-id", default="betula-pendula@2026-08-28.1")
    args = parser.parse_args()
    path = f"/api/projects/{args.project_id}"
    baseline = call_json(args.base_url, path)
    original = {item["id"]: item for item in baseline["plan"]["objects"]}
    targets = [item for item in original.values() if item["kind"] == "tree" and item.get("planting_zone_id") == args.zone_id]
    assert targets, "Fixture needs trees in the selected zone"
    if args.scenario and "edit-unlock" in args.scenario:
        assert all(item["locked"] for item in targets), "Unlock acceptance needs an explicitly prepared locked population"
    if not args.scenario or any(name in args.scenario for name in ("edit-lock", "edit-move", "edit-species", "delete")):
        assert all(not item["locked"] for item in targets), "This acceptance scenario needs an unlocked population"
    species = call_json(args.base_url, "/api/species")
    replacement = next((item for item in species if item["id"] == args.replacement_species_id), None)
    assert replacement and replacement["kind"] == "tree", "Replacement must be a real tree catalog revision"
    target_count = len(targets)
    scenarios = [
        ("inspect", "Проверь текущий план посадок и покажи найденные ограничения.", "plan_issues"),
        ("species", f"Подбери подходящие породы деревьев для участка {args.zone_name}.", "species_shortlist"),
        ("edit-lock", f"Закрепи все {target_count} дерева на участке {args.zone_name}.", "prepare_existing_change"),
        ("edit-move", f"Перемести все {target_count} дерева на участке {args.zone_name}: смещение X +0.1 м, Y 0 м.", "prepare_existing_change"),
        ("edit-species", f"Замени породу всех {target_count} деревьев на участке {args.zone_name} на {replacement['common_name']}.", "prepare_existing_change"),
        ("delete", f"Удали все {target_count} дерева на участке {args.zone_name}.", "prepare_existing_change"),
    ]
    if args.scenario and "edit-unlock" in args.scenario:
        scenarios.append(("edit-unlock", f"Сними закрепление всех {target_count} деревьев на участке {args.zone_name}.", "prepare_existing_change"))
    if args.scenario and "edit-species-blocked" in args.scenario:
        scenarios.append(("edit-species-blocked", f"Замени породу всех {target_count} деревьев на участке {args.zone_name} на {replacement['common_name']}.", "prepare_existing_change"))
    if target_count > 1:
        scenarios.append(("ambiguous-selection", f"Закрепи 1 дерево на участке {args.zone_name}.", "prepare_existing_change"))
    elif args.scenario and "ambiguous-selection" in args.scenario:
        raise AssertionError("Ambiguous selection needs at least two trees in the selected zone")
    for name, prompt, tool_name in scenarios:
        if args.scenario and name not in args.scenario:
            continue
        run = call_json(args.base_url, f"{path}/agent-runs", method="POST", payload={"text": prompt})
        run_id = run["state"]["run_id"]
        print(json.dumps({"started": name, "run_id": run_id}, ensure_ascii=False), flush=True)
        call_json(args.base_url, f"{path}/agent-runs/{run_id}/run", method="POST", payload={})
        run = wait_for_preview(args.base_url, args.project_id, run_id, args.timeout)
        state = run["state"]
        last = state.get("last_result") or {}
        assert last.get("name") == tool_name, f"{name}: unexpected result {last.get('name')}, status={state['status']}, failure={state.get('failure')}"
        assert not any(event["kind"] == "commit_applied" for event in run["events"]), f"{name}: unexpected commit"
        if name in {"inspect", "species"}:
            assert state["status"] == "finished" and not state.get("pending_approval"), f"{name}: read did not finish without approval"
            assert last["status"] == "succeeded" and isinstance(last.get("data"), dict)
            outcome = state.get("read_outcome") or {}
            assert outcome.get("status") == "complete" and outcome.get("capability") == tool_name, "Read has no verified completion evidence"
            assert outcome["snapshot_version"] == baseline["state_version"] and outcome["plan_version"] == baseline["plan"]["version"]
            assert outcome.get("caveat") and not outcome["object_ids"]
            assert set(outcome["zone_ids"]) == ({args.zone_id} if name == "species" else set())
            assert outcome["source"] == ("species_suitability" if name == "species" else "saved_plan_validation")
            pages = [event["payload"]["read_page"] for event in run["events"]
                     if event["kind"] == "tool_result" and event["payload"].get("call_id") in outcome["evidence_refs"]]
            assert len(pages) == outcome["pages"]
            read_ids = [item_id for page in pages for item_id in page["item_ids"]]
            assert len(read_ids) == len(set(read_ids)) == outcome["total"]
            if name == "species":
                assert outcome["kind"] == "tree"
        elif name == "edit-species-blocked":
            assert state["status"] == "waiting_question" and state["pending_question"]["slot"] == "preview"
            assert not state.get("pending_approval") and (last.get("error") or {}).get("code") == "DOMAIN_PREVIEW_BLOCKED"
            refusal = last.get("preview_refusal") or {}
            assert refusal.get("status") == "blocked" and refusal.get("blocked_count", 0) > 0
            assert refusal.get("reasons") and refusal.get("reason_codes") and refusal.get("remedy")
            assert state["intent"]["species_ids"] == [replacement["id"]]
            assert state["intent"]["goal"]["target_count"] == target_count
            assert state["intent"]["explicit_zone_ids"] == [args.zone_id]
        elif name == "ambiguous-selection":
            assert state["status"] == "waiting_question", "Ambiguous population did not request clarification"
            assert not state.get("pending_approval") and state.get("pending_question")
            assert state["intent"]["goal"]["target_count"] == 1, "Clarification silently expanded the requested count"
            assert (last.get("error") or {}).get("code") == "OBJECT_SELECTION_REQUIRED"
        else:
            assert state["status"] == "waiting_approval", f"{name}: no verified preview: {state.get('pending_question')}"
            assert state["intent"]["goal"]["target_count"] == target_count
            assert set(state["intent"]["explicit_zone_ids"]) == {args.zone_id}
            reference = state["pending_approval"]["preview_ref"]
            preview = call_json(args.base_url, f"{path}/agent-runs/{run_id}/preview?" + urlencode({"preview_ref": reference}))
            assert preview["can_apply"] and preview["base_plan_version"] == baseline["plan"]["version"]
            assert not preview["additions"], f"{name}: unexpected additions"
            if name == "delete":
                assert not preview["updates"] and len(preview["deletion_ids"]) == target_count
                affected = preview["deletion_ids"]
            else:
                assert not preview["deletion_ids"] and len(preview["updates"]) == target_count
                affected = [item["id"] for item in preview["updates"]]
                for changed in preview["updates"]:
                    expected = dict(original[changed["id"]])
                    if name in {"edit-lock", "edit-unlock"}:
                        expected["locked"] = name == "edit-lock"
                    elif name == "edit-move":
                        expected["x"] += 0.1
                    elif name == "edit-species":
                        assert expected["species_revision_id"] != replacement["id"], "Replacement acceptance needs a different source species"
                        assert expected["size_class"] == "standard", "Catalog forecast comparison requires the standard-size fixture"
                        expected["species_revision_id"] = replacement["id"]
                        expected["canopy_forecast"] = replacement["canopy_forecast"]
                        expected["root_forecast"] = replacement["root_forecast"]
                    assert set(changed) == set(expected), f"{name}: object fields changed"
                    for key, value in expected.items():
                        if key in {"x", "y"}:
                            assert math.isclose(changed[key], value, rel_tol=0, abs_tol=1e-7), f"{name}: unexpected {key}"
                        else:
                            assert changed[key] == value, f"{name}: changed unrelated property {key}"
            assert set(affected) == {item["id"] for item in targets}, "Preview changed a different population"
        after = call_json(args.base_url, path)
        assert after["state_version"] == baseline["state_version"] and after["plan"] == baseline["plan"], f"{name}: preview mutated project"
        print(json.dumps({"pass": True, "scenario": name, "run_id": run_id, "status": state["status"], "tool": tool_name}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
