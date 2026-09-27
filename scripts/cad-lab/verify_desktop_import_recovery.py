"""Exercise the frozen app against a private backup; never mutate the user's DB."""

import argparse
import hashlib
import json
import selectors
import sqlite3
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    workspace = args.output / "workspace"
    workspace.mkdir()
    with (
        sqlite3.connect(f"file:{args.database}?mode=ro", uri=True) as source,
        sqlite3.connect(workspace / "projects.sqlite3") as target,
    ):
        source.backup(target)
    resources = args.app / "Contents/Resources"
    report = {"project": args.project, "database_is_copy": True, "checks": []}
    log = (args.output / "runtime.log").open("w")
    process = subprocess.Popen(
        [
            str(resources / "Runtime/GreenAtlasRuntime"),
            "--data-dir",
            str(workspace),
            "--web-dir",
            str(resources / "Web"),
        ],
        stdout=subprocess.PIPE,
        stderr=log,
        text=True,
    )
    try:
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(45):
            raise RuntimeError("Frozen runtime did not start")
        launch = json.loads(process.stdout.readline())
        # Launch credentials stay in process memory, never in reports or URLs.
        opener = build_opener(ProxyHandler({}))

        def request(path, body=None, method="GET", raw=False):
            data = json.dumps(body).encode() if body is not None else None
            headers = {"x-green-atlas-local-session": launch["session_token"]}
            if data is not None:
                headers["Content-Type"] = "application/json"
            try:
                with opener.open(
                    Request(
                        launch["origin"] + path,
                        data=data,
                        headers=headers,
                        method=method,
                    ),
                    timeout=180,
                ) as response:
                    payload = response.read()
                    return payload if raw else json.loads(payload)
            except HTTPError as error:
                raise RuntimeError(
                    f"{method} {path}: {error.code} {error.read().decode()}"
                ) from error

        root = f"/api/projects/{args.project}"
        project = request(root + "?include_geometry=false")
        asset = request(root + "/source-dxf/asset")
        content = request(asset["file_url"], raw=True)
        assert (
            hashlib.sha256(content).hexdigest()
            == project["source_file"]["content_sha256"]
        )
        report["checks"].append({"source_asset": "passed", "bytes": len(content)})
        del content
        # Explicitly confirm the recorded mappings in the QA copy only. This
        # tests execution, not the semantic correctness of those suggestions.
        mappings = [
            {
                "layer_id": layer["id"],
                "kind": layer["mapped_kind"],
                "confirmed": True,
                "visible": layer["visible"],
                "utility_context": layer.get("utility_context"),
                "utility_axis_bindings": layer.get("utility_axis_bindings", []),
            }
            for layer in project["layers"]
        ]
        request(root + "/layer-mappings", {"mappings": mappings}, "PUT")
        started = time.monotonic()
        operation = request(root + "/operations/geometry", method="POST")
        last_stage = None
        while operation["status"] in ("queued", "running"):
            if time.monotonic() - started > 180:
                raise RuntimeError("Calculation exceeded the QA budget")
            stage = operation.get("stage")
            if stage != last_stage:
                print(stage, flush=True)
                last_stage = stage
            time.sleep(1)
            operation = request(root + "/operations/" + operation["id"])
        report["checks"].append(
            {
                "calculation": operation["status"],
                "error": operation.get("error"),
                "seconds": round(time.monotonic() - started, 2),
            }
        )
        assert operation["status"] == "completed", operation.get("error")
        project = request(root + "?include_geometry=true")
        report["checks"].append(
            {
                "geometry_features": len(
                    project["geometry"]["feature_collection"]["features"]
                ),
                "scope": project["geometry"].get("calculation_scope"),
                "allowed_area_m2": project["allowed_area_m2"],
            }
        )
        passport = request(root + "/data-passport")
        report["checks"].append(
            {
                "passport": passport["calculation_status"],
                "mass_placement": passport["mass_placement_status"],
            }
        )
        from shapely.geometry import mapping, shape

        areas = [
            shape(feature["geometry"])
            for feature in project["geometry"]["feature_collection"]["features"]
            if feature.get("properties", {}).get("kind") == "allowed"
        ]
        area = max(areas, key=lambda item: item.area)
        point = area.buffer(-3).representative_point()
        assert not point.is_empty
        request(
            root + "/planting-zones",
            {
                "zones": [
                    {
                        "id": "import-qa",
                        "label": "Проверка импорта",
                        "geometry": mapping(area),
                    }
                ]
            },
            "PUT",
        )
        request(root + "/plan/manual", method="POST")
        candidate = {"kind": "tree", "x": point.x, "y": point.y, "radius": 1}
        checked = request(root + "/plan/placement-check", candidate, "POST")
        assert checked["allowed"], checked
        plan = request(root + "/plan/objects", candidate, "POST")
        assert len(plan["objects"]) == 1
        reopened = request(root + "?include_geometry=false")
        assert reopened["plan"]["objects"][0]["id"] == plan["objects"][0]["id"]
        assert any(
            issue["code"] == "SOURCE_GEOMETRY_PARTIAL" for issue in plan["issues"]
        )
        report["checks"].append(
            {
                "planting_and_reopen": "passed",
                "planting_status": plan["objects"][0]["status"],
            }
        )
        report["status"] = "passed"
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        raise
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        log.close()
        (args.output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2)
        )
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
