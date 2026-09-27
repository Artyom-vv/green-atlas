import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from app.cad_delivery import uploads as module
from app.cad_delivery.contracts import TransferCreate, TransferDecision
from app.cad_delivery.routes import store, upload_chunk
from app.cad_delivery.store import TransferError, TransferStore
from app.cad_delivery.uploads import UPLOAD_TTL, UploadStore
from app.main import app
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "CHUNK_BYTES", 4)
    now = [1000]
    consent = TransferStore(tmp_path / "consent.sqlite", now=lambda: now[0])
    files = [b"abcdefghij", b"{native}"]
    request = TransferCreate.model_validate(
        {
            "request_id": str(uuid4()),
            "client_secret": "s" * 43,
            "plugin_version": "0.1.21",
            "manifest": {
                "entry": "Улица.dxf",
                "files": [
                    {
                        "name": name,
                        "kind": kind,
                        "sha256": sha256(data).hexdigest(),
                        "bytes": len(data),
                    }
                    for name, kind, data in zip(
                        ["Улица.dxf", "Улица.dxf.green-atlas.geometry.json"],
                        ["drawing", "native_probe"],
                        files,
                        strict=True,
                    )
                ],
            },
        }
    )
    receipt, code = consent.create(request)
    consent.decide(
        str(receipt.id),
        "alice",
        TransferDecision(
            decision="approve",
            confirmation_code=code,
            manifest_sha256=request.manifest.digest(),
        ),
    )
    return UploadStore(consent), now, str(receipt.id), "s" * 43, files, request


def send(storage, identifier, token, index, offset, data):
    return storage.append(
        identifier, token, index, offset, data, sha256(data).hexdigest()
    )


def send_all(package):
    storage, _, identifier, token, files, _ = package
    storage.begin(identifier, token)
    for index, data in enumerate(files):
        for offset in range(0, len(data), 4):
            send(storage, identifier, token, index, offset, data[offset : offset + 4])


def test_resume_restart_duplicate_and_seal(package):
    storage, now, identifier, token, files, _ = package
    lease = storage.begin(identifier, token)
    sent = send(storage, identifier, token, 0, 0, files[0][:4])
    assert sent.files[0].received_bytes == 4
    now[0] += 601  # Consent expired; already started upload still authorized.
    reopened = UploadStore(TransferStore(storage.consent.path, now=lambda: now[0]))
    assert reopened.begin(identifier, token).expires_at == lease.expires_at
    assert send(reopened, identifier, token, 0, 0, files[0][:4]) == sent
    for index, data in enumerate(files):
        for offset in range(4 if index == 0 else 0, len(data), 4):
            send(reopened, identifier, token, index, offset, data[offset : offset + 4])
    ready = reopened.finish(identifier, token)
    assert ready.status == "ready"
    assert reopened.finish(identifier, token) == ready
    for i, data in enumerate(files):
        assert reopened._file(identifier, i).read_bytes() == data


def test_no_approval_no_files(package):
    storage, _, identifier, token, _, _ = package
    storage.consent.cancel(identifier, token)
    with pytest.raises(TransferError):
        storage.begin(identifier, token)
    assert list(storage.root.iterdir()) == []


def test_wrong_token_and_expired_lease(package):
    storage, now, identifier, token, _, _ = package
    with pytest.raises(TransferError) as error:
        storage.begin(identifier, "wrong" * 10)
    assert error.value.status == 404
    storage.begin(identifier, token)
    now[0] += UPLOAD_TTL
    with pytest.raises(TransferError) as error:
        storage.progress(identifier, token)
    assert error.value.status == 410


def test_cannot_start_upload_after_consent_expired(package):
    storage, now, identifier, token, _, _ = package
    now[0] += 601
    with pytest.raises(TransferError) as error:
        storage.begin(identifier, token)
    assert error.value.status == 410


@pytest.mark.parametrize(
    "index,offset,data,digest,code",
    [
        (2, 0, b"abcd", sha256(b"abcd").hexdigest(), "FILE_NOT_LISTED"),
        (0, 5, b"abcd", sha256(b"abcd").hexdigest(), "UPLOAD_OFFSET"),
        (0, 0, b"abcd", "0" * 64, "CHUNK_HASH"),
        (0, 0, b"a", sha256(b"a").hexdigest(), "CHUNK_SIZE"),
        (0, 0, b"abcde", sha256(b"abcde").hexdigest(), "CHUNK_SIZE"),
    ],
)
def test_invalid_chunks_never_advance(package, index, offset, data, digest, code):
    storage, _, identifier, token, _, _ = package
    storage.begin(identifier, token)
    with pytest.raises(TransferError) as error:
        storage.append(identifier, token, index, offset, data, digest)
    assert error.value.code == code
    assert storage.progress(identifier, token).files[0].received_bytes == 0


