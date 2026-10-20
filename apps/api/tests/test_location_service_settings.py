"""Settings are private account overrides, never project fields or public keys."""

import json
import os

import httpx
import pytest
from fastapi.testclient import TestClient

from app.desktop.web import SESSION_COOKIE, SESSION_HEADER, create_desktop_web
from app.location import providers, settings
from app.main import app

ORIGIN = "http://127.0.0.1:18327"
SECRET = "s" * 43
ENDPOINT = "/api/location/settings"


@pytest.fixture
def private_settings(monkeypatch, tmp_path):
    personal = tmp_path / "profile/Location/dadata.json"
    bundled = tmp_path / "runtime/Services/dadata.json"
    monkeypatch.setenv("GREEN_ATLAS_LOCATION_SETTINGS", str(personal))
    monkeypatch.delenv("DADATA_API_KEY", raising=False)
    monkeypatch.setattr(settings.sys, "frozen", True, raising=False)
    monkeypatch.setattr(
        settings.sys, "_MEIPASS", str(bundled.parent.parent), raising=False
    )
    return personal, bundled


def write_key(path, token, *, private=True):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.write_text(json.dumps({"api_key": token}), encoding="utf-8")
    path.chmod(0o600 if private else 0o644)


@pytest.fixture
def desktop(private_settings, tmp_path):
    (tmp_path / "index.html").write_text("<title>Fixture</title>")
    web = create_desktop_web(app, tmp_path, origin=ORIGIN, secret=SECRET)
    with TestClient(web, base_url=ORIGIN, client=("127.0.0.1", 50001)) as client:
        client.cookies.set(SESSION_COOKIE, SECRET)
        client.headers["Origin"] = ORIGIN
        yield client


def test_precedence_personal_environment_frozen_default_and_reset(
    private_settings, monkeypatch
):
    personal, bundled = private_settings
    assert settings.load_dadata_token() is None
    assert settings.get_location_service_settings().model_dump() == {
        "configured": False,
        "source": "none",
        "personal_key_configured": False,
        "bundled_key_configured": False,
    }
    write_key(bundled, "release-default", private=False)
    assert settings.load_dadata_token() == "release-default"
    assert settings.get_location_service_settings().source == "bundled"
    monkeypatch.setenv("DADATA_API_KEY", "environment-key")
    assert settings.load_dadata_token() == "environment-key"
    assert settings.get_location_service_settings().source == "environment"
    assert settings.save_dadata_token("personal-key").source == "personal"
    assert settings.load_dadata_token() == "personal-key"
    assert settings.reset_dadata_token().source == "environment"
    assert not personal.exists()
    monkeypatch.delenv("DADATA_API_KEY")
    assert settings.get_location_service_settings().source == "bundled"


def test_source_checkout_never_loads_release_file(private_settings, monkeypatch):
    _, bundled = private_settings
    write_key(bundled, "must-not-load", private=False)
    monkeypatch.setattr(settings.sys, "frozen", False)
    assert settings.load_dadata_token() is None


def test_frozen_default_path_cannot_be_redirected_by_environment(
    private_settings, monkeypatch, tmp_path
):
    _, bundled = private_settings
    other = tmp_path / "other.json"
    write_key(other, "other-key")
    monkeypatch.setenv("GREEN_ATLAS_BUNDLED_DADATA_SETTINGS", str(other))
    assert settings.bundled_dadata_settings_path() == bundled
    assert settings.load_dadata_token() is None


def test_invalid_personal_setting_does_not_fall_back(private_settings, monkeypatch):
    personal, bundled = private_settings
    write_key(bundled, "release-default", private=False)
    write_key(personal, "own-key")
    monkeypatch.setenv("DADATA_API_KEY", "env-key")
    personal.write_text("broken")
    with pytest.raises(settings.LocationProviderError) as error:
        settings.load_dadata_token()
    assert error.value.code == "settings_unreadable"


def test_invalid_bundle_does_not_prevent_valid_personal_override(private_settings):
    personal, bundled = private_settings
    write_key(bundled, "default", private=False)
    bundled.write_text("broken")
    with pytest.raises(settings.LocationProviderError):
        settings.load_dadata_token()
    write_key(personal, "own-key")
    assert settings.load_dadata_token() == "own-key"
    assert not settings.get_location_service_settings().bundled_key_configured


@pytest.mark.parametrize(
    "value", ["", " ", "key with space", "key\nline", "ключ", 12, None]
)
def test_bounded_printable_key_validation_does_not_echo_secret(desktop, value):
    marker = "INVALID-SECRET-EXAMPLE"
    payload = {"api_key": value, "unexpected": marker}
    response = desktop.put(ENDPOINT, json=payload)
    assert response.status_code == 422
    assert marker not in response.text
    assert "api_key" in response.json()["field_errors"]


def test_settings_http_returns_status_only_and_reset_reactivates_default(
    desktop, private_settings, monkeypatch
):
    personal, bundled = private_settings
    write_key(bundled, "default-example-key", private=False)
    # Credential settings must not read or mutate any project.
    import app.composition

    monkeypatch.setattr(
        app.composition, "get_application", lambda: pytest.fail("project access")
    )
    assert desktop.get(ENDPOINT).json()["source"] == "bundled"
    saved = desktop.put(ENDPOINT, json={"api_key": "my-example-key"})
    assert saved.status_code == 200
    assert saved.json() == {
        "configured": True,
        "source": "personal",
        "personal_key_configured": True,
        "bundled_key_configured": True,
    }
    assert "my-example-key" not in saved.text
    assert "default-example-key" not in saved.text
    assert saved.headers["cache-control"] == "no-store"
    assert settings.load_dadata_token() == "my-example-key"
    if os.name != "nt":
        assert personal.stat().st_mode & 0o777 == 0o600
        assert personal.parent.stat().st_mode & 0o777 == 0o700
    removed = desktop.delete(ENDPOINT)
    assert removed.status_code == 200
    assert removed.json()["source"] == "bundled"
    assert not personal.exists()


