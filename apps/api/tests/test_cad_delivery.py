from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from app.cad_delivery.contracts import (
    TransferCreate,
    TransferDecision,
    TransferManifest,
)
from app.cad_delivery.routes import public_origin, store
from app.cad_delivery.store import TransferError, TransferStore
from app.main import app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def request_body():
    return {
        "request_id": str(uuid4()),
        "client_secret": "s" * 43,
        "plugin_version": "0.1.21",
        "manifest": {
            "entry": "Улица.dxf",
            "files": [
                {
                    "name": "Улица.dxf",
                    "kind": "drawing",
                    "sha256": "a" * 64,
                    "bytes": 100,
                },
                {
                    "name": "Улица.dxf.green-atlas.geometry.json",
                    "kind": "native_probe",
                    "sha256": "b" * 64,
                    "bytes": 200,
                },
            ],
        },
    }


@pytest.fixture
def pairing(tmp_path):
    clock = [1000.0]
    storage = TransferStore(tmp_path / "pairing.sqlite", now=lambda: clock[0])
    return storage, clock


def decision(body, code, choice="approve"):
    return TransferDecision(
        decision=choice,
        confirmation_code=code,
        manifest_sha256=TransferManifest.model_validate(body["manifest"]).digest(),
    )


def test_no_upload_permission_before_approval_and_no_secret_persisted(
    pairing, request_body
):
    storage, _ = pairing
    request = TransferCreate.model_validate(request_body)
    status, code = storage.create(request)
    with pytest.raises(TransferError, match="Подтвердите"):
        storage.require_approved(str(status.id), request_body["client_secret"])
    approved = storage.decide(str(status.id), "account-a", decision(request_body, code))
    assert approved.status == "approved"
    assert (
        storage.require_approved(str(status.id), request_body["client_secret"])
        == request.manifest
    )
    assert request_body["client_secret"].encode() not in storage.path.read_bytes()
    assert request_body["client_secret"] not in str(request)
    assert "account-a" not in status.model_dump_json()


def test_restart_retry_and_concurrent_create_return_same_receipt(pairing, request_body):
    storage, clock = pairing
    request = TransferCreate.model_validate(request_body)
    with ThreadPoolExecutor(max_workers=4) as pool:
        receipts = list(pool.map(lambda _: storage.create(request), range(8)))
    assert all(item == receipts[0] for item in receipts)
    status, code = receipts[0]
    reopened = TransferStore(storage.path, now=lambda: clock[0])
    assert reopened.create(request) == (status, code)
    once = reopened.decide(str(status.id), "owner", decision(request_body, code))
    assert (
        reopened.decide(str(status.id), "owner", decision(request_body, code)) == once
    )
    with reopened.connection() as db:
        assert db.execute("SELECT count(*) FROM cad_transfers").fetchone()[0] == 1


def test_changed_manifest_and_wrong_device_secret_never_recover_receipt(
    pairing, request_body
):
    storage, _ = pairing
    storage.create(TransferCreate.model_validate(request_body))
    changed = deepcopy(request_body)
    changed["manifest"]["files"][0]["sha256"] = "c" * 64
    with pytest.raises(TransferError, match="изменённого"):
        storage.create(TransferCreate.model_validate(changed))
    changed["client_secret"] = "x" * 43
    with pytest.raises(TransferError, match="не найдена"):
        storage.create(TransferCreate.model_validate(changed))


def test_expiration_cannot_be_extended_by_retry(pairing, request_body):
    storage, clock = pairing
    request = TransferCreate.model_validate(request_body)
    status, code = storage.create(request)
    storage.decide(str(status.id), "owner", decision(request_body, code))
    clock[0] += 601
    assert storage.create(request)[0].status == "expired"
    with pytest.raises(TransferError):
        storage.require_approved(str(status.id), request_body["client_secret"])
    with pytest.raises(TransferError) as error:
        storage.decide(str(status.id), "owner", decision(request_body, code))
    assert error.value.status == 410


