"""Проверка перенесённого runtime; CAD-сценарий требует отдельного native ticket."""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

import pytest


def ready_line(process: subprocess.Popen[str], timeout: float = 30) -> str:
    """Windows pipe не является socket: читаем ready с отдельным ограниченным ожиданием."""
    stream = process.stdout
    assert stream is not None
    records: Queue[str | Exception] = Queue(maxsize=1)

    def read() -> None:
        try:
            records.put(stream.readline())
        except (OSError, ValueError) as error:
            records.put(error)

    Thread(target=read, daemon=True).start()
    try:
        value = records.get(timeout=timeout)
    except Empty:
        pytest.fail("Runtime не объявил готовность за отведённое время")
    if isinstance(value, Exception):
        raise value
    return value


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
    if os.name == "nt":
        system = Path(os.environ["SystemRoot"])
        clean_path = os.pathsep.join(map(str, (system / "System32", system)))
    else:
        clean_path = "/usr/bin:/bin"
    env = dict(os.environ, PATH=clean_path)
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX"):
        env.pop(key, None)
    return bundle / source.name, root, env


@pytest.mark.parametrize(
    "relative_path",
    [
        "species/data/source-profiles.json",
        "species/data/moscow-assortment.json",
        "regulations/placement_rules.json",
        "regulations/references.json",
        "shared/messages.ru.json",
        "cad_bridge/probe_diagnostics.json",
        "dxf_import/layers/recognition_catalog.json",
        "shared/storage-config.json",
        "rendering/blender_worker.py",
        "rendering/render_style.py",
        "rendering/lawn_instances.py",
        "rendering/lawn_scatter.py",
        "rendering/preview_asset_worker.py",
        "rendering/native_style.json",
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
            line = ready_line(process)
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
                    origin + "/_desktop/health",
                    headers={"X-Green-Atlas-Local-Session": secret},
                ),
                timeout=10,
            ) as response:
                assert response.status == 200
                assert json.load(response) == {
                    "service": "green-atlas-desktop",
                    "protocol": 1,
                }
            with opener.open(
                urllib.request.Request(
                    origin + "/api/projects",
                    headers={"X-Green-Atlas-Local-Session": secret},
                ),
                timeout=10,
            ) as response:
                assert json.load(response) == []
            with pytest.raises(urllib.error.HTTPError) as invalid:
                opener.open(
                    urllib.request.Request(
                        origin + "/api/projects",
                        data=json.dumps({"name": []}).encode(),
                        headers={
                            "X-Green-Atlas-Local-Session": secret,
                            "Content-Type": "application/json",
                        },
                    ),
                    timeout=10,
                )
            assert invalid.value.code == 422
            assert json.load(invalid.value)["field_errors"]["name"] == ["Введите текст"]
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
                if transfer["schema"] in {
                    "green-atlas.transfer/2",
                    "green-atlas.transfer/4",
                    "green-atlas.transfer/5",
                }:
                    project = api(f"/api/projects/{project_id}")
                    assert project["import_status"]["mode"] == "autocad_live"
                    assert (
                        project["source_file"]["content_sha256"]
                        == transfer["manifest"]["files"][0]["sha256"]
                    )
                    assert project["layers"]
                    if transfer["schema"] == "green-atlas.transfer/5":
                        retained = Path(
                            project["source_file"]["native_session"]["snapshot_path"]
                        )
                        assert retained.is_relative_to(
                            root / "data/projects.sqlite3.cad-archives"
                        )
                        assert (
                            hashlib.sha256(retained.read_bytes()).hexdigest()
                            == transfer["manifest"]["files"][0]["sha256"]
                        )
                        assert retained.with_name("host.dwg").is_file()
                    if presets:
                        assert all(
                            not item["mapping_confirmed"] for item in project["layers"]
                        )
                        recognition = api(
                            f"/api/projects/{project_id}/source-layer-review"
                        )
                        assert recognition["status"] == "completed", recognition[
                            "message"
                        ]
                        assert recognition["provider"] == "openai/gpt-6-luna"
                        assert recognition["processed_count"] == len(project["layers"])
                        assert "Сохранённые" in recognition["message"]
                    (root / "live-handoff-acceptance.json").write_text(
                        json.dumps(
                            {
                                "project_id": project_id,
                                "scope": "isolated frozen runtime storage, not GUI acceptance",
                                "plugin_version": transfer["plugin_version"],
                                "status": handoff["status"],
                                "layer_count": len(project["layers"]),
                                "source_sha256": project["source_file"][
                                    "content_sha256"
                                ],
                            },
                            indent=2,
                        )
                    )
                assert api("/_desktop/handoffs", {"ticket": str(ticket)}) == handoff
                assert len(api("/api/projects")) == 1
            log.flush()
            log.seek(0)
            assert secret not in log.read()
            shutdown = urllib.request.Request(
                origin + "/_desktop/shutdown",
                data=b"{}",
                headers={
                    "X-Green-Atlas-Local-Session": secret,
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with opener.open(shutdown, timeout=10) as response:
                assert response.status == 202
                assert json.load(response) == {"state": "stopping"}
            assert process.wait(timeout=10) == 0
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()


def test_relocated_runtime_saves_automatic_planting(frozen):
    """Opt-in prepared control uses the exact relocated runtime binary."""
    source = os.environ.get("GREEN_ATLAS_PREPARED_DB")
    project_id = os.environ.get("GREEN_ATLAS_PREPARED_PROJECT_ID")
    if not source or not project_id:
        pytest.skip("Set GREEN_ATLAS_PREPARED_DB and GREEN_ATLAS_PREPARED_PROJECT_ID")
    binary, root, env = frozen
    data = root / "prepared-data"
    data.mkdir()
    with (
        sqlite3.connect(
            Path(source).absolute().as_uri() + "?mode=ro", uri=True
        ) as original,
        sqlite3.connect(data / "projects.sqlite3") as copy,
    ):
        original.backup(copy)
    # The DB and immutable prepared inputs are one local workspace. A missing
    # sidecar must never be filled by an implicit live CAD query in this test.
    prepared_source = Path(str(source) + ".prepared-geometry")
    assert prepared_source.is_dir(), "The positive control has no prepared geometry"
    shutil.copytree(
        prepared_source,
        data / "projects.sqlite3.prepared-geometry",
        ignore=shutil.ignore_patterns("domains"),
    )
    geometry_source = Path(str(source) + ".geometry")
    if geometry_source.is_dir():
        shutil.copytree(geometry_source, data / "projects.sqlite3.geometry")
    web = root / "prepared-web"
    web.mkdir()
    (web / "index.html").write_text("<title>Isolated planting control</title>")
    env.pop("GREEN_ATLAS_DB_PATH", None)
    env["GREEN_ATLAS_LAYER_MODEL_PROVIDER"] = "names"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def launch():
        process = subprocess.Popen(
            [str(binary), "--data-dir", str(data), "--web-dir", str(web)],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        line = ready_line(process)
        assert line, process.stderr.read()[-4000:]
        receipt = json.loads(line)

        def api(path, payload=None, method=None):
            request = urllib.request.Request(
                receipt["origin"] + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                method=method,
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
        assert project["resume_kind"] == "workspace"
        assert project["prepared_state"] == "available"
        if project["plan"] is None:
            project = api(path + "/plan/manual", {})
        layer = next(
            item
            for item in project["layers"]
            if item.get("mapped_kind") == "utility" and item.get("utility_context")
        )
        reviewer = "Isolated local restart control"
        physical = data / "projects.sqlite3.prepared-geometry"

        def physical_hashes():
            hashes = {}
            for file in physical.glob("*.json"):
                with file.open("rb") as stream:
                    hashes[file.name] = hashlib.file_digest(
                        stream, "sha256"
                    ).hexdigest()
            return hashes

        physical_before = physical_hashes()
        geometry_version = project["geometry_version"]
        project = api(
            path + "/layer-mappings",
            {
                "mappings": [
                    {
                        "layer_id": layer["id"],
                        "kind": layer["mapped_kind"],
                        "category": layer.get("category"),
                        "visible": layer["visible"],
                        "confirmed": layer.get("mapping_confirmed"),
                        "utility_axis_bindings": layer.get("utility_axis_bindings", []),
                        "utility_context": {
                            **layer["utility_context"],
                            "confirmed_by": reviewer,
                        },
                    }
                ]
            },
            method="PUT",
        )
        assert project["map_ready"] and project["geometry_version"] == geometry_version
        assert physical_hashes() == physical_before
        reviewer_basis = project["plan"]["validation_basis"]
        before = len(project["plan"]["objects"])
        preview = api(
            path + "/plan/automatic/preview",
            {
                "base_plan_version": project["plan"]["version"],
                "zone_id": os.environ.get(
                    "GREEN_ATLAS_PREPARED_ZONE_ID", project["planting_zones"][0]["id"]
                ),
                "near": "area",
                "plant_kind": os.environ.get("GREEN_ATLAS_PREPARED_PLANT_KIND", "auto"),
                "target_count": 8,
            },
        )
        assert preview["found"] > 0 and preview["change_set"]["can_apply"], preview
        change = preview["change_set"]
        saved = api(
            path + "/plan/change-sets/apply",
            {
                "preview_id": change["id"],
                "digest": change["digest"],
                "base_plan_version": change["base_plan_version"],
            },
        )
        assert len(saved["added_ids"]) == preview["found"]
        assert len(saved["plan"]["objects"]) == before + preview["found"]
        basis = saved["plan"]["validation_basis"]
        assert all(
            basis[key]
            for key in ("geometry_digest", "calculation_digest", "evidence_digest")
        )
        assert basis["geometry_digest"] == reviewer_basis["geometry_digest"]
        assert basis["calculation_digest"] == reviewer_basis["calculation_digest"]
        history = api(path + "/plan/history")
        assert history["undo_target_id"] == change["id"]
        undone = api(path + "/plan/history/undo", {})
        assert len(undone["plan"]["objects"]) == before
        assert undone["plan"]["version"] > saved["plan"]["version"]
        assert api(path + "/plan/history")["redo_target_id"] == change["id"]
        redone = api(path + "/plan/history/redo", {})
        assert redone["plan"]["objects"] == saved["plan"]["objects"]
        assert redone["plan"]["version"] > undone["plan"]["version"]
        basis = redone["plan"]["validation_basis"]
        assert physical_hashes() == physical_before
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()

    process, api = launch()
    try:
        reopened = api(path)
        assert len(reopened["plan"]["objects"]) == before + preview["found"]
        assert reopened["plan"]["objects"] == saved["plan"]["objects"]
        assert {item["id"] for item in reopened["plan"]["objects"]} >= set(
            saved["added_ids"]
        )
        assert reopened["plan"]["validation_basis"] == basis
        reopened_layer = next(
            item for item in reopened["layers"] if item["id"] == layer["id"]
        )
        assert reopened_layer["utility_context"]["confirmed_by"] == reviewer
        history = api(path + "/plan/history")
        assert any(entry["id"] == change["id"] for entry in history["entries"])
        from urllib.parse import urlencode

        receipt = api(
            path
            + f"/plan/change-sets/{change['id']}/receipt?"
            + urlencode(
                {
                    "digest": change["digest"],
                    "base_plan_version": change["base_plan_version"],
                    "base_state_version": project["state_version"],
                }
            )
        )
        assert receipt["status"] == "applied"
        assert receipt["receipt"]["added_ids"] == saved["added_ids"]
    finally:
        process.terminate()
        process.wait(timeout=10)
        process.stdout.close()
        process.stderr.close()


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
