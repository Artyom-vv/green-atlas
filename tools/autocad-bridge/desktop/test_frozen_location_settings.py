"""One real-runtime credential lifecycle, without upstream calls or user settings."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from contextlib import contextmanager
from pathlib import Path

import pytest
from test_frozen_runtime import ready_line

ENDPOINT = "/api/location/settings"
PERSONAL_KEY = "isolated-frozen-test-personal-key"


def test_frozen_location_settings_survive_restart_and_reset(tmp_path):
    configured = os.environ.get("GREEN_ATLAS_FROZEN_RUNTIME")
    if not configured:
        pytest.skip("Set GREEN_ATLAS_FROZEN_RUNTIME to the built executable")
    binary = Path(configured).resolve(strict=True)
    assert binary.is_file()
    root = tmp_path.resolve() / "location-settings.noindex"
    root.mkdir(mode=0o700)
    home, web, data = (root / name for name in ("home", "web", "data"))
    for path in (home, web, data):
        path.mkdir(mode=0o700)
    (web / "index.html").write_text("<title>Isolated location settings</title>")
    personal = home / "private/Location/dadata.json"
    default_source = (
        "bundled"
        if (binary.parent / "_internal/Services/dadata.json").is_file()
        else "none"
    )
    default_status = {
        "configured": default_source == "bundled",
        "source": default_source,
        "personal_key_configured": False,
        "bundled_key_configured": default_source == "bundled",
    }
    if os.name == "nt":
        system = Path(os.environ["SystemRoot"])
        clean_path = os.pathsep.join(map(str, (system / "System32", system)))
    else:
        clean_path = "/usr/bin:/bin"
    env = dict(
        os.environ,
        PATH=clean_path,
        PYTHONUTF8="1",
        HOME=str(home),
        USERPROFILE=str(home),
        APPDATA=str(home / "AppData/Roaming"),
        LOCALAPPDATA=str(home / "AppData/Local"),
        XDG_CONFIG_HOME=str(home / ".config"),
        XDG_DATA_HOME=str(home / ".local/share"),
        GREEN_ATLAS_LOCATION_SETTINGS=str(personal),
        GREEN_ATLAS_LAYER_MODEL_PROVIDER="names",
        GREEN_ATLAS_AUTOCAD_ROOT=str(root / "absent-autocad"),
    )
    for name in (
        "DADATA_API_KEY",
        "OPENAI_API_KEY",
        "OPENAI_PROXY_URL",
        "GREEN_ATLAS_RELAY_URL",
        "GREEN_ATLAS_RELAY_TOKEN",
        "GREEN_ATLAS_DB_PATH",
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
    ):
        env.pop(name, None)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @contextmanager
    def runtime():
        process = subprocess.Popen(
            [str(binary), "--data-dir", str(data), "--web-dir", str(web)],
            cwd=root,
            env=env,
            stdout=subprocess.PIPE,
            # Ready receipts and local credentials must never become test logs.
            stderr=subprocess.DEVNULL,
            text=True,
        )
        try:
            line = ready_line(process)
            try:
                ready = json.loads(line)
                origin, secret = ready["origin"], ready["session_token"]
            except (ValueError, KeyError, TypeError):
                pytest.fail("Frozen runtime returned no valid ready receipt")
            del line, ready
            if not origin.startswith("http://127.0.0.1:") or len(secret) < 43:
                pytest.fail("Frozen runtime returned an invalid local session")

            def request(method="GET", payload=None, *, authenticated=True):
                headers = {"Content-Type": "application/json"}
                if authenticated:
                    headers["X-Green-Atlas-Local-Session"] = secret
                outgoing = urllib.request.Request(
                    origin + ENDPOINT,
                    method=method,
                    data=json.dumps(payload).encode() if payload is not None else None,
                    headers=headers,
                )
                try:
                    incoming = opener.open(outgoing, timeout=10)
                except urllib.error.HTTPError as error:
                    incoming = error
                with incoming:
                    raw = incoming.read()
                    if secret.encode() in raw:
                        pytest.fail("A runtime response exposed the session credential")
                    return incoming.code, json.loads(raw), incoming.headers

            yield request
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()

    with runtime() as request:
        status, body, _ = request(authenticated=False)
        assert status == 403
        assert body["code"] == "LOCAL_SESSION_REQUIRED"
        assert not personal.exists()
        status, body, headers = request()
        assert status == 200 and body == default_status
        assert headers["Cache-Control"] == "no-store"
        status, body, _ = request("PUT", {"api_key": PERSONAL_KEY})
        assert status == 200 and body == {
            **default_status,
            "configured": True,
            "source": "personal",
            "personal_key_configured": True,
        }
        assert PERSONAL_KEY not in json.dumps(body)
        saved = personal.read_bytes()
        assert json.loads(saved) == {"api_key": PERSONAL_KEY}
        if os.name != "nt":
            assert personal.stat().st_mode & 0o777 == 0o600
        status, body, _ = request()
        assert status == 200 and body["source"] == "personal"
        assert PERSONAL_KEY not in json.dumps(body)
        status, body, _ = request("PUT", {"api_key": "invalid\nkey"})
        assert status == 422 and "invalid\nkey" not in json.dumps(body)
        assert personal.read_bytes() == saved

    with runtime() as request:
        status, body, _ = request()
        assert status == 200 and body["source"] == "personal"
        assert body["personal_key_configured"]
        assert PERSONAL_KEY not in json.dumps(body)
        assert personal.read_bytes() == saved
        status, body, _ = request("DELETE")
        assert status == 200 and body == default_status
        assert not personal.exists()
        status, body, _ = request()
        assert status == 200 and body == default_status
