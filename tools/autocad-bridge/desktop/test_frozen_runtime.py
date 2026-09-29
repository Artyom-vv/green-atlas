"""Opt-in relocated frozen runtime checks; requires a real native AutoCAD ticket."""

import hashlib
import json
import os
import selectors
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def frozen(tmp_path_factory):
    configured = os.environ.get("GREEN_ATLAS_FROZEN_RUNTIME")
    if not configured:
        pytest.skip("Set GREEN_ATLAS_FROZEN_RUNTIME to the built executable")
    source = Path(configured)
    assert source.is_file()
    root = tmp_path_factory.mktemp("relocated-desktop").resolve()
    bundle = root / "runtime"
    shutil.copytree(source.parent, bundle, symlinks=True)
    env = dict(os.environ, PATH="/usr/bin:/bin")
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(key, None)
    return bundle / source.name, root, env


@pytest.mark.parametrize(
    "relative_path",
    [
        "species/data/source-profiles.json",
        "species/data/moscow-assortment.json",
        "regulations/placement_rules.json",
    ],
)
def test_frozen_runtime_includes_catalog_resources(frozen, relative_path):
    binary, _, _ = frozen
    source = Path(__file__).resolve().parents[3] / "apps/api/app" / relative_path
    packaged = binary.parent / "_internal/app" / relative_path
    assert packaged.is_file(), f"Missing runtime resource: {relative_path}"
    assert packaged.read_bytes() == source.read_bytes()


def test_relocated_runtime_without_python_path(frozen):
    binary, root, env = frozen
    web = root / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>Packaged desktop test</title>")
    presets = os.environ.get("GREEN_ATLAS_FROZEN_PRESETS")
    if presets:
        shutil.copytree(Path(presets), root / "LayerRecognition")
        # Prove the sample is reproducible without an API account or local CLI.
        env["GREEN_ATLAS_LAYER_MODEL_PROVIDER"] = "names"
    transfers = root / "Transfers"
    transfers.mkdir()
    configured = os.environ.get("GREEN_ATLAS_NATIVE_TICKET")
    ticket = None
    if configured:
        ticket = transfers / "native-control" / "transfer.gatransfer"
        shutil.copytree(Path(configured).parent, ticket.parent)
    with (root / "runtime.log").open("w+") as log:
        process = subprocess.Popen(
            [
                str(binary),
                "--data-dir",
                str(root / "data"),
                "--web-dir",
                str(web),
                "--ticket-root",
                str(transfers),
            ],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
        )
        try:
            with selectors.DefaultSelector() as events:
                events.register(process.stdout, selectors.EVENT_READ)
                assert events.select(timeout=30), "Frozen runtime did not start"
                line = process.stdout.readline()
            if not line:
                log.seek(0)
                pytest.fail(log.read()[-4000:])
            receipt = json.loads(line)
            origin, secret = receipt["origin"], receipt["session_token"]
            assert origin.startswith("http://127.0.0.1:") and len(secret) >= 43
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with pytest.raises(urllib.error.HTTPError) as denied:
                opener.open(origin + "/api/projects", timeout=10)
            assert denied.value.code == 403
            with opener.open(
                urllib.request.Request(
                    origin + "/api/projects",
                    headers={"X-Green-Atlas-Local-Session": secret},
                ),
                timeout=10,
            ) as response:
                assert json.load(response) == []
            assert (root / "data/projects.sqlite3").is_file()
            if ticket:

                def api(path, payload=None):
                    request = urllib.request.Request(
                        origin + path,
                        data=json.dumps(payload).encode() if payload else None,
                        headers={
                            "X-Green-Atlas-Local-Session": secret,
                            "Content-Type": "application/json",
                        },
                    )
                    with opener.open(request, timeout=10) as response:
                        return json.load(response)

                handoff = api("/_desktop/handoffs", {"ticket": str(ticket)})
                deadline = time.monotonic() + 180
                while (
                    handoff["status"] in {"queued", "processing"}
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.1)
                    handoff = api("/_desktop/handoffs/" + handoff["id"])
                assert handoff["status"] == "needs_review", handoff
                projects = api("/api/projects")
                assert len(projects) == 1
                project_id = projects[0]["id"]
                transfer = json.loads(ticket.read_bytes())
                if transfer["schema"] in {"green-atlas.transfer/2", "green-atlas.transfer/4"}:
                    project = api(f"/api/projects/{project_id}")
                    assert project["import_status"]["mode"] == "autocad_live"
                    assert project["source_file"]["content_sha256"] == transfer["manifest"]["files"][0]["sha256"]
                    assert project["layers"]
                    if presets:
                        assert all(not item["mapping_confirmed"] for item in project["layers"])
                        recognition = api(f"/api/projects/{project_id}/source-layer-review")
                        assert recognition["status"] == "completed", recognition["message"]
                        assert recognition["provider"] == "openai/gpt-6-luna"
                        assert recognition["processed_count"] == len(project["layers"])
                        assert "Сохранённые" in recognition["message"]
                    (root / "live-handoff-acceptance.json").write_text(json.dumps({
                        "project_id": project_id,
                        "scope": "isolated frozen runtime storage, not GUI acceptance",
                        "plugin_version": transfer["plugin_version"],
                        "status": handoff["status"],
                        "layer_count": len(project["layers"]),
                        "source_sha256": project["source_file"]["content_sha256"],
                    }, indent=2))
                else:
                    operation_path = f"/api/projects/{project_id}/operations/latest?kind=inspect_cad_package"
                    operation = api(operation_path)
                    while (
                        operation["status"] in {"queued", "running"}
                        and time.monotonic() < deadline
                    ):
                        time.sleep(0.1)
                        operation = api(operation_path)
                    assert operation["status"] == "completed", operation
                assert api("/_desktop/handoffs", {"ticket": str(ticket)}) == handoff
                assert len(api("/api/projects")) == 1
            log.flush()
            log.seek(0)
            assert secret not in log.read()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