@pytest.mark.parametrize("action", ["deny", "cancel"])
def test_denial_and_cancellation_are_terminal(pairing, request_body, action):
    storage, _ = pairing
    status, code = storage.create(TransferCreate.model_validate(request_body))
    if action == "deny":
        storage.decide(str(status.id), "owner", decision(request_body, code, "deny"))
    else:
        storage.cancel(str(status.id), request_body["client_secret"])
    with pytest.raises(TransferError):
        storage.decide(str(status.id), "owner", decision(request_body, code))
    with pytest.raises(TransferError):
        storage.require_approved(str(status.id), request_body["client_secret"])


def test_approval_is_owner_bound(pairing, request_body):
    storage, _ = pairing
    status, code = storage.create(TransferCreate.model_validate(request_body))
    storage.decide(str(status.id), "alice", decision(request_body, code))
    with pytest.raises(TransferError) as error:
        storage.decide(str(status.id), "bob", decision(request_body, code))
    assert error.value.status == 404
    with pytest.raises(TransferError):
        storage.review(str(status.id), "bob")


def test_deny_does_not_require_copying_code(pairing, request_body):
    storage, _ = pairing
    status, code = storage.create(TransferCreate.model_validate(request_body))
    wrong = "FFFFFFFF" if code != "FFFFFFFF" else "00000000"
    result = storage.decide(
        str(status.id), "owner", decision(request_body, wrong, "deny")
    )
    assert result.status == "denied"
    with pytest.raises(TransferError):
        storage.require_approved(str(status.id), request_body["client_secret"])


def test_expired_consent_retention_does_not_remove_recent_receipts(
    pairing, request_body
):
    storage, clock = pairing
    old, _ = storage.create(TransferCreate.model_validate(request_body))
    clock[0] += 86500
    request_body["request_id"] = str(uuid4())
    recent, _ = storage.create(TransferCreate.model_validate(request_body))
    assert storage.review(str(old.id), "owner")[0].status == "expired"
    clock[0] += 501
    request_body["request_id"] = str(uuid4())
    storage.create(TransferCreate.model_validate(request_body))
    with pytest.raises(TransferError):
        storage.review(str(old.id), "owner")
    assert storage.review(str(recent.id), "owner")[0].status == "awaiting_approval"


@pytest.mark.parametrize(
    "origin",
    [
        "https://example.com:invalid",
        "https://example.com:0",
        "https://[broken",
        "https://example.com/other",
        "http://external.example",
        "https://bad host",
        "https://user:password@example.com",
        "https://example.com?secret=123",
    ],
)
def test_invalid_public_origin_fails_closed(monkeypatch, origin):
    monkeypatch.setenv("GREEN_ATLAS_BRIDGE_ORIGIN", origin)
    with pytest.raises(HTTPException) as error:
        public_origin()
    assert error.value.status_code == 503


@pytest.mark.parametrize(
    "origin",
    [
        "https://green.example",
        "http://127.0.0.1:5173",
        "http://[::1]:5173",
    ],
)
def test_valid_public_origin(monkeypatch, origin):
    monkeypatch.setenv("GREEN_ATLAS_BRIDGE_ORIGIN", origin)
    assert public_origin() == origin


def test_code_guess_limit_is_durable(pairing, request_body):
    storage, _ = pairing
    status, code = storage.create(TransferCreate.model_validate(request_body))
    wrong = "FFFFFFFF" if code != "FFFFFFFF" else "00000000"
    for _ in range(5):
        with pytest.raises(TransferError, match="Код не совпадает"):
            storage.decide(str(status.id), "owner", decision(request_body, wrong))
    assert (
        storage.device_status(str(status.id), request_body["client_secret"]).status
        == "denied"
    )
    with pytest.raises(TransferError):
        storage.decide(str(status.id), "owner", decision(request_body, code))


