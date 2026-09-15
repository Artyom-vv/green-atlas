"""Create a separate real-DXF project for local autonomous acceptance."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from shapely.geometry import Point, mapping, shape


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--fixture", type=Path, default=Path(__file__).resolve().parents[3] / "fixtures/large-map/kitay-gorod/kitay-gorod-3d.dxf")
    parser.add_argument("--name", default="Китай-город — проверка автономного агента")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--seed-edit-plan", action="store_true", help="Seed three unlocked trees in zone 1 and three locked trees in zone 2, only in the newly created project")
    mode.add_argument("--geometry-only", action="store_true", help="Stop after importing and calculating the real DXF, for agent zone creation before a planting plan exists")
    args = parser.parse_args()
    if urlparse(args.base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("Acceptance seeding requires a local API with an isolated database")
    with httpx.Client(base_url=args.base_url, timeout=180, trust_env=False) as client:
        def request(method: str, path: str, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        project = request("POST", "/api/projects", json={"name": args.name})
        print(json.dumps({"created_project_id": project["id"]}), flush=True)
        prefix = f"/api/projects/{project['id']}"
        with args.fixture.open("rb") as source:
            imported = request("POST", f"{prefix}/source-dxf", files={"file": (args.fixture.name, source, "application/dxf")})
        request("PUT", f"{prefix}/layer-mappings", json={"mappings": [
            {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
            for layer in imported["layers"]
        ]})
        operation = request("POST", f"{prefix}/operations/geometry")
        deadline = time.monotonic() + 180
        while operation["status"] not in {"completed", "failed", "cancelled"}:
            if time.monotonic() > deadline:
                raise TimeoutError("DXF geometry calculation timed out")
            time.sleep(0.5)
            operation = request("GET", f"{prefix}/operations/{operation['id']}")
        if operation["status"] != "completed":
            raise RuntimeError(f"Geometry failed: {operation}")
        project = request("GET", prefix, params={"include_geometry": "true"})
        if args.geometry_only:
            print(json.dumps({"project_id": project["id"], "source": args.fixture.name,
                              "state_version": project["state_version"], "geometry_version": project["geometry_version"],
                              "plan_version": project["plan"]["version"] if project.get("plan") else None,
                              "zones": len(project["planting_zones"]), "planting_count": 0,
                              "contour_ids": [str(feature["id"]) for feature in project["geometry"]["feature_collection"]["features"]
                                              if feature.get("id") and feature.get("properties", {}).get("kind") in {"allowed", "site_border"}]},
                             ensure_ascii=False), flush=True)
            return
        polygons = []
        for feature in project["geometry"]["feature_collection"]["features"]:
            if feature["properties"].get("kind") != "allowed":
                continue
            geometry = shape(feature["geometry"])
            polygons.extend(list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry])
        polygons = sorted((p for p in polygons if p.geom_type == "Polygon" and not p.is_empty), key=lambda p: p.area, reverse=True)[:8]
        if len(polygons) < 8:
            raise RuntimeError("The acceptance scenarios require eight non-overlapping allowed areas")
        zones = [
            {"id": f"acceptance-zone-{index}", "label": f"Допустимая область {index}", "geometry": mapping(polygon)}
            for index, polygon in enumerate(polygons, 1)
        ]
        request("PUT", f"{prefix}/planting-zones", json={"zones": zones})
        project = request("POST", f"{prefix}/plan/manual")
        if args.seed_edit_plan:
            assert not project["plan"]["objects"], "Edit seeding requires this fresh empty project"
            for index, polygon in enumerate(polygons[:2]):
                interior = polygon.buffer(-15)
                min_x, min_y, max_x, max_y = interior.bounds
                candidates = [Point(min_x + (max_x - min_x) * x / 20, min_y + (max_y - min_y) * y / 20)
                              for y in range(1, 20) for x in range(1, 20)]
                chosen = []
                for point in candidates:
                    if not interior.covers(point) or any(point.distance(previous) < 30 for previous in chosen):
                        continue
                    current = request("GET", prefix)
                    draft = {"base_plan_version": current["plan"]["version"], "label": "Тестовые посадки для приёмки изменений",
                             "operations": [{"type": "add", "object": {"kind": "tree", "x": point.x, "y": point.y,
                                  "radius": 1.6, "layout_radius_m": 1.6, "size_class": "standard",
                                  "species_revision_id": "sorbus-aucuparia@2026-08-28.1", "locked": index == 1}}]}
                    preview = request("POST", f"{prefix}/plan/change-sets/preview", json=draft)
                    if not preview["can_apply"]:
                        continue
                    request("POST", f"{prefix}/plan/change-sets/apply", json={"preview_id": preview["id"],
                            "digest": preview["digest"], "base_plan_version": preview["base_plan_version"]})
                    chosen.append(point)
                    if len(chosen) == 3:
                        break
                if len(chosen) != 3:
                    raise RuntimeError(f"No three valid separated edit-fixture positions in zone {index + 1}; project {project['id']} retained")
            project = request("GET", prefix)
        print(json.dumps({"project_id": project["id"], "source": args.fixture.name,
                          "zones": len(zones), "state_version": project["state_version"],
                          "plan_version": project["plan"]["version"],
                          "planting_count": len(project["plan"]["objects"])}, ensure_ascii=False))


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    main()