def test_concurrent_duplicate_appends_only_once(package):
    storage, _, identifier, token, files, _ = package
    storage.begin(identifier, token)
    with ThreadPoolExecutor(max_workers=4) as pool:
        result = list(
            pool.map(
                lambda _: send(storage, identifier, token, 0, 0, files[0][:4]), range(8)
            )
        )
    assert all(r.files[0].received_bytes == 4 for r in result)
    assert storage._file(identifier, 0).stat().st_size == 4
    with pytest.raises(TransferError, match="другие"):
        send(storage, identifier, token, 0, 0, b"zzzz")


def test_failed_write_tail_reconciled_on_retry(package, monkeypatch):
    storage, _, identifier, token, files, _ = package
    storage.begin(identifier, token)
    sync = module.os.fsync

    def fail_sync(_):
        raise OSError("disk failure")

    monkeypatch.setattr(module.os, "fsync", fail_sync)
    with pytest.raises(TransferError) as error:
        send(storage, identifier, token, 0, 0, files[0][:4])
    assert error.value.code == "STAGING_WRITE_FAILED"
    assert storage.progress(identifier, token).files[0].received_bytes == 0
    monkeypatch.setattr(module.os, "fsync", sync)
    send(storage, identifier, token, 0, 0, files[0][:4])
    assert storage._file(identifier, 0).read_bytes() == files[0][:4]


def test_full_hash_and_completeness_checked(package):
    storage, _, identifier, token, _, _ = package
    storage.begin(identifier, token)
    with pytest.raises(TransferError) as error:
        storage.finish(identifier, token)
    assert error.value.code == "UPLOAD_INCOMPLETE"
    send_all(package)
    with storage._file(identifier, 0).open("r+b") as file:
        file.write(b"Z")
    with pytest.raises(TransferError) as error:
        storage.finish(identifier, token)
    assert error.value.code == "FILE_HASH"
    assert storage.progress(identifier, token).status == "uploading"


def test_cancel_during_hash_prevents_ready(package, monkeypatch):
    storage, _, identifier, token, _, _ = package
    send_all(package)

    class CancellingDigest:
        def __init__(self):
            self.delegate = sha256()

        def update(self, data):
            self.delegate.update(data)

        def hexdigest(self):
            storage.consent.cancel(identifier, token)
            return self.delegate.hexdigest()

    monkeypatch.setattr(module, "sha256", CancellingDigest)
    with pytest.raises(TransferError):
        storage.finish(identifier, token)
    with storage.consent.connection() as db:
        assert (
            db.execute(
                "SELECT ready FROM cad_uploads WHERE id=?", (identifier,)
            ).fetchone()[0]
            == 0
        )


def test_bounded_reservation_and_cleanup_only_transfer_files(
    package, monkeypatch, tmp_path
):
    storage, now, identifier, token, files, _ = package
    monkeypatch.setattr(module, "STAGING_BUDGET", 1)
    with pytest.raises(TransferError) as error:
        storage.begin(identifier, token)
    assert error.value.status == 507
    monkeypatch.setattr(module, "STAGING_BUDGET", 1024)
    storage.begin(identifier, token)
    send(storage, identifier, token, 0, 0, files[0][:4])
    original = tmp_path / "original.dxf"
    original.write_bytes(b"do not touch")
    now[0] += UPLOAD_TTL
    storage.collect_expired()
    assert not storage._directory(identifier).exists()
    assert original.read_bytes() == b"do not touch"
    with storage.consent.connection() as db:
        assert db.execute("SELECT count(*) FROM cad_uploads").fetchone()[0] == 0


def test_disconnected_body_does_not_commit_partial_chunk(package):
    storage, _, identifier, token, _, _ = package
    storage.begin(identifier, token)
    messages = iter(
        [
            {"type": "http.request", "body": b"ab", "more_body": True},
            {"type": "http.disconnect"},
        ]
    )

    async def receive():
        return next(messages)

    request = Request(
        {"type": "http", "headers": [(b"content-type", b"application/octet-stream")]},
        receive,
    )
    with pytest.raises(ClientDisconnect):
        asyncio.run(
            upload_chunk(
                UUID(identifier),
                0,
                request,
                Response(),
                0,
                sha256(b"abcd").hexdigest(),
                token,
                storage,
            )
        )
    assert storage.progress(identifier, token).files[0].received_bytes == 0
    assert not storage._directory(identifier).exists()