def test_relocated_runtime_saves_automatic_planting(frozen):
    """Opt-in Kustanayskaya control uses the exact installed runtime binary."""
    source = os.environ.get("GREEN_ATLAS_PREPARED_DB")
    project_id = os.environ.get("GREEN_ATLAS_PREPARED_PROJECT_ID")
    if not source or not project_id:
        pytest.skip("Set GREEN_ATLAS_PREPARED_DB and GREEN_ATLAS_PREPARED_PROJECT_ID")
    binary, root, env = frozen
    data = root / "prepared-data"
    data.mkdir()
    with sqlite3.connect(source) as original, sqlite3.connect(data / "projects.sqlite3") as copy:
        original.backup(copy)
    web = root / "prepared-web"
    web.mkdir()
    (web / "index.html").write_text("<title>Isolated planting control</title>")
    env.pop("GREEN_ATLAS_DB_PATH", None)
    env["GREEN_ATLAS_LAYER_MODEL_PROVIDER"] = "names"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def launch():
        process = subprocess.Popen(
            [str(binary), "--data-dir", str(data), "--web-dir", str(web)],
            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True,
        )
        with selectors.DefaultSelector() as events:
            events.register(process.stdout, selectors.EVENT_READ)
            assert events.select(timeout=30), "Frozen runtime did not start"
            line = process.stdout.readline()
        assert line, process.stderr.read()[-4000:]
        receipt = json.loads(line)

        def api(path, payload=None):
            request = urllib.request.Request(
                receipt["origin"] + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={
                    "X-Green-Atlas-Local-Session": receipt["session_token"],
                    "Content-Type": "application/json",
                },
            )
            with opener.open(request, timeout=120) as response:
                return json.load(response)

        return process, api

    path = f"/api/projects/{project_id}"
    process, api = launch()
    try:
        project = api(path)
        assert project["map_ready"] and project["planting_zones"]
        before = len(project["plan"]["objects"])
        preview = api(path + "/plan/automatic/preview", {
            "base_plan_version": project["plan"]["version"],
            "zone_id": project["planting_zones"][0]["id"],
            "near": "area", "plant_kind": "auto", "target_count": 8,
        })
        assert preview["found"] > 0 and preview["change_set"]["can_apply"], preview
        change = preview["change_set"]
        saved = api(path + "/plan/change-sets/apply", {
            "preview_id": change["id"], "digest": change["digest"],
            "base_plan_version": change["base_plan_version"],
        })
        assert len(saved["added_ids"]) == preview["found"]
        assert len(saved["plan"]["objects"]) == before + preview["found"]
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()

    process, api = launch()
    try:
        reopened = api(path)
        assert len(reopened["plan"]["objects"]) == before + preview["found"]
        assert {item["id"] for item in reopened["plan"]["objects"]} >= set(saved["added_ids"])
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()