@pytest.mark.parametrize("method", ["GET", "PUT", "DELETE"])
def test_standalone_api_rejects_even_loopback_forged_native_header_before_file_access(
    method, monkeypatch
):
    monkeypatch.setattr(
        settings, "dadata_settings_path", lambda: pytest.fail("settings read")
    )
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50001)) as client:
        response = client.request(
            method,
            ENDPOINT,
            json={"api_key": "other-key"},
            headers={SESSION_HEADER: SECRET, "Origin": ORIGIN},
        )
    assert response.status_code == 403
    assert response.json()["code"] == "LOCAL_SESSION_REQUIRED"
    assert "other-key" not in response.text


def test_native_session_and_cookie_session_both_work(desktop):
    assert desktop.get(ENDPOINT).status_code == 200
    desktop.cookies.clear()
    assert desktop.get(ENDPOINT).status_code == 403
    assert desktop.get(ENDPOINT, headers={SESSION_HEADER: SECRET}).status_code == 200


def test_invalid_cookie_and_foreign_origin_cannot_edit_settings(
    desktop, private_settings
):
    personal, _ = private_settings
    assert (
        desktop.put(
            ENDPOINT,
            json={"api_key": "must-not-save"},
            headers={"Origin": "https://foreign.example"},
        ).status_code
        == 403
    )
    desktop.cookies.set(SESSION_COOKIE, "wrong")
    assert desktop.put(ENDPOINT, json={"api_key": "must-not-save"}).status_code == 403
    assert not personal.exists()


@pytest.mark.parametrize("failure", ["file_fsync", "directory_fsync", "replace"])
def test_failed_staging_preserves_previous_key_and_leaves_no_temp_files(
    private_settings, monkeypatch, failure
):
    personal, _ = private_settings
    write_key(personal, "old-key")
    before = personal.read_bytes()
    if failure == "replace":
        monkeypatch.setattr(
            settings.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk"))
        )
    elif failure == "directory_fsync":
        monkeypatch.setattr(
            settings,
            "_sync_directory",
            lambda *_: (_ for _ in ()).throw(OSError("disk")),
        )
    else:
        monkeypatch.setattr(
            settings.os, "fsync", lambda *_: (_ for _ in ()).throw(OSError("disk"))
        )
    with pytest.raises(settings.LocationProviderError):
        settings.save_dadata_token("new-key")
    assert personal.read_bytes() == before
    assert list(personal.parent.glob(".dadata-*")) == []


@pytest.mark.parametrize("link_parent", [False, True])
def test_symlinks_are_not_read_written_or_removed(
    private_settings, tmp_path, link_parent
):
    personal, _ = private_settings
    target = tmp_path / "unrelated/dadata.json"
    write_key(target, "must-stay")
    personal.parent.mkdir(parents=True, exist_ok=True)
    if link_parent:
        personal.parent.rmdir()
        personal.parent.symlink_to(target.parent, target_is_directory=True)
    else:
        personal.symlink_to(target)
    for action in (
        settings.load_dadata_token,
        lambda: settings.save_dadata_token("replacement"),
        settings.reset_dadata_token,
    ):
        with pytest.raises(settings.LocationProviderError):
            action()
    assert json.loads(target.read_text())["api_key"] == "must-stay"


@pytest.mark.parametrize(
    "contents", ["[]", "null", '{"api_key":42}', '{"api_key":""}', "x" * 8193]
)
def test_unreadable_bundled_config_is_sanitized(desktop, private_settings, contents):
    _, bundled = private_settings
    write_key(bundled, "default", private=False)
    bundled.write_text(contents)
    response = desktop.get(ENDPOINT)
    assert response.status_code == 409
    assert response.json()["code"] == "settings_unreadable"
    assert str(bundled) not in response.text
    assert contents not in response.text


def test_personal_auth_failure_never_retries_default(private_settings):
    personal, bundled = private_settings
    write_key(personal, "invalid-personal-key")
    write_key(bundled, "valid-default", private=False)
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(401, json={"message": "denied"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(providers.LocationProviderError) as error:
            providers.suggest_addresses("Москва", kind="city", client=client)
    assert error.value.code == "provider_auth"
    assert len(requests) == 1
    assert requests[0].headers["Authorization"] == "Token invalid-personal-key"


def test_personal_settings_are_separate_for_different_profiles(
    private_settings, monkeypatch, tmp_path
):
    first, bundled = private_settings
    write_key(bundled, "default", private=False)
    settings.save_dadata_token("profile-one")
    second = tmp_path / "second-user/Location/dadata.json"
    monkeypatch.setenv("GREEN_ATLAS_LOCATION_SETTINGS", str(second))
    assert settings.get_location_service_settings().source == "bundled"
    settings.save_dadata_token("profile-two")
    assert settings.load_dadata_token() == "profile-two"
    assert json.loads(first.read_text())["api_key"] == "profile-one"
    settings.reset_dadata_token()
    assert json.loads(first.read_text())["api_key"] == "profile-one"


def test_project_contracts_contain_no_service_secrets():
    from app.projects.contracts import Project

    schema = json.dumps(Project.model_json_schema())
    for field in ("api_key", "personal_key_configured", "bundled_key_configured"):
        assert field not in schema
