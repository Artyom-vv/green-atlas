"""Exercise the running hybrid preview endpoint without applying any planting."""

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from time import monotonic
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project")
    parser.add_argument("zones", nargs="+")
    parser.add_argument("--api", default="http://127.0.0.1:8004")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument("--deadline", type=float, default=300)
    args = parser.parse_args()

    def fingerprint():
        with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
            raw = db.execute("SELECT CAST(payload AS BLOB) FROM projects WHERE id=?", (args.project,)).fetchone()[0]
        return hashlib.sha256(raw).hexdigest()

    def call(path, payload=None):
        request = Request(args.api + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={"Content-Type": "application/json"})
        with urlopen(request, timeout=args.request_timeout) as response:
            return json.load(response)

    path = f"/api/projects/{args.project}"
    before = fingerprint()
    project = call(path + "?include_geometry=false")
    labels = {zone["id"]: zone["label"] for zone in project["planting_zones"]}
    receipt = {"project": args.project, "state_version": project["state_version"],
               "geometry_version": project["geometry_version"], "plan_version": project["plan"]["version"],
               "payload_sha256_before": before, "applied": False, "cases": []}
    for zone in args.zones:
        label = labels[zone]
        request = {"type": "fill", "base_plan_version": project["plan"]["version"],
                   "zone_ids": [zone], "plant_kind": "shrub", "layout": "natural",
                   "placement_mode": "count", "target_count": args.count,
                   "spacing_m": 2.15, "layout_radius_m": .65, "edge_offset_m": .65}
        start, calls = monotonic(), []
        while True:
            if monotonic() - start > args.deadline:
                raise TimeoutError("Предпросмотр не завершился за контрольный срок")
            call_start = monotonic()
            result = call(path + "/plan/patterns/preview", request)
            domains = [{k: v for k, v in domain.items()
                        if k not in {"geometry", "unresolved_geometry", "pending_geometry", "source_issues"}}
                       for domain in result["search_domains"]]
            calls.append({"seconds": monotonic() - call_start, "domains": domains})
            print(json.dumps({"zone": label, "call": len(calls), "seconds": calls[-1]["seconds"],
                              "progress": [{k: d[k] for k in ("processed_objects", "total_objects", "stop_reason")}
                                           for d in domains]}, ensure_ascii=False), flush=True)
            if domains and all(d["stop_reason"] == "resolution" for d in domains):
                break
        change_set = result.get("change_set") or {}
        case = {"zone": zone, "label": label, "request": request, "seconds": monotonic() - start,
                "calls": calls, "requested": result["requested_count"], "accepted": result["accepted_count"],
                "generated": result["generated_count"], "can_apply": change_set.get("can_apply", False),
                "reasons": result["reason_summary"], "search_stop_reason": result["search_stop_reason"],
                "point_count": len(change_set.get("additions", []))}
        receipt["cases"].append(case)
        print(json.dumps({k: v for k, v in case.items() if k not in {"calls", "request"}}, ensure_ascii=False), flush=True)
    receipt["payload_sha256_after"] = fingerprint()
    receipt["payload_unchanged"] = receipt["payload_sha256_after"] == before
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    if not receipt["payload_unchanged"]:
        raise ValueError("Содержимое проекта изменилось во время проверки, проверьте параллельные действия")
    print(json.dumps({"receipt": str(args.output), "payload_unchanged": True}), flush=True)


if __name__ == "__main__":
    main()
