import json
from hashlib import sha256
from uuid import uuid4

import pytest
from app.cad_delivery.compile_worker import compile_package
from app.cad_delivery.contracts import (
    PublicationRequest,
    TransferCreate,
    TransferDecision,
    TransferManifest,
)
from app.cad_delivery.publication import TransferPublication
from app.cad_delivery.routes import publication
from app.cad_delivery.store import TransferError, TransferStore
from app.cad_delivery.uploads import UploadStore
from app.composition import create_runtime
from app.main import app
from app.operations.contracts import OperationKind, OperationStatus
from fastapi.testclient import TestClient
from test_cad_bridge_compiler import probe_with_xref, valid_probe


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    runtime = create_runtime(tmp_path / "projects.sqlite")
    consent = TransferStore(tmp_path / "intake" / "transfers.sqlite")
    drawing = b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    probe = valid_probe()
    probe["source"]["sha256"] = sha256(drawing).hexdigest()
    data = {
        "Street.dxf": drawing,
        "Street.dxf.green-atlas.geometry.json": json.dumps(probe).encode(),
    }
    request = TransferCreate.model_validate(
        {
            "request_id": str(uuid4()),
            "client_secret": "s" * 43,
            "plugin_version": probe["plugin_version"],
            "manifest": {
                "entry": "Street.dxf",
                "files": [
                    {
                        "name": name,
                        "kind": "drawing" if name.endswith(".dxf") else "native_probe",
                        "sha256": sha256(value).hexdigest(),
                        "bytes": len(value),
                    }
                    for name, value in data.items()
                ],
            },
        }
    )
    receipt, code = consent.create(request)
    identifier, token = str(receipt.id), "s" * 43
    consent.decide(
        identifier,
        "alice",
        TransferDecision(
            decision="approve",
            confirmation_code=code,
            manifest_sha256=request.manifest.digest(),
        ),
    )
    uploads = UploadStore(consent)
    uploads.begin(identifier, token)
    for index, value in enumerate(data.values()):
        uploads.append(identifier, token, index, 0, value, sha256(value).hexdigest())
    uploads.finish(identifier, token)
    service = TransferPublication(uploads, runtime)
    yield service, runtime, identifier, token, request
    runtime.close()


PRODUCER = PublicationRequest(autocad_version="2027.0.1", target="macos-arm64")


def test_native_package_creates_one_project_with_real_intake(delivery):
    service, runtime, identifier, token, request = delivery
    started, claim = service.start(identifier, token, PRODUCER)
    assert started.status == "processing"
    assert service.start(identifier, token, PRODUCER)[1] is None
    service.run(identifier, token, claim)
    result = service.status(identifier, token)
    assert result.status == "needs_review", result
    projects = runtime.project_repository.list()
    assert len(projects) == 1
    assert str(result.project_id) == projects[0].id
    assert result.project_path == f"/projects/{projects[0].id}/import?source=cad"
    operation = runtime.cad_intake.lifecycle.latest(
        projects[0].id, OperationKind.INSPECT_CAD_PACKAGE
    )
    assert operation.status == OperationStatus.COMPLETED, operation
    assert operation.cad_intake.passport.drawings[0].status == "readable"
    assert service.start(identifier, token, PRODUCER) == (result, None)
    package_path = runtime.cad_intake.config.root(
        f"upload-{identifier.replace('-', '')}"
    ).path
    assert (
        package_path / request.manifest.entry
    ).read_bytes() == b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    service.uploads.consent.cancel(identifier, token)
    service.uploads.collect_expired()
    assert (
        package_path / request.manifest.entry
    ).is_file()  # Project source is not temporary upload.
    assert service.status(identifier, token) == result


def test_cancel_before_publication_never_creates_project(delivery):
    service, runtime, identifier, token, _ = delivery
    _, claim = service.start(identifier, token, PRODUCER)
    service.consent.cancel(identifier, token)
    service.run(identifier, token, claim)
    assert service.status(identifier, token).status == "failed"
    assert runtime.project_repository.list() == []


def test_retry_after_project_created_reuses_project_and_package(delivery, monkeypatch):
    service, runtime, identifier, token, _ = delivery
    original_start = runtime.cad_intake.start

    def fail(*args):
        raise OSError("simulated crash after project creation")

    monkeypatch.setattr(runtime.cad_intake, "start", fail)
    _, claim = service.start(identifier, token, PRODUCER)
    service.run(identifier, token, claim)
    assert service.status(identifier, token).status == "failed"
    first = runtime.project_repository.list()[0].id
    monkeypatch.setattr(runtime.cad_intake, "start", original_start)
    _, claim = service.start(identifier, token, PRODUCER)
    service.run(identifier, token, claim)
    assert str(service.status(identifier, token).project_id) == first
    assert len(runtime.project_repository.list()) == 1


def test_other_token_and_changed_producer_rejected(delivery):
    service, _, identifier, token, _ = delivery
    service.start(identifier, token, PRODUCER)
    with pytest.raises(TransferError):
        service.status(identifier, "x" * 43)
    with pytest.raises(TransferError, match="версию"):
        service.start(
            identifier,
            token,
            PublicationRequest(autocad_version="2027.0.1", target="windows-x86_64"),
        )


