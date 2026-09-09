"""Sequential real-DXF acceptance set for the autonomous runtime.

The runner talks only to the local API configured with the isolated preview
database. It never calls ``approve``; every scenario must stop at a verified
preview and leave the project plan unchanged.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ID = "0216cb9e-23ac-429e-bd1a-08815164ef0f"
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
        {"plant_kind": "tree", "scope_mode": "explicit", "requested": 6},
    ),
    (
        "delegated-mixed-density",
        "Выбери подходящий участок сам. Подготовь 10 растений: деревья и "
        "кустарники, состав выбери сам, размести группами вдоль зданий, "
        "плотнее, отступы соблюдай.",
        {"plant_kind": "mixed", "scope_mode": "delegated", "requested": 10},
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
        with urlopen(request, timeout=90) as response:
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
        if status in {"waiting_approval", "failed", "cancelled", "finished"}:
            return record
        time.sleep(1)
    raise TimeoutError(f"run {run_id} did not reach a terminal checkpoint")


def validate_scenario(record: dict, expected: dict) -> dict:
    state = record["state"]
    intent = state["intent"]
    last = state.get("last_result") or {}
    data = last.get("data") or {}
    change_set = data.get("change_set") or {}
    if state["status"] != "waiting_approval":
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
    if change_set.get("can_apply") is not True:
        raise AssertionError("preview is not applicable")
    requested = data.get("requested")
    found = data.get("found")
    if requested != expected["requested"] or found != expected["requested"]:
        raise AssertionError(f"preview count={found}/{requested}")
    if not state.get("resolved_scope", {}).get("zone_ids"):
        raise AssertionError("resolved scope is empty")
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
        "shortfall": data.get("shortfall"),
        "kind_counts": data.get("kind_counts"),
        "plan_version": change_set.get("base_plan_version"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--project-id", default=PROJECT_ID)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()
    project_id = args.project_id

    baseline = call_json(args.base_url, f"/api/projects/{project_id}")
    baseline_plan = baseline.get("plan") or {}
    baseline_signature = (baseline.get("state_version"), baseline_plan.get("version"), len(baseline_plan.get("objects", [])))
    results = []
    for name, prompt, expected in SCENARIOS:
        created = call_json(
            args.base_url,
            f"/api/projects/{project_id}/agent-runs",
            method="POST",
            payload={"text": prompt},
        )
        run_id = created["state"]["run_id"]
        call_json(args.base_url, f"/api/projects/{project_id}/agent-runs/{run_id}/run", method="POST", payload={})
        record = wait_for_preview(args.base_url, project_id, run_id, args.timeout)
        result = validate_scenario(record, expected)
        result["scenario"] = name
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    after = call_json(args.base_url, f"/api/projects/{project_id}")
    after_plan = after.get("plan") or {}
    after_signature = (after.get("state_version"), after_plan.get("version"), len(after_plan.get("objects", [])))
    if after_signature != baseline_signature:
        raise AssertionError(f"preview mutated project: before={baseline_signature} after={after_signature}")
    print(json.dumps({"pass": True, "scenarios": len(results), "project": baseline_signature}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, RuntimeError, TimeoutError) as error:
        print(json.dumps({"pass": False, "error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