def test_polling_interval(pairing, request_body):
    storage, clock = pairing
    status, _ = storage.create(TransferCreate.model_validate(request_body))
    storage.device_status(str(status.id), request_body["client_secret"])
    with pytest.raises(TransferError) as error:
        storage.device_status(str(status.id), request_body["client_secret"])
    assert error.value.status == 429
    clock[0] += 5
    assert (
        storage.device_status(str(status.id), request_body["client_secret"]).status
        == "awaiting_approval"
    )


@pytest.mark.parametrize(
    "bad_name", ["../a.dxf", "C:\\secret.dxf", "/tmp/a.dxf", "a\x00.dxf"]
)
def test_local_paths_rejected(request_body, bad_name):
    request_body["manifest"]["files"][0]["name"] = bad_name
    with pytest.raises(ValidationError):
        TransferCreate.model_validate(request_body)


def test_manifest_order_does_not_change_identity(request_body):
    original = TransferCreate.model_validate(request_body).manifest
    request_body["manifest"]["files"].reverse()
    assert (
        TransferCreate.model_validate(request_body).manifest.digest()
        == original.digest()
    )


@pytest.fixture
def client(pairing, monkeypatch):
    monkeypatch.setenv("GREEN_ATLAS_BRIDGE_ORIGIN", "https://green.example")
    monkeypatch.setenv("GREEN_ATLAS_BRIDGE_PROXY_KEY", "p" * 48)
    app.dependency_overrides[store] = lambda: pairing[0]
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.pop(store, None)


def browser_headers():
    return {
        "Origin": "https://green.example",
        "X-Green-Atlas-Proxy-Key": "p" * 48,
        "X-Green-Atlas-User": "alice",
    }


def test_http_full_pairing_no_token_in_url_or_public_output(client, request_body):
    receipt = client.post("/api/cad-bridge/device/transfers", json=request_body)
    assert receipt.status_code == 201, receipt.text
    payload = receipt.json()
    assert receipt.headers["cache-control"] == "no-store"
    assert (
        payload["verification_url"]
        == f"https://green.example/connect/autocad/{request_body['request_id']}"
    )
    assert request_body["client_secret"] not in receipt.text
    path = f"/api/cad-bridge/approvals/{payload['id']}"
    review = client.get(path, headers=browser_headers())
    assert review.status_code == 200
    assert "confirmation_code" not in review.json()
    approved = client.post(
        path,
        headers=browser_headers(),
        json=decision(request_body, payload["confirmation_code"]).model_dump(),
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"
    poll = client.get(
        f"/api/cad-bridge/device/transfers/{payload['id']}",
        headers={"X-Green-Atlas-Transfer-Token": request_body["client_secret"]},
    )
    assert poll.json()["status"] == "approved"


@pytest.mark.parametrize(
    "fault,code",
    [
        ("no_login", 401),
        ("forged_user", 401),
        ("cross_origin", 403),
        ("missing_origin", 403),
    ],
)
def test_http_rejects_untrusted_approval(client, request_body, fault, code):
    payload = client.post("/api/cad-bridge/device/transfers", json=request_body).json()
    headers = browser_headers()
    if fault == "no_login":
        headers = {}
    if fault == "forged_user":
        headers.pop("X-Green-Atlas-Proxy-Key")
    if fault == "cross_origin":
        headers["Origin"] = "https://attacker.example"
    if fault == "missing_origin":
        headers.pop("Origin")
    result = client.post(
        f"/api/cad-bridge/approvals/{payload['id']}",
        headers=headers,
        json=decision(request_body, payload["confirmation_code"]).model_dump(),
    )
    assert result.status_code == code


def test_production_route_disabled_without_configuration(monkeypatch, request_body):
    monkeypatch.delenv("GREEN_ATLAS_BRIDGE_ORIGIN", raising=False)
    with TestClient(app) as client:
        response = client.post("/api/cad-bridge/device/transfers", json=request_body)
        assert response.status_code == 503
        assert response.json()["code"] == "BRIDGE_DISABLED"
