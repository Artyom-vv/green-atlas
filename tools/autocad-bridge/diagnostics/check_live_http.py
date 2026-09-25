"""Read-only controls through ordinary HTTP placement-check, not a pilot API."""

import argparse
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--road-pattern", action="store_true")
    args = parser.parse_args()
    controls = {
        "closed_building": (15935.0, -4960.0),
        "road": (16092.001603, -5397.977498),
        "open_building_not_certified": (15994.606306, -5245.249309),
        "candidate_ground": (15970.0, -4920.0),
    }
    receipt = {"url": args.url, "controls": []}
    with httpx.Client(timeout=120) as client:
        project = client.get(args.url).raise_for_status().json()
        receipt["basis"] = {
            k: project[k] for k in ("id", "state_version", "geometry_version")
        }
        for name, (x, y) in controls.items():
            start = time.monotonic()
            response = client.post(
                args.url + "/plan/placement-check",
                json={
                    "kind": "shrub",
                    "x": x,
                    "y": y,
                    "radius": 0.5,
                    "base_plan_version": project["plan"]["version"],
                    "state_version": project["state_version"],
                    "geometry_version": project["geometry_version"],
                },
            )
            record = {
                "name": name,
                "elapsed_s": time.monotonic() - start,
                "http_status": response.status_code,
                "body": response.json(),
            }
            receipt["controls"].append(record)
            print(
                json.dumps(
                    {
                        "name": name,
                        "elapsed_s": record["elapsed_s"],
                        "http_status": response.status_code,
                        "code": record["body"].get("code"),
                        "allowed": record["body"].get("allowed"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        if args.road_pattern:
            request = {
                "type": "fill",
                "base_plan_version": project["plan"]["version"],
                "zone_ids": ["native-road-control"],
                "plant_kind": "shrub",
                "composition": "shrubs",
                "placement_mode": "count",
                "target_count": 5,
                "spacing_m": 2.15,
                "edge_offset_m": 0.65,
                "layout": "natural",
                "layout_radius_m": 0.65,
                "species_revision_id": "spiraea-japonica@2026-08-28.1",
                "seed": 1,
            }
            start = time.monotonic()
            response = client.post(args.url + "/plan/patterns/preview", json=request)
            response.raise_for_status()
            receipt["road_pattern"] = {
                "request": request,
                "reply": response.json(),
                "elapsed_s": time.monotonic() - start,
            }
            print(
                json.dumps(
                    {
                        "road_pattern": {
                            key: response.json()[key]
                            for key in (
                                "requested_count",
                                "generated_count",
                                "accepted_count",
                            )
                        }
                    }
                )
            )
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
