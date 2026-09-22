"""Opt-in relocated frozen runtime checks; requires a real native AutoCAD ticket."""

import hashlib
import json
import os
import selectors
import shutil
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


def test_relocated_runtime_without_python_path(frozen):
    binary, root, env = frozen
    web = root / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>Packaged desktop test</title>")
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
                deadline = time.monotonic() + 45
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


def test_frozen_native_compiler_uses_real_prepared_ticket(frozen):
    configured = os.environ.get("GREEN_ATLAS_NATIVE_TICKET")
    if not configured:
        pytest.skip(
            "Set GREEN_ATLAS_NATIVE_TICKET to an AutoCAD-produced positive control"
        )
    ticket_path = Path(configured)
    ticket = json.loads(ticket_path.read_bytes())
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
    )
    assert result.returncode != 0
    assert "Unknown Green Atlas worker" in result.stderr
    assert "session_token" not in result.stdout