def test_frozen_native_compiler_uses_real_prepared_ticket(frozen):
    configured = os.environ.get("GREEN_ATLAS_NATIVE_TICKET")
    if not configured:
        pytest.skip(
            "Set GREEN_ATLAS_NATIVE_TICKET to an AutoCAD-produced positive control"
        )
    ticket_path = Path(configured)
    ticket = json.loads(ticket_path.read_bytes())
    if ticket["schema"] in {"green-atlas.transfer/2", "green-atlas.transfer/4"}:
        pytest.skip("Live tickets use the import worker exercised by the frozen handoff test")
    binary, root, env = frozen
    package = root / "package"
    package.mkdir()
    for file in ticket["manifest"]["files"]:
        assert Path(file["name"]).name == file["name"]
        source = ticket_path.parent / file["name"]
        assert source.stat().st_size == file["bytes"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == file["sha256"]
        shutil.copyfile(source, package / file["name"])
    probe = json.loads(
        (
            package / (ticket["manifest"]["entry"] + ".green-atlas.geometry.json")
        ).read_bytes()
    )
    assert probe["summary"]["native"] > 0, (
        "This must be a positive native geometry control, not an empty prior ticket"
    )
    task = package / "compile-request.json"
    task.write_text(
        json.dumps(
            {
                "directory": str(package),
                "manifest": ticket["manifest"],
                "producer": ticket["producer"],
                "plugin_version": ticket["plugin_version"],
                "root_id": "upload-" + "a" * 32,
            }
        )
    )
    result = subprocess.run(
        [str(binary), "--worker", "app.cad_delivery.compile_worker", str(task)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads((package / "bridge-report.json").read_bytes()) == {"failures": []}
    upload = json.loads((package / "upload.json").read_bytes())
    assert len(upload["snapshots"]) == 1
    snapshot = package / upload["snapshots"][0]["path"]
    assert snapshot.stat().st_size > 0
    assert (
        hashlib.sha256(snapshot.read_bytes()).hexdigest()
        == upload["snapshots"][0]["sha256"]
    )
    assert (
        "session_token" not in result.stdout
    )  # worker did not restart the main server

    # The next production worker must consume this snapshot in the frozen bundle,
    # not fall back to Python or report an empty successful import.
    inspection = root / "source-package.json"
    work = root / "inspection-request.json"
    entry = next(
        file
        for file in ticket["manifest"]["files"]
        if file["name"] == ticket["manifest"]["entry"]
    )
    work.write_text(
        json.dumps(
            {
                "root": str(package),
                "cache": str(root / "cache"),
                "output": str(inspection),
                "request": {
                    "root_id": "upload-" + "a" * 32,
                    "entry": entry["name"],
                    "entry_sha256": entry["sha256"],
                },
            }
        )
    )
    result = subprocess.run(
        [str(binary), "--worker", "app.cad_intake.worker", str(work)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    passport = json.loads(inspection.read_bytes())
    assert passport["status"] == "requires_review"
    assert len(passport["drawings"]) == 1
    assert passport["drawings"][0]["status"] == "readable", passport["drawings"][0][
        "message"
    ]
    assert passport["drawings"][0]["source_sha256"] == entry["sha256"]


def test_frozen_unknown_worker_never_launches_runtime(frozen):
    binary, root, env = frozen
    result = subprocess.run(
        [str(binary), "--worker", "os"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode != 0
    assert "Unknown Green Atlas worker" in result.stderr
    assert "session_token" not in result.stdout
