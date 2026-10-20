"""Explicit relay accounts never silently fall back to the frozen release account."""

import json
import os
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from app.dxf_import.layers.contracts import Layer, LayerKind
from app.layer_recognition import (
    api_settings,
    providers,
    relay_provider,
    relay_settings,
)
from app.layer_recognition.budget import RecognitionUnavailable
from app.shared.failures import FailureReason

URL = "https://relay.example/api/luna/recognize"
TOKEN = "dummy-release-token-for-isolated-tests"


def write(path, *, token=TOKEN, url=URL, private=True, payload=None):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(
        json.dumps(payload if payload is not None else {"url": url, "token": token})
    )
    path.chmod(0o600 if private else 0o644)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    personal = tmp_path / "home/AI/relay.json"
    bundled = tmp_path / "runtime/Services/relay.json"
    monkeypatch.setattr(relay_settings, "settings_directory", lambda: personal.parent)
    monkeypatch.setattr(api_settings, "settings_directory", lambda: personal.parent)
    monkeypatch.setattr(providers, "settings_directory", lambda: personal.parent)
    monkeypatch.setattr(relay_settings.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        relay_settings.sys, "_MEIPASS", str(bundled.parent.parent), raising=False
    )
    monkeypatch.delenv("GREEN_ATLAS_LUNA_RELAY_URL", raising=False)
    monkeypatch.delenv("GREEN_ATLAS_LUNA_RELAY_TOKEN", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_PROXY_URL", raising=False)
    return personal, bundled


def test_clean_frozen_default_does_not_write_personal_settings(settings):
    personal, bundled = settings
    assert relay_settings.load_relay_settings() is None
    write(bundled, private=False)
    assert relay_settings.load_relay_settings() == relay_settings.RelaySettings(
        URL, TOKEN, bundled=True
    )
    assert not personal.parent.exists()
    assert TOKEN not in repr(relay_settings.load_relay_settings())


def test_source_checkout_never_reads_release_file(settings, monkeypatch):
    _, bundled = settings
    write(bundled, private=False)
    monkeypatch.setattr(relay_settings.sys, "frozen", False)
    assert relay_settings.load_relay_settings() is None


def test_runtime_bundle_path_cannot_be_redirected(settings, monkeypatch, tmp_path):
    _, bundled = settings
    elsewhere = tmp_path / "redirect.json"
    write(elsewhere, token="must-not-load")
    monkeypatch.setenv("GREEN_ATLAS_BUNDLED_RELAY_SETTINGS", str(elsewhere))
    assert relay_settings.bundled_relay_settings_path() == bundled
    assert relay_settings.load_relay_settings() is None


def test_bundled_release_token_has_server_minimum_length(settings):
    personal, bundled = settings
    write(bundled, token="short-release-token", private=False)
    with pytest.raises(RecognitionUnavailable) as error:
        relay_settings.load_relay_settings()
    assert error.value.reason == FailureReason.RECOGNITION_RELAY_SETTINGS_INVALID
    # Existing private account formats remain compatible and take precedence.
    write(personal, token="legacy-private-token")
    assert relay_settings.load_relay_settings().token == "legacy-private-token"


def test_existing_environment_overrides_personal_and_personal_overrides_release(
    settings, monkeypatch
):
    personal, bundled = settings
    write(bundled, private=False)
    write(
        personal,
        url="https://personal.example/api/luna/recognize",
        token="personal-token",
    )
    before = personal.read_bytes()
    selected = relay_settings.load_relay_settings()
    assert selected.url == "https://personal.example/api/luna/recognize"
    assert selected.token == "personal-token"
    # Preserve the prior field-level environment-over-personal selection.
    monkeypatch.setenv("GREEN_ATLAS_LUNA_RELAY_TOKEN", "environment-token")
    selected = relay_settings.load_relay_settings()
    assert selected.url == "https://personal.example/api/luna/recognize"
    assert selected.token == "environment-token"
    monkeypatch.setenv("GREEN_ATLAS_LUNA_RELAY_URL", URL)
    assert relay_settings.load_relay_settings().url == URL
    assert personal.read_bytes() == before


@pytest.mark.parametrize(
    "payload", [{}, {"url": URL}, {"token": "own"}, {"url": URL, "token": 7}]
)
def test_incomplete_personal_override_blocks_bundle(settings, payload):
    personal, bundled = settings
    write(bundled, private=False)
    write(personal, payload=payload)
    with pytest.raises(RecognitionUnavailable) as error:
        relay_settings.load_relay_settings()
    assert error.value.reason == FailureReason.RECOGNITION_RELAY_SETTINGS_INVALID
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize("data", [b"broken", b"[]", b"\xff", b" " * 8193])
def test_unreadable_personal_override_blocks_bundle(settings, data):
    personal, bundled = settings
    write(bundled, private=False)
    write(personal)
    personal.write_bytes(data)
    with pytest.raises(RecognitionUnavailable) as error:
        relay_settings.load_relay_settings()
    assert error.value.reason == FailureReason.RECOGNITION_RELAY_SETTINGS_UNREADABLE


@pytest.mark.parametrize(
    "values",
    [
        {"GREEN_ATLAS_LUNA_RELAY_URL": URL},
        {"GREEN_ATLAS_LUNA_RELAY_TOKEN": "own"},
        {"GREEN_ATLAS_LUNA_RELAY_URL": "", "GREEN_ATLAS_LUNA_RELAY_TOKEN": ""},
    ],
)
def test_partial_or_empty_explicit_environment_never_uses_bundle(
    settings, monkeypatch, values
):
    _, bundled = settings
    write(bundled, private=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(RecognitionUnavailable) as error:
        relay_settings.load_relay_settings()
    assert error.value.reason == FailureReason.RECOGNITION_RELAY_SETTINGS_INVALID


def test_invalid_bundle_is_irrelevant_to_valid_explicit_account(settings, monkeypatch):
    personal, bundled = settings
    write(bundled, private=False)
    bundled.write_text("invalid")
    with pytest.raises(RecognitionUnavailable):
        relay_settings.load_relay_settings()
    write(personal, token="personal-token")
    assert relay_settings.load_relay_settings().token == "personal-token"
    personal.unlink()
    monkeypatch.setenv("GREEN_ATLAS_LUNA_RELAY_URL", URL)
    monkeypatch.setenv("GREEN_ATLAS_LUNA_RELAY_TOKEN", "environment-token")
    assert relay_settings.load_relay_settings().token == "environment-token"


@pytest.mark.skipif(os.name == "nt", reason="POSIX account settings permissions")
def test_unsafe_personal_permissions_block_bundle(settings):
    personal, bundled = settings
    write(bundled, private=False)
    write(personal)
    personal.chmod(0o644)
    with pytest.raises(RecognitionUnavailable) as error:
        relay_settings.load_relay_settings()
    assert error.value.reason == FailureReason.RECOGNITION_RELAY_SETTINGS_UNREADABLE


@pytest.mark.parametrize(
    "kind", ["link", "parent_link", "hardlink", "directory", "fifo"]
)
def test_non_regular_or_aliased_personal_settings_are_rejected(
    settings, tmp_path, kind
):
    personal, _ = settings
    target = tmp_path / "private.json"
    write(target)
    personal.parent.mkdir(parents=True, exist_ok=True)
    if kind == "link":
        personal.symlink_to(target)
    elif kind == "parent_link":
        personal.parent.rmdir()
        folder = tmp_path / "linked-folder"
        write(folder / "relay.json")
        personal.parent.symlink_to(folder, target_is_directory=True)
    elif kind == "hardlink":
        os.link(target, personal)
    elif kind == "directory":
        personal.mkdir()
    else:
        if not hasattr(os, "mkfifo"):
            pytest.skip("No POSIX fifo")
        os.mkfifo(personal, 0o600)
    with pytest.raises(RecognitionUnavailable) as error:
        relay_settings.load_relay_settings()
    assert error.value.reason == FailureReason.RECOGNITION_RELAY_SETTINGS_UNREADABLE


@pytest.mark.parametrize(
    "url,token",
    [
        (None, TOKEN),
        (12, TOKEN),
        (URL, None),
        (URL, 12),
        (URL, ""),
        (URL, " "),
        (URL, "two words"),
        (URL, "token\nline"),
        (URL, "ключ"),
        (URL, "a" * 513),
        ("http://relay.example/api/luna/recognize", TOKEN),
        (URL + "?key=private", TOKEN),
        (URL + "#fragment", TOKEN),
        ("https://user:password@relay.example/api/luna/recognize", TOKEN),
        ("https://relay.example:wrong/api/luna/recognize", TOKEN),
        ("https://relay.example:0/api/luna/recognize", TOKEN),
        ("https://[broken/api/luna/recognize", TOKEN),
        ("https://relay.example/api/luna/recognize\n", TOKEN),
        ("https://relay.example/" + "a" * 2048, TOKEN),
    ],
)
def test_strict_credential_validation_never_echoes_values(url, token):
    with pytest.raises(RecognitionUnavailable) as error:
        relay_settings.RelaySettings(url, token)
    assert error.value.reason == FailureReason.RECOGNITION_RELAY_SETTINGS_INVALID
    assert TOKEN not in str(error.value)
    assert "password" not in str(error.value)


def test_auto_uses_frozen_release_only_when_no_explicit_account(settings, monkeypatch):
    personal, bundled = settings
    write(bundled, private=False)
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    selected = providers.configured_provider()
    assert isinstance(selected, relay_provider.RelayLayerProvider)
    assert selected.relay_settings.bundled
    assert not personal.parent.exists()


@pytest.mark.parametrize("mode", ["personal", "environment"])
def test_auto_explicit_relay_wins_and_never_reads_openai(settings, monkeypatch, mode):
    personal, bundled = settings
    write(bundled, private=False)
    if mode == "personal":
        write(personal, token="own-relay-token")
    else:
        monkeypatch.setenv("GREEN_ATLAS_LUNA_RELAY_URL", URL)
        monkeypatch.setenv("GREEN_ATLAS_LUNA_RELAY_TOKEN", "own-relay-token")
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    monkeypatch.setattr(
        providers,
        "load_api_settings",
        Mock(side_effect=AssertionError("OpenAI settings read")),
    )
    selected = providers.configured_provider()
    assert isinstance(selected, relay_provider.RelayLayerProvider)
    assert selected.relay_settings.token == "own-relay-token"
    assert not selected.relay_settings.bundled


@pytest.mark.parametrize("mode", ["personal", "environment"])
def test_auto_explicit_openai_wins_over_release_default(settings, monkeypatch, mode):
    personal, bundled = settings
    write(bundled, private=False)
    own_key = "dummy-personal-openai-key"
    if mode == "personal":
        write(personal.parent / "openai.json", payload={"api_key": own_key})
    else:
        monkeypatch.setenv("OPENAI_API_KEY", own_key)
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    selected = providers.configured_provider()
    assert type(selected) is providers.OpenAiLayerProvider
    assert selected.settings.key == own_key
    assert not personal.exists()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        {},
        {"api_key": ""},
        {"api_key": 5},
        {"api_key": "own", "proxy_url": "invalid"},
    ],
)
def test_malformed_explicit_openai_file_never_falls_back_to_release(
    settings, monkeypatch, payload
):
    personal, bundled = settings
    write(bundled, private=False)
    path = personal.parent / "openai.json"
    write(path, payload={} if payload is None else payload)
    if payload is None:
        path.write_text("malformed-json")
    before = path.read_bytes()
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    with pytest.raises(RecognitionUnavailable):
        providers.configured_provider()
    assert path.read_bytes() == before
    assert not personal.exists()


