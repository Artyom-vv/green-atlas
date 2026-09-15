"""Sequential real-DXF acceptance set for the autonomous runtime.

The runner talks only to the local API configured with the isolated preview
database. It never calls ``approve``; scenarios stop at an exact verified
preview or an honest capacity question and leave the project plan unchanged.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
import math
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import ProxyHandler, Request, build_opener


SCENARIOS = (
    (
        "delegated-tree-density",
        "Выбери подходящий участок сам. Посади 6 деревьев вдоль зданий, "
        "породу выбери сам, плотнее, отступы соблюдай.",
        {"plant_kind": "tree", "scope_mode": "delegated", "requested": 6},
    ),
    (
        "explicit-tree-zone",
        "На участке Допустимая область 5 посади 6 деревьев вдоль зданий, "
        "породу выбери сам, отступы соблюдай.",
        {"plant_kind": "tree", "scope_mode": "explicit", "requested": 6,
         "outcomes": ["exact", "partial", "impossible"]},
    ),
    (
        "delegated-mixed-density",
        "Выбери подходящий участок сам. Подготовь 10 растений: деревья и "
        "кустарники, состав выбери сам, размести группами вдоль зданий, "
        "плотнее, отступы соблюдай.",
        {"plant_kind": "mixed", "scope_mode": "delegated", "requested": 10},
    ),
    (
        "delegated-tree-ten",
        "Посади 10 деревьев вдоль зданий. Участок и породу выбери сам, отступы соблюдай.",
        {"plant_kind": "tree", "scope_mode": "delegated", "requested": 10},
    ),
    (
        "explicit-capacity-limit",
        "На участке Допустимая область 8 посади 70 деревьев вдоль зданий. "
        "Породу выбери сам, отступы соблюдай.",
        {"plant_kind": "tree", "scope_mode": "explicit", "requested": 70,
         "outcomes": ["partial", "impossible"]},
    ),
)


def call_json(base_url: str, path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"content-type": "application/json"} if body is not None else {},
    )
    try:
        # Host corporate proxies must not intercept the local fixture API.
        opener = build_opener(ProxyHandler({})) if urlsplit(base_url).hostname in {"127.0.0.1", "localhost", "::1"} else build_opener()
        with opener.open(request, timeout=90) as response:
            return json.loads(response.read())
    except (HTTPError, URLError) as error:
        detail = error.read().decode("utf-8", "replace") if isinstance(error, HTTPError) else str(error)
        raise RuntimeError(f"{method} {path}: {detail[:600]}") from error


def wait_for_preview(base_url: str, project_id: str, run_id: str, timeout_seconds: int) -> dict:
    path = f"/api/projects/{project_id}/agent-runs/{run_id}"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        record = call_json(base_url, path)
        status = record["state"]["status"]
        if status in {"waiting_approval", "waiting_question", "failed", "cancelled", "finished"}:
            return record
        time.sleep(1)
    raise TimeoutError(f"run {run_id} did not reach a terminal checkpoint")


def validate_scenario(record: dict, expected: dict) -> dict:
    state = record["state"]
    intent = state["intent"]
    last = state.get("last_result") or {}
    data = last.get("data") or {}
    change_set = data.get("change_set") or {}
    outcome = data.get("placement_outcome") or {}
    if state["status"] not in {"waiting_approval", "waiting_question"}:
        failure = state.get("failure") or {}
        raise AssertionError(f"status={state['status']} failure={failure.get('code')}")
    if intent["goal"]["operation"] != "place":
        raise AssertionError(f"operation lost: {intent['goal']['operation']}")
    if intent.get("plant_kind") != expected["plant_kind"]:
        raise AssertionError(f"plant_kind lost: {intent.get('plant_kind')}")
    if intent.get("scope_mode") != expected["scope_mode"]:
        raise AssertionError(f"scope_mode lost: {intent.get('scope_mode')}")
    if last.get("name") != "prepare_placement":
        raise AssertionError(f"last tool={last.get('name')}")
    if not outcome:
        raise AssertionError(f"placement has no capacity result: question={state.get('pending_question')}, error={last.get('error')}")
    requested = outcome.get("requested")
    found = outcome.get("found")
    if intent["goal"].get("target_count") != expected["requested"] or requested != expected["requested"]:
        raise AssertionError(f"target count changed: {intent['goal'].get('target_count')} / {requested}")
    if outcome.get("status") not in expected.get("outcomes", ["exact"]):
        raise AssertionError(f"unexpected capacity outcome: {outcome.get('status')}")
    if not isinstance(found, int) or found > requested or outcome.get("shortfall") != requested - found:
        raise AssertionError(f"preview count={found}/{requested}")
    if outcome["status"] == "exact":
        if found != requested or state["status"] != "waiting_approval" or change_set.get("can_apply") is not True:
            raise AssertionError("exact preview is not applicable or has the wrong count")
        if (last.get("verification") or {}).get("status") != "verified":
            raise AssertionError("exact preview was not verified")
    else:
        if state["status"] != "waiting_question" or state.get("pending_approval") or data.get("requires_confirmation"):
            raise AssertionError("incomplete target incorrectly offered approval")
        if not outcome.get("reason") or not outcome.get("remedy_options"):
            raise AssertionError("incomplete target has no explanation or remedies")
        if (outcome["status"] == "partial") != (found > 0):
            raise AssertionError("partial/impossible result contradicts the found count")
    if not state.get("resolved_scope", {}).get("zone_ids"):
        raise AssertionError("resolved scope is empty")
    if intent["scope_mode"] == "explicit" and set(state["resolved_scope"]["zone_ids"]) != set(intent["explicit_zone_ids"]):
        raise AssertionError("explicit scope changed during placement")
    return {
        "status": state["status"],
        "step": state["step"],
        "tools": len(state["tool_calls"]),
        "operation": intent["goal"]["operation"],
        "plant_kind": intent.get("plant_kind"),
        "scope_mode": intent.get("scope_mode"),
        "resolved_zone_ids": state["resolved_scope"]["zone_ids"],
        "requested": requested,
        "found": found,
        "outcome": outcome["status"],
        "shortfall": outcome["shortfall"],
        "remedy_options": outcome.get("remedy_options"),
        "kind_counts": data.get("kind_counts"),
        "plan_version": change_set.get("base_plan_version"),
    }


def validate_saved_preview(base_url: str, project_id: str, record: dict) -> dict:
    """Verify the public geometry/approval boundary, not just planner metadata."""
    state = record["state"]
    reference = (state.get("pending_approval") or {}).get("preview_ref")
    if state["status"] != "waiting_approval" or not reference:
        return {"saved_preview_available": False}
    preview = call_json(
        base_url,
        f"/api/projects/{project_id}/agent-runs/{state['run_id']}/preview?"
        + urlencode({"preview_ref": reference}),
    )
    data = state["last_result"]["data"]
    compact = data["change_set"]
    additions = preview.get("additions", [])
    for key in ("id", "digest", "base_plan_version"):
        if preview.get(key) != compact.get(key):
            raise AssertionError(f"saved preview {key} differs from verified result")
    if preview.get("can_apply") is not True or preview.get("updates") or preview.get("deletion_ids"):
        raise AssertionError("placement preview is not applicable or includes unrelated changes")
    if len(additions) != data["placement_outcome"]["found"]:
        raise AssertionError("saved geometry count differs from placement outcome")
    ids = [item.get("id") for item in additions]
    if len(set(ids)) != len(ids) or not all(ids):
        raise AssertionError("saved geometry has missing or duplicate object IDs")
    zones = set(state["resolved_scope"]["zone_ids"])
    species = set(data.get("species_revision_ids", []))
    for item in additions:
        if item.get("planting_zone_id") not in zones:
            raise AssertionError("saved object escaped the resolved scope")
        if species and item.get("species_revision_id") not in species:
            raise AssertionError("saved object differs from the verified species")
        if any(not isinstance(item.get(key), (int, float)) or not math.isfinite(item[key]) for key in ("x", "y", "radius")):
            raise AssertionError("saved geometry contains an invalid coordinate or radius")
    if dict(Counter(item["kind"] for item in additions)) != data.get("kind_counts"):
        raise AssertionError("saved geometry composition differs from verified result")
    return {"saved_preview_available": True, "saved_objects": len(additions), "run_id": state["run_id"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", required=True, help="Isolated project created by seed_autonomous_fixture.py")
    parser.add_argument("--scenario", action="append", choices=[item[0] for item in SCENARIOS])
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--exercise-quantity-answer", action="store_true",
                        help="For a partial result, send the UI's explicit quantity remedy and verify a new exact preview without applying it")
    args = parser.parse_args()
    project_id = args.project_id

    baseline = call_json(args.base_url, f"/api/projects/{project_id}")
    baseline_plan = baseline.get("plan") or {}
    baseline_signature = (baseline.get("state_version"), baseline_plan.get("version"), len(baseline_plan.get("objects", [])))
    results = []
    quantity_answers = 0
    for name, prompt, expected in SCENARIOS:
        if args.scenario and name not in args.scenario:
            continue
        created = call_json(
            args.base_url,
            f"/api/projects/{project_id}/agent-runs",
            method="POST",
            payload={"text": prompt},
        )
        run_id = created["state"]["run_id"]
        print(json.dumps({"started": name, "run_id": run_id, "project_id": project_id}, ensure_ascii=False), flush=True)
        call_json(args.base_url, f"/api/projects/{project_id}/agent-runs/{run_id}/run", method="POST", payload={})
        record = wait_for_preview(args.base_url, project_id, run_id, args.timeout)
        result = validate_scenario(record, expected)
        result.update(validate_saved_preview(args.base_url, project_id, record))
        if args.exercise_quantity_answer and result["outcome"] == "partial":
            original = record["state"]
            run_path = f"/api/projects/{project_id}/agent-runs/{run_id}"
            answer = f"Измени количество растений на {result['found']}. Сохрани участок, схему и породы."
            answered = call_json(args.base_url, run_path + "/answer", method="POST", payload={"text": answer})
            assert answered["state"]["status"] == "queued" and answered["state"]["execution_attempt_id"] is None
            assert answered["state"]["pending_approval"] is None
            scheduled = call_json(args.base_url, run_path + "/run", method="POST", payload={})
            assert scheduled["state"]["execution_attempt_id"] != original["execution_attempt_id"]
            amended = wait_for_preview(args.base_url, project_id, run_id, args.timeout)
            amended_result = validate_scenario(amended, {**expected, "scope_mode": "explicit", "requested": result["found"], "outcomes": ["exact"]})
            amended_result.update(validate_saved_preview(args.base_url, project_id, amended))
            assert amended["state"]["resolved_scope"]["zone_ids"] == original["resolved_scope"]["zone_ids"]
            assert amended["state"]["intent"]["source_turns"] == [*original["intent"]["source_turns"], answer]
            for key in ("species_revision_ids", "arrangement", "plant_kind"):
                assert amended["state"]["last_result"]["data"][key] == original["last_result"]["data"][key]
            assert not any(event["kind"] in {"commit_started", "commit_applied"} for event in amended["events"])
            result["quantity_answer"] = {**amended_result, "answer": answer, "source_turns": amended["state"]["intent"]["source_turns"],
                "same_scope_species_and_arrangement": True, "distinct_execution_attempt": True}
            quantity_answers += 1
        after_case = call_json(args.base_url, f"/api/projects/{project_id}")
        after_case_plan = after_case.get("plan") or {}
        case_signature = (after_case.get("state_version"), after_case_plan.get("version"), len(after_case_plan.get("objects", [])))
        if case_signature != baseline_signature:
            raise AssertionError(f"preview mutated project in {name}: before={baseline_signature} after={case_signature}")
        if (after_case.get("plan") != baseline.get("plan")
                or after_case.get("planting_zones") != baseline.get("planting_zones")):
            raise AssertionError(f"preview changed full plan or planting zones in {name}")
        result["scenario"] = name
        result["run_id"] = run_id
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    after = call_json(args.base_url, f"/api/projects/{project_id}")
    after_plan = after.get("plan") or {}
    after_signature = (after.get("state_version"), after_plan.get("version"), len(after_plan.get("objects", [])))
    if after_signature != baseline_signature:
        raise AssertionError(f"preview mutated project: before={baseline_signature} after={after_signature}")
    if args.exercise_quantity_answer and not quantity_answers:
        raise AssertionError("No partial result was available to verify the quantity-answer path")
    print(json.dumps({"pass": True, "scenarios": len(results), "project": baseline_signature}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError, TimeoutError) as error:
        print(json.dumps({"pass": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
