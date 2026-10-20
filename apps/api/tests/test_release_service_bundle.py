"""Run the actual spec's data-input phase without building executable binaries."""

from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.layer_recognition.relay_settings import RelaySettings

SPEC = Path(__file__).resolve().parents[3] / "tools/autocad-bridge/desktop/runtime.spec"
URL = "https://release.example/api/luna/recognize"
TOKEN = "dummy-release-only-token-for-isolated-tests"


@pytest.fixture
def spec_inputs(monkeypatch, tmp_path):
    config = ModuleType("PyInstaller.config")
    config.CONF = {"workpath": str(tmp_path / "build")}
    hooks = ModuleType("PyInstaller.utils.hooks")
    hooks.collect_submodules = lambda *_: []
    hooks.collect_data_files = lambda *_: []
    monkeypatch.setitem(sys.modules, "PyInstaller.config", config)
    monkeypatch.setitem(sys.modules, "PyInstaller.utils.hooks", hooks)
    monkeypatch.delenv("GREEN_ATLAS_BUNDLED_RELAY_SETTINGS", raising=False)
    monkeypatch.delenv("GREEN_ATLAS_BUNDLED_DADATA_SETTINGS", raising=False)
    tree = ast.parse(SPEC.read_text(), filename=str(SPEC))
    first_analysis = next(
        i
        for i, node in enumerate(tree.body)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "a" for target in node.targets
        )
    )
    tree.body = tree.body[:first_analysis]
    code = compile(tree, str(SPEC), "exec")

    def run():
        namespace = {"SPECPATH": str(SPEC.parent)}
        exec(code, namespace)
        return [
            (Path(source), destination)
            for source, destination in namespace["service_data"]
        ]

    return run


def release_file(tmp_path, payload):
    source = tmp_path / "private-release.json"
    source.write_text(json.dumps(payload))
    source.chmod(0o600)
    return source


def test_release_build_without_explicit_input_never_reads_owner_settings(
    spec_inputs, monkeypatch, tmp_path
):
    # A developer's valid private relay must never enter an ordinary build.
    private = tmp_path / "home/Library/Application Support/Green Atlas/AI"
    private.mkdir(parents=True)
    (private / "relay.json").write_text(
        json.dumps({"url": URL, "token": "owner-token-must-not-bundle"})
    )
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GREEN_ATLAS_LUNA_RELAY_URL", URL)
    monkeypatch.setenv(
        "GREEN_ATLAS_LUNA_RELAY_TOKEN", "environment-token-must-not-bundle"
    )
    assert spec_inputs() == []
    assert not (tmp_path / "build/private-services").exists()


def test_explicit_release_input_is_sanitized_and_private(
    spec_inputs, monkeypatch, tmp_path
):
    payload = {
        "url": URL,
        "token": TOKEN,
        "api_key": "openai-key-must-not-copy",
        "proxy": "private-proxy",
        "arbitrary": "discard",
    }
    source = release_file(tmp_path, payload)
    before = source.read_bytes()
    monkeypatch.setenv("GREEN_ATLAS_BUNDLED_RELAY_SETTINGS", str(source))
    data = spec_inputs()
    assert len(data) == 1
    path, destination = data[0]
    assert path.name == "relay.json" and destination == "Services"
    assert json.loads(path.read_text()) == {"url": URL, "token": TOKEN}
    assert source.read_bytes() == before
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    assert RelaySettings(**json.loads(path.read_text())).token == TOKEN


def test_relay_and_existing_dadata_release_inputs_coexist(
    spec_inputs, monkeypatch, tmp_path
):
    relay = release_file(tmp_path, {"url": URL, "token": TOKEN})
    dadata = tmp_path / "dadata-private.json"
    dadata.write_text(
        json.dumps({"api_key": "dummy-dadata-key", "api_secret": "discard"})
    )
    monkeypatch.setenv("GREEN_ATLAS_BUNDLED_RELAY_SETTINGS", str(relay))
    monkeypatch.setenv("GREEN_ATLAS_BUNDLED_DADATA_SETTINGS", str(dadata))
    data = spec_inputs()
    assert {path.name for path, _ in data} == {"relay.json", "dadata.json"}
    assert all(destination == "Services" for _, destination in data)
    assert json.loads(
        next(path for path, _ in data if path.name == "dadata.json").read_text()
    ) == {"api_key": "dummy-dadata-key"}


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {},
        {"url": URL},
        {"token": TOKEN},
        {"url": 5, "token": TOKEN},
        {"url": URL, "token": None},
        {"url": URL, "token": ""},
        {"url": URL, "token": "short-release-token"},
        {"url": URL, "token": "two words"},
        {"url": URL, "token": "a" * 513},
        {"url": "http://release.example/api/luna/recognize", "token": TOKEN},
        {"url": URL + "?secret=hidden", "token": TOKEN},
        {"url": "https://[bad/api/luna/recognize", "token": TOKEN},
        {"url": "https://release.example:invalid/api/luna/recognize", "token": TOKEN},
        {"url": "https://release.example:0/api/luna/recognize", "token": TOKEN},
        {"url": URL + "\n", "token": TOKEN},
        {"url": URL, "token": "ключ"},
    ],
)
def test_invalid_explicit_input_stops_build_without_echoing_secret(
    spec_inputs, monkeypatch, tmp_path, payload
):
    source = release_file(tmp_path, payload)
    monkeypatch.setenv("GREEN_ATLAS_BUNDLED_RELAY_SETTINGS", str(source))
    with pytest.raises(SystemExit) as error:
        spec_inputs()
    assert str(error.value) == "Invalid private relay release settings; value omitted"
    assert TOKEN not in str(error.value)
    assert not (tmp_path / "build/private-services/relay.json").exists()


@pytest.mark.parametrize("case", ["missing", "malformed", "large", "directory"])
def test_unreadable_explicit_input_stops_build(
    spec_inputs, monkeypatch, tmp_path, case
):
    source = tmp_path / "release-private.json"
    if case == "malformed":
        source.write_bytes(b"\xffinvalid")
    elif case == "large":
        source.write_bytes(b" " * 8193)
    elif case == "directory":
        source.mkdir()
    monkeypatch.setenv("GREEN_ATLAS_BUNDLED_RELAY_SETTINGS", str(source))
    with pytest.raises(SystemExit) as error:
        spec_inputs()
    assert "value omitted" in str(error.value)
