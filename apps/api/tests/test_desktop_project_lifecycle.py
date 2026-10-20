"""Owned synthetic prepared project through real desktop HTTP, never CAD acceptance."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from threading import Thread

import pytest
from test_hybrid_search import populate
from test_native_live_provider import setup as native_setup

from app.composition import create_runtime
from app.dxf_import.contracts import ImportMode
from app.native_query.preparation.prepared_snapshot import (
    PreparedStore,
    prepare_snapshot,
)

REPOSITORY = Path(__file__).resolve().parents[3]


def seed_project(data):
    """Use the explicit three-object unit-test oracle, not a user DB or CAD parser."""
    data.mkdir()
    live, _, project = native_setup.__wrapped__(data)
    populate(project)
    project.name = "Изолированный проект Windows"
    project.source_file.native_session = live.session
    project.import_status.mode = ImportMode.AUTOCAD_LIVE
    project.map_ready = True
    database = data / "projects.sqlite3"
    store = PreparedStore(Path(str(database) + ".prepared-geometry"))
    store.save(prepare_snapshot(live, project))
    runtime = create_runtime(database)
    try:
        runtime.project_repository.create(project)
    finally:
        runtime.close()
    return project.id


def immutable_sources(data):
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (data / "projects.sqlite3.prepared-geometry").glob("*.json")
        if len(path.stem) == 64
    }


@contextmanager
def desktop(command, root, data, env):
    log_path = root / "desktop.log"
    with log_path.open("a", encoding="utf-8") as log:
        process = subprocess.Popen(
            [*command, "--data-dir", str(data), "--web-dir", str(root / "web")],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=log,
            text=True,
            encoding="utf-8",
        )
        api = None
        try:
            records = Queue()
            Thread(
                target=lambda: records.put(process.stdout.readline()), daemon=True
            ).start()
            line = records.get(timeout=45)
            assert line, log_path.read_text(encoding="utf-8")[-4000:]
            ready = json.loads(line)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with pytest.raises(urllib.error.HTTPError) as denied:
                opener.open(ready["origin"] + "/api/projects", timeout=10)
            assert denied.value.code == 403

            def api(path, payload=None):
                request = urllib.request.Request(
                    ready["origin"] + path,
                    data=json.dumps(payload).encode("utf-8")
                    if payload is not None
                    else None,
                    headers={
                        "X-Green-Atlas-Local-Session": ready["session_token"],
                        "Content-Type": "application/json",
                    },
                )
                with opener.open(request, timeout=90) as response:
                    return json.load(response)

            yield api
        finally:
            try:
                if api is not None and process.poll() is None:
                    assert api("/_desktop/shutdown", {}) == {"state": "stopping"}
                    assert process.wait(timeout=15) == 0
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=10)
                process.stdout.close()


@pytest.mark.parametrize("mode", ["source", "frozen"])
def test_owned_prepared_project_planting_history_and_restart(tmp_path, mode):
    binary = os.environ.get("GREEN_ATLAS_FROZEN_RUNTIME")
    if mode == "frozen" and not binary:
        pytest.skip("Set GREEN_ATLAS_FROZEN_RUNTIME to qualify the compiled executable")
    root = tmp_path / "Профиль растения"
    root.mkdir()
    data = root / "Workspace"
    project_id = seed_project(data)
    original_sources = immutable_sources(data)
    assert original_sources
    (root / "web").mkdir()
    (root / "web/index.html").write_text(
        "<title>Owned synthetic lifecycle</title>", encoding="utf-8"
    )
    env = dict(os.environ, PYTHONUTF8="1", GREEN_ATLAS_LAYER_MODEL_PROVIDER="names")
    for key in (
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "GREEN_ATLAS_DB_PATH",
        "GREEN_ATLAS_NATIVE_TICKET",
    ):
        env.pop(key, None)
    if mode == "source":
        env["PYTHONPATH"] = str(REPOSITORY / "apps/api")
        command = [
            sys.executable,
            str(REPOSITORY / "tools/autocad-bridge/desktop/entry.py"),
        ]
    else:
        copied = root / "runtime"
        shutil.copytree(Path(binary).parent, copied)
        command = [str(copied / Path(binary).name)]
        env.pop("PYTHONPATH", None)
        if os.name == "nt":
            system = Path(os.environ["SystemRoot"])
            env["PATH"] = os.pathsep.join(map(str, (system / "System32", system)))
        else:
            env["PATH"] = "/usr/bin:/bin"
    path = f"/api/projects/{project_id}"
    with desktop(command, root, data, env) as api:
        project = api(path)
        assert project["map_ready"] and project["prepared_state"] == "available"
        assert project["resume_kind"] == "workspace"
        check = api(path + "/plan/placement-check", {"kind": "tree", "x": 60, "y": 50})
        assert check["allowed"], check
        preview = api(
            path + "/plan/automatic/preview",
            {
                "base_plan_version": project["plan"]["version"],
                "zone_id": "work",
                "near": "area",
                "plant_kind": "auto",
                "target_count": 3,
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
        objects = saved["plan"]["objects"]
        assert len(objects) == preview["found"]
        assert api(path + "/plan/history")["undo_target_id"] == change["id"]
        undone = api(path + "/plan/history/undo", {})
        assert undone["plan"]["objects"] == []
        assert undone["plan"]["version"] > saved["plan"]["version"]
        redone = api(path + "/plan/history/redo", {})
        assert redone["plan"]["objects"] == objects
        assert redone["plan"]["version"] > undone["plan"]["version"]
    assert immutable_sources(data) == original_sources
    with desktop(command, root, data, env) as api:
        reopened = api(path)
        assert reopened["plan"]["objects"] == objects
        assert (
            reopened["plan"]["validation_basis"] == redone["plan"]["validation_basis"]
        )
        assert api(path + "/plan/history")["undo_target_id"] == change["id"]
        assert api(path + "/plan/history/undo", {})["plan"]["objects"] == []
        assert api(path + "/plan/history/redo", {})["plan"]["objects"] == objects
    assert immutable_sources(data) == original_sources
