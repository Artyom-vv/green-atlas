"""Доступ выпуска отзывается отдельно и расходует прежний общий бюджет."""

import os
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_layer_relay import answer, layer

from app.layer_recognition import providers, relay_auth, remote_server
from app.layer_recognition.api_settings import ApiSettings
from app.layer_recognition.budget import LIMIT_MICRO_USD, RecognitionBudget
from app.layer_recognition.providers import prompt
from app.layer_recognition.relay_auth import RelayAccess
from app.layer_recognition.remote_server import create_app

OWNER = "owner-" + "o" * 40
RELEASE = "release-" + "r" * 40


def write_release(path: Path, content: bytes = RELEASE.encode()) -> Path:
    path.write_bytes(content)
    path.chmod(0o600)
    return path


def request(client: TestClient, token: str) -> httpx.Response:
    return client.post(
        "/recognize",
        json={"task": "layer_roles", "source": prompt([layer()])},
        headers={"Authorization": "Bearer " + token},
    )


def provider_mock() -> Mock:
    provider = Mock()
    provider.budget.total.return_value = 0
    provider._request.return_value = answer()
    return provider


@pytest.mark.parametrize("suffix", [b"", b"\n"])
def test_separate_release_and_existing_owner_use_same_provider(
    tmp_path: Path, suffix: bytes
) -> None:
    path = write_release(tmp_path / "release.token", RELEASE.encode() + suffix)
    provider = provider_mock()
    client = TestClient(create_app(provider, OWNER, release_token_file=path))
    assert request(client, OWNER).status_code == 200
    assert request(client, RELEASE).status_code == 200
    assert request(client, "wrong-token").status_code == 401
    assert client.post("/recognize", json={}).status_code == 401
    assert provider._request.call_count == 2
    assert client.get("/health").json() == {"status": "ok"}


def test_release_revocation_and_atomic_rotation_need_no_restart(tmp_path: Path) -> None:
    path = write_release(tmp_path / "release.token")
    provider = provider_mock()
    client = TestClient(create_app(provider, OWNER, release_token_file=path))
    assert request(client, RELEASE).status_code == 200
    path.unlink()
    assert request(client, RELEASE).status_code == 401
    assert request(client, OWNER).status_code == 200
    rotated = "rotated-" + "n" * 40
    replacement = write_release(tmp_path / "new.token", rotated.encode())
    replacement.replace(path)
    assert request(client, RELEASE).status_code == 401
    assert request(client, rotated).status_code == 200
    assert request(client, OWNER).status_code == 200
    assert provider._request.call_count == 4


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"r" * 31,
        b"r" * 513,
        b"r" * 511 + b"\n\n",
        b"r" * 32 + b"\x00",
        b"r" * 32 + b"\r\n",
        b"r" * 32 + b" ",
        b"r" * 32 + b"\t",
        b"r" * 32 + b"\x7f",
        b"r" * 32 + b"\xff",
    ],
)
def test_malformed_release_fails_closed_without_breaking_owner(
    tmp_path: Path, content: bytes
) -> None:
    path = write_release(tmp_path / "release.token", content)
    provider = provider_mock()
    client = TestClient(create_app(provider, OWNER, release_token_file=path))
    assert request(client, RELEASE).status_code == 401
    assert request(client, OWNER).status_code == 200
    provider._request.assert_called_once()


@pytest.mark.parametrize("size", [32, 512])
def test_release_token_size_boundaries(tmp_path: Path, size: int) -> None:
    token = "r" * size
    path = write_release(tmp_path / "release.token", token.encode())
    assert RelayAccess(OWNER, path).allows("Bearer " + token)


@pytest.mark.parametrize("mode", [0o620, 0o604, 0o602, 0o601, 0o644, 0o666])
def test_unsafe_release_permissions_disable_only_release(
    tmp_path: Path, mode: int
) -> None:
    path = write_release(tmp_path / "release.token")
    path.chmod(mode)
    access = RelayAccess(OWNER, path)
    assert not access.allows("Bearer " + RELEASE)
    assert access.allows("Bearer " + OWNER)


def test_release_readable_by_service_group_is_supported(tmp_path: Path) -> None:
    path = write_release(tmp_path / "release.token")
    path.chmod(0o440)
    assert RelayAccess(OWNER, path).allows("Bearer " + RELEASE)