@pytest.mark.parametrize(
    "values",
    [
        {"OPENAI_API_KEY": ""},
        {"OPENAI_PROXY_URL": ""},
        {"OPENAI_PROXY_URL": "socks5://localhost:11080"},
    ],
)
def test_incomplete_explicit_openai_environment_never_uses_release(
    settings, monkeypatch, values
):
    _, bundled = settings
    write(bundled, private=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    with pytest.raises(RecognitionUnavailable) as error:
        providers.configured_provider()
    assert error.value.reason == FailureReason.RECOGNITION_OPENAI_KEY_MISSING


def test_explicit_relay_provider_choice_keeps_forced_release_behavior(
    settings, monkeypatch
):
    _, bundled = settings
    write(bundled, private=False)
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "relay")
    monkeypatch.setattr(
        providers,
        "load_api_settings",
        Mock(side_effect=AssertionError("OpenAI settings read")),
    )
    selected = providers.configured_provider()
    assert isinstance(selected, relay_provider.RelayLayerProvider)
    assert selected.relay_settings.bundled


def test_upstream_refusal_never_switches_from_personal_to_release(
    settings, monkeypatch
):
    personal, bundled = settings
    write(personal, token="personal-token")
    write(bundled, private=False)
    requested_tokens = []

    def respond(request):
        requested_tokens.append(request.headers["Authorization"])
        return httpx.Response(401)

    original = httpx.Client
    monkeypatch.setattr(
        relay_provider.httpx,
        "Client",
        lambda **_: original(transport=httpx.MockTransport(respond)),
    )
    source = Layer(
        id="one",
        source_name="Контур",
        suggested_kind=LayerKind.RESTRICTED,
        object_count=1,
        color="#000000",
        entity_types={"LINE": 1},
    )
    with pytest.raises(RecognitionUnavailable):
        relay_provider.RelayLayerProvider(relay_settings.load_relay_settings()).propose(
            [source]
        )
    assert requested_tokens == ["Bearer personal-token"]
    assert Path(personal).is_file()