def test_compile_rejects_wrong_source_and_preserves_explicit_diagnostic(
    delivery, tmp_path
):
    service, _, identifier, _, request = delivery
    directory = tmp_path / "compiler"
    directory.mkdir()
    for i, file in enumerate(request.manifest.files):
        (directory / file.name).write_bytes(
            service.uploads._file(identifier, i).read_bytes()
        )
    probe_file = directory / "Street.dxf.green-atlas.geometry.json"
    probe = json.loads(probe_file.read_bytes())
    probe["source"]["sha256"] = "0" * 64
    probe_file.write_text(json.dumps(probe))
    package = compile_package(
        directory,
        request.manifest,
        PRODUCER,
        request.plugin_version,
        f"upload-{uuid4().hex}",
    )
    assert not package.snapshots
    failures = json.loads((directory / "bridge-report.json").read_text())["failures"]
    assert len(failures) == 1
    assert "match the uploaded drawing" in failures[0]["detail"]


def test_native_dependency_rebased_by_exact_bytes_not_client_path(tmp_path):
    probe = probe_with_xref(tmp_path)
    probe["source"]["sha256"] = sha256(b"main drawing").hexdigest()
    child = (tmp_path / "references" / "child.dxf").read_bytes()
    probe["xref_dependencies"][0]["resolved_path"] = "Z:\\nonexistent\\child.dxf"
    secondary = valid_probe()
    secondary["plugin_version"] = "0.1.6"
    secondary["source"]["sha256"] = sha256(child).hexdigest()
    directory = tmp_path / "server"
    directory.mkdir()
    files = {
        "Main.dxf": b"main drawing",
        "Renamed child.dxf": child,
        "Main.dxf.green-atlas.geometry.json": json.dumps(probe).encode(),
        "Renamed child.dxf.green-atlas.geometry.json": json.dumps(secondary).encode(),
    }
    for name, data in files.items():
        (directory / name).write_bytes(data)
    manifest = TransferManifest.model_validate(
        {
            "entry": "Main.dxf",
            "files": [
                {
                    "name": name,
                    "kind": "drawing" if name.endswith(".dxf") else "native_probe",
                    "bytes": len(data),
                    "sha256": sha256(data).hexdigest(),
                }
                for name, data in files.items()
            ],
        }
    )
    result = compile_package(
        directory, manifest, PRODUCER, "0.1.6", f"upload-{uuid4().hex}"
    )
    assert len(result.snapshots) == 2
    main = json.loads((directory / "Main.dxf.green-atlas.snapshot.json").read_bytes())
    assert main["dependencies"][0]["path"] == "Renamed child.dxf"
    assert main["dependencies"][0]["sha256"] == sha256(child).hexdigest()
    assert json.loads((directory / "bridge-report.json").read_text())["failures"] == []


def test_expired_claim_can_be_retried_but_old_worker_cannot_publish(
    delivery, monkeypatch
):
    service, runtime, identifier, token, _ = delivery
    _, old_claim = service.start(identifier, token, PRODUCER)
    later = service.consent.now() + 601
    monkeypatch.setattr(service.consent, "now", lambda: later)
    assert service.status(identifier, token).status == "failed"
    _, new_claim = service.start(identifier, token, PRODUCER)
    assert new_claim != old_claim
    service.run(identifier, token, old_claim)
    assert service.status(identifier, token).status == "processing"
    assert not runtime.project_repository.list()
    service.run(identifier, token, new_claim)
    assert service.status(identifier, token).status == "needs_review"
    assert len(runtime.project_repository.list()) == 1


def test_retry_recovers_commit_before_inspection_dispatch(delivery, monkeypatch):
    service, runtime, identifier, token, _ = delivery
    run = runtime.cad_intake.run
    monkeypatch.setattr(runtime.cad_intake, "run", lambda _: None)
    _, claim = service.start(identifier, token, PRODUCER)
    service.run(identifier, token, claim)
    receipt = service.status(identifier, token)
    assert receipt.status == "needs_review"
    operation = runtime.cad_intake.lifecycle.get(
        str(receipt.project_id), receipt.operation_id
    )
    assert operation.status == OperationStatus.QUEUED
    monkeypatch.setattr(runtime.cad_intake, "run", run)
    service.resume_review(identifier, token)
    assert (
        runtime.cad_intake.lifecycle.get(
            str(receipt.project_id), receipt.operation_id
        ).status
        == OperationStatus.COMPLETED
    )


def test_deleted_project_not_recreated_by_delivery_retry(delivery):
    service, runtime, identifier, token, _ = delivery
    _, claim = service.start(identifier, token, PRODUCER)
    service.run(identifier, token, claim)
    receipt = service.status(identifier, token)
    runtime.application.delete_project(str(receipt.project_id))
    with pytest.raises(TransferError) as error:
        service.start(identifier, token, PRODUCER)
    assert error.value.status == 410
    assert runtime.project_repository.list() == []


def test_http_publication_returns_scoped_project_receipt(delivery):
    service, runtime, identifier, token, _ = delivery
    app.dependency_overrides[publication] = lambda: service
    path = f"/api/cad-bridge/device/transfers/{identifier}/publication"
    headers = {"X-Green-Atlas-Transfer-Token": token}
    try:
        with TestClient(app) as client:
            started = client.post(path, headers=headers, json=PRODUCER.model_dump())
            assert started.status_code == 202, started.text
            assert started.json()["status"] == "processing"
            receipt = client.get(path, headers=headers)
            assert receipt.status_code == 200
            assert receipt.json()["status"] == "needs_review"
            assert receipt.json()["project_path"].startswith("/projects/")
            assert receipt.headers["cache-control"] == "no-store"
            assert (
                client.post(path, headers=headers, json=PRODUCER.model_dump()).json()
                == receipt.json()
            )
            assert len(runtime.project_repository.list()) == 1
            assert (
                client.get(
                    path, headers={"X-Green-Atlas-Transfer-Token": "x" * 43}
                ).status_code
                == 404
            )
    finally:
        app.dependency_overrides.pop(publication, None)