@pytest.mark.parametrize("kind", ["missing", "directory", "symlink", "fifo"])
def test_non_regular_release_source_disables_only_release(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "release.token"
    if kind == "directory":
        path.mkdir()
    elif kind == "symlink":
        path.symlink_to(write_release(tmp_path / "target.token"))
    elif kind == "fifo":
        os.mkfifo(path, 0o600)
    access = RelayAccess(OWNER, path)
    assert not access.allows("Bearer " + RELEASE)
    assert access.allows("Bearer " + OWNER)


def test_unreadable_release_file_disables_only_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_release(tmp_path / "release.token")
    monkeypatch.setattr(relay_auth.os, "open", Mock(side_effect=PermissionError))
    access = RelayAccess(OWNER, path)
    assert not access.allows("Bearer " + RELEASE)
    assert access.allows("Bearer " + OWNER)


def test_owner_token_cannot_be_reused_as_release_and_secrets_are_not_repr(
    tmp_path: Path,
) -> None:
    path = write_release(tmp_path / "release.token", OWNER.encode())
    access = RelayAccess(OWNER, path)
    assert access._release_token() is None
    assert access.allows("Bearer " + OWNER)
    assert OWNER not in repr(access)
    assert str(path) not in repr(access)


def test_unconfigured_release_retains_existing_call_contract() -> None:
    provider = provider_mock()
    client = TestClient(create_app(provider, OWNER))
    assert request(client, OWNER).status_code == 200
    assert request(client, RELEASE).status_code == 401
    with pytest.raises(ValueError, match="missing or too short"):
        create_app(provider, "short")


def test_non_ascii_authorization_is_rejected_without_exception() -> None:
    assert not RelayAccess(OWNER).allows("Bearer неверный-токен")


def test_environment_factory_passes_optional_release_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "openai_key").write_text("server-test-key")
    (tmp_path / "proxy_url").write_text("socks5://127.0.0.1:11080")
    (tmp_path / "client_token").write_text(OWNER)
    release = tmp_path / "release.token"
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("GREEN_ATLAS_RELAY_RELEASE_TOKEN_FILE", str(release))
    provider = Mock()
    monkeypatch.setattr(
        remote_server, "OpenAiLayerProvider", Mock(return_value=provider)
    )
    budget = Mock()
    monkeypatch.setattr(remote_server, "RecognitionBudget", budget)
    factory = Mock(return_value=FastAPI())
    monkeypatch.setattr(remote_server, "create_app", factory)
    remote_server.create_app_from_environment()
    factory.assert_called_once_with(provider, OWNER, release_token_file=release)
    assert budget.call_args.args == (Path("/var/lib/green-atlas-luna/budget.sqlite3"),)
    budget.return_value.total.assert_called_once_with()


def test_owner_and_release_share_unchanged_persistent_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_release(tmp_path / "release.token")
    ledger = tmp_path / "budget.sqlite3"
    budget = RecognitionBudget(ledger)
    budget.initialize()
    original_client = httpx.Client
    transport = Mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "completed",
                "usage": {"input_tokens": 1, "output_tokens": 2},
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": answer()}],
                    }
                ],
            },
        )
    )

    def factory(**_: object) -> httpx.Client:
        return original_client(transport=httpx.MockTransport(transport))

    monkeypatch.setattr(providers.httpx, "Client", factory)
    settings = ApiSettings("server-test-key", ledger=ledger)
    client = TestClient(
        create_app(
            providers.OpenAiLayerProvider(settings), OWNER, release_token_file=path
        )
    )
    assert request(client, OWNER).status_code == 200
    first_spend = budget.total()
    assert first_spend > 0
    assert request(client, RELEASE).status_code == 200
    spent = budget.total()
    assert spent == first_spend * 2
    path.unlink()
    assert request(client, RELEASE).status_code == 401
    assert budget.total() == spent
    write_release(path)
    restarted = TestClient(
        create_app(
            providers.OpenAiLayerProvider(settings), OWNER, release_token_file=path
        )
    )
    assert budget.total() == spent
    assert restarted.get("/health").status_code == 200
    assert budget.total() == spent
    budget.reserve(LIMIT_MICRO_USD - spent)
    owner_refusal = request(restarted, OWNER)
    release_refusal = request(restarted, RELEASE)
    assert owner_refusal.status_code == release_refusal.status_code == 503
    assert (
        owner_refusal.json()
        == release_refusal.json()
        == {
            "detail": {
                "schema_version": "green-atlas.recognition-failure/1",
                "code": "RECOGNITION_BUDGET_EXHAUSTED",
                "retryable": False,
                "http_status": None,
            }
        }
    )
    assert budget.total() == LIMIT_MICRO_USD == 2_000_000
    assert transport.call_count == 2
    with sqlite3.connect(ledger) as database:
        assert database.execute("SELECT limit_usd FROM allowance").fetchall() == [
            (2_000_000,)
        ]
        assert database.execute("SELECT COUNT(*) FROM requests").fetchone() == (3,)
