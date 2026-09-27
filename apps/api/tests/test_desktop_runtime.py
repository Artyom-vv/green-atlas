"""Real child + existing API, not a frozen installer or AutoCAD UI test."""

import json
import os
import selectors
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app.desktop.lease import WorkspaceLease
from app.desktop.runtime import configure_local_storage
from app.desktop.web import SESSION_HEADER


def test_workspace_has_one_runtime_owner(tmp_path):
    first = WorkspaceLease(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="уже работает"):
            WorkspaceLease(tmp_path)
    finally:
        first.close()
    second = WorkspaceLease(tmp_path)
    second.close()


def test_local_storage_does_not_reuse_remote_or_developer_data(tmp_path, monkeypatch):
    # Restore the process environment after this focused configuration test.
    for key in (
        "GREEN_ATLAS_DB_PATH",
        "GREEN_ATLAS_CAD_INTAKE_PATH",
        "GREEN_ATLAS_CAD_ROOTS_JSON",
        "GREEN_ATLAS_BRIDGE_ORIGIN",
        "GREEN_ATLAS_BRIDGE_PROXY_KEY",
        "GREEN_ATLAS_DWG_CONVERTER",
    ):
        monkeypatch.setenv(key, "previous-value")
    directory = configure_local_storage(tmp_path.resolve() / "desktop")
    assert os.environ["GREEN_ATLAS_DB_PATH"] == str(directory / "projects.sqlite3")
    assert os.environ["GREEN_ATLAS_CAD_ROOTS_JSON"] == "{}"
    assert "GREEN_ATLAS_BRIDGE_ORIGIN" not in os.environ
    assert "GREEN_ATLAS_BRIDGE_PROXY_KEY" not in os.environ


def test_symlink_storage_is_not_silently_followed(tmp_path):
    link = tmp_path / "alias"
    target = tmp_path / "target"
    target.mkdir()
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic"):
        configure_local_storage(link / "workspace")


def test_real_runtime_selects_port_and_serves_isolated_project_list(tmp_path):
    root = tmp_path.resolve()
    web = root / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>Desktop runtime test</title>")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    with (root / "runtime.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.desktop.runtime",
                "--data-dir",
                str(root / "data"),
                "--web-dir",
                str(web),
            ],
            stdout=subprocess.PIPE,
            stderr=log,
            env=env,
            text=True,
        )
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                assert ready.select(timeout=30), (
                    "Local runtime did not announce readiness"
                )
                line = process.stdout.readline()
            if not line:
                log.seek(0)
                pytest.fail("Local runtime stopped: " + log.read()[-2000:])
            receipt = json.loads(line)
            assert receipt["service"] == "green-atlas-desktop"
            origin, secret = receipt["origin"], receipt["session_token"]
            assert origin.startswith("http://127.0.0.1:")
            assert len(secret) >= 43
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with pytest.raises(urllib.error.HTTPError) as unauthorized:
                opener.open(origin + "/api/projects", timeout=10)
            assert unauthorized.value.code == 403
            request = urllib.request.Request(
                origin + "/api/projects", headers={SESSION_HEADER: secret}
            )
            with opener.open(request, timeout=10) as response:
                projects = json.load(response)
            assert projects == []
            assert (root / "data/projects.sqlite3").is_file()
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