def test_unknown_staging_file_preserved_without_blocking_new_upload(package, caplog):
    storage, now, identifier, token, _, original = package
    storage.begin(identifier, token)
    directory = storage._directory(identifier)
    directory.mkdir()
    unrelated = directory / "operator-note.txt"
    unrelated.write_text("keep")
    now[0] += UPLOAD_TTL
    request = original.model_copy(deep=True, update={"request_id": uuid4()})
    receipt, code = storage.consent.create(request)
    storage.consent.decide(
        str(receipt.id),
        "alice",
        TransferDecision(
            decision="approve",
            confirmation_code=code,
            manifest_sha256=request.manifest.digest(),
        ),
    )
    assert storage.begin(str(receipt.id), token).status == "uploading"
    assert unrelated.read_text() == "keep"
    assert "cleanup requires attention" in caplog.text
    with storage.consent.connection() as db:
        assert db.execute("SELECT count(*) FROM cad_uploads").fetchone()[0] == 2


def test_staging_symlink_never_written(package, tmp_path):
    storage, _, identifier, token, files, _ = package
    storage.begin(identifier, token)
    outside = tmp_path / "user-data"
    outside.mkdir()
    storage._directory(identifier).symlink_to(outside, target_is_directory=True)
    with pytest.raises(TransferError) as error:
        send(storage, identifier, token, 0, 0, files[0][:4])
    assert error.value.code == "STAGING_UNAVAILABLE"
    assert list(outside.iterdir()) == []


def test_complete_chunk_size_on_real_four_megabyte_boundary(package, monkeypatch):
    storage, _, _, token, _, original = package
    monkeypatch.setattr(module, "CHUNK_BYTES", 4 * 1024**2)
    data = b"a" * (module.CHUNK_BYTES + 7)
    request = original.model_copy(deep=True, update={"request_id": uuid4()})
    request.manifest.files[0].bytes = len(data)
    request.manifest.files[0].sha256 = sha256(data).hexdigest()
    receipt, code = storage.consent.create(request)
    identifier = str(receipt.id)
    storage.consent.decide(
        identifier,
        "alice",
        TransferDecision(
            decision="approve",
            confirmation_code=code,
            manifest_sha256=request.manifest.digest(),
        ),
    )
    storage.begin(identifier, token)
    send(storage, identifier, token, 0, 0, data[: module.CHUNK_BYTES])
    result = send(
        storage, identifier, token, 0, module.CHUNK_BYTES, data[module.CHUNK_BYTES :]
    )
    assert result.files[0].received_bytes == len(data)
    assert (
        sha256(storage._file(identifier, 0).read_bytes()).hexdigest()
        == request.manifest.files[0].sha256
    )


def test_http_chunk_resume_finish_and_untrusted_access(package, monkeypatch):
    storage, _, identifier, token, files, _ = package
    monkeypatch.setenv("GREEN_ATLAS_BRIDGE_ORIGIN", "https://green.example")
    monkeypatch.setenv("GREEN_ATLAS_BRIDGE_PROXY_KEY", "p" * 48)
    app.dependency_overrides[store] = lambda: storage.consent
    base = f"/api/cad-bridge/device/transfers/{identifier}"
    headers = {"X-Green-Atlas-Transfer-Token": token}
    try:
        with TestClient(app) as client:
            assert client.post(f"{base}/upload").status_code == 422
            assert client.post(f"{base}/upload", headers=headers).status_code == 200
            for i, file in enumerate(files):
                for offset in range(0, len(file), 4):
                    data = file[offset : offset + 4]
                    response = client.put(
                        f"{base}/files/{i}?offset={offset}",
                        content=data,
                        headers={
                            **headers,
                            "Content-Type": "application/octet-stream",
                            "X-Chunk-SHA256": sha256(data).hexdigest(),
                        },
                    )
                    assert response.status_code == 200, response.text
                    assert response.headers["cache-control"] == "no-store"
            assert client.get(f"{base}/upload", headers=headers).json()["files"][0][
                "received_bytes"
            ] == len(files[0])
            done = client.post(f"{base}/upload/finish", headers=headers)
            assert done.status_code == 200, done.text
            assert done.json()["status"] == "ready"
            assert str(storage.root) not in done.text
            client.post(f"{base}/cancel", headers=headers)
            assert client.get(f"{base}/upload", headers=headers).status_code == 403
    finally:
        app.dependency_overrides.pop(store, None)
