import json
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from threading import Event

import pytest
from test_cad_bridge_compiler import valid_probe

from app.composition import create_runtime
from app.desktop.handoff import LocalHandoff
from app.operations.contracts import OperationKind, OperationStatus


@pytest.fixture
def local(tmp_path, monkeypatch):
    tmp_path = tmp_path.resolve()
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    runtime = create_runtime(tmp_path / "projects.sqlite3")
    root = tmp_path / "transfers"
    package = root / "test-ticket"
    package.mkdir(parents=True)
    drawing = b"0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n"
    probe = valid_probe()
    probe["source"]["sha256"] = sha256(drawing).hexdigest()
    files = {
        "Street.dxf": drawing,
        "Street.dxf.green-atlas.geometry.json": json.dumps(probe).encode(),
    }
    for name, data in files.items():
        (package / name).write_bytes(data)
    ticket = package / "transfer.gatransfer"
    ticket.write_text(
        json.dumps(
            {
                "schema": "green-atlas.transfer/1",
                "plugin_version": probe["plugin_version"],
                "producer": {"autocad_version": "2027.0.1", "target": "macos-arm64"},
                "manifest": {
                    "entry": "Street.dxf",
                    "files": [
                        {
                            "name": name,
                            "kind": "drawing"
                            if name.endswith(".dxf")
                            else "native_probe",
                            "sha256": sha256(data).hexdigest(),
                            "bytes": len(data),
                        }
                        for name, data in files.items()
                    ],
                },
            }
        )
    )
    service = LocalHandoff(runtime, root)
    yield service, runtime, ticket
    service.close()
    runtime.close()


def finish(service, identifier):
    service.futures[identifier].result(timeout=30)
    return service.get(identifier)


def test_local_ticket_creates_real_project_and_intake_without_pairing(local):
    service, runtime, ticket = local
    before = {path.name: path.read_bytes() for path in ticket.parent.iterdir()}
    receipt = service.submit(ticket)
    result = finish(service, receipt["id"])
    assert result["status"] == "needs_review", result
    projects = runtime.application.list_projects()
    assert len(projects) == 1
    operation = runtime.cad_intake.lifecycle.latest(
        projects[0].id, OperationKind.INSPECT_CAD_PACKAGE
    )
    assert operation.status == OperationStatus.COMPLETED
    assert {path.name: path.read_bytes() for path in ticket.parent.iterdir()} == before
    assert not (service.storage / "transfers.sqlite3").exists()
    assert result["project_path"] == f"/projects/{projects[0].id}/import?source=cad"


def test_repeat_concurrent_ticket_never_duplicates_project(local):
    service, runtime, ticket = local
    with ThreadPoolExecutor(max_workers=4) as workers:
        receipts = list(workers.map(lambda _: service.submit(ticket), range(4)))
    assert len({item["id"] for item in receipts}) == 1
    result = finish(service, receipts[0]["id"])
    assert service.submit(ticket) == result
    assert len(runtime.application.list_projects()) == 1


def test_repeat_ticket_recovers_intake_interrupted_after_publication(local):
    service, runtime, ticket = local
    receipt = service.submit(ticket)
    finish(service, receipt["id"])
    project = runtime.application.list_projects()[0]
    first = runtime.cad_intake.lifecycle.latest(
        project.id, OperationKind.INSPECT_CAD_PACKAGE
    )
    interrupted = first.model_copy(deep=True)
    interrupted.status = OperationStatus.INTERRUPTED
    runtime.cad_intake.lifecycle.operations.save(interrupted)

    retried = service.submit(ticket)
    assert finish(service, retried["id"])["status"] == "needs_review"
    latest = runtime.cad_intake.lifecycle.latest(
        project.id, OperationKind.INSPECT_CAD_PACKAGE
    )
    assert latest.status == OperationStatus.COMPLETED
    assert latest.id != first.id
    assert latest.retry_of_operation_id == first.id
    assert [item.id for item in runtime.application.list_projects()] == [project.id]


def test_mutated_file_and_symlink_never_become_projects(local):
    service, runtime, ticket = local
    drawing = ticket.parent / "Street.dxf"
    drawing.write_bytes(b"changed")
    result = finish(service, service.submit(ticket)["id"])
    assert result["status"] == "failed"
    assert not runtime.application.list_projects()
    link = ticket.parent / "alias"
    link.symlink_to(ticket.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        service.submit(link / ticket.name)


def test_cancel_queued_ticket_does_not_create_project(local):
    service, runtime, ticket = local
    gate = Event()
    service.pool.submit(lambda: gate.wait(5))
    receipt = service.submit(ticket)
    assert service.cancel(receipt["id"])["status"] == "cancelled"
    gate.set()
    assert finish(service, receipt["id"])["status"] == "cancelled"
    assert not runtime.application.list_projects()


def test_crash_gap_after_project_creation_reuses_reserved_id(local, monkeypatch):
    service, runtime, ticket = local
    start = runtime.cad_intake.start
    monkeypatch.setattr(
        runtime.cad_intake,
        "start",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("test interruption")),
    )
    receipt = service.submit(ticket)
    assert finish(service, receipt["id"])["status"] == "failed"
    first_id = runtime.application.list_projects()[0].id
    monkeypatch.setattr(runtime.cad_intake, "start", start)
    service.submit(ticket)
    assert finish(service, receipt["id"])["status"] == "needs_review"
    assert [p.id for p in runtime.application.list_projects()] == [first_id]


def test_deleted_project_is_not_recreated_by_repeat(local):
    service, runtime, ticket = local
    finish(service, service.submit(ticket)["id"])
    runtime.application.delete_project(runtime.application.list_projects()[0].id)
    with pytest.raises(KeyError):
        service.submit(ticket)
    assert not runtime.application.list_projects()


def test_changed_file_after_publication_does_not_recover_stale_receipt(local):
    service, runtime, ticket = local
    finish(service, service.submit(ticket)["id"])
    (ticket.parent / "Street.dxf").write_bytes(b"changed")
    with pytest.raises(ValueError, match="изменился"):
        service.submit(ticket)
    assert len(runtime.application.list_projects()) == 1


def test_browser_cookie_cannot_submit_local_paths(local, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.desktop.web import SESSION_COOKIE, SESSION_HEADER, create_desktop_web

    service, _, ticket = local
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>fixture</title>")
    origin, secret = "http://127.0.0.1:18421", "a" * 43
    app = create_desktop_web(
        FastAPI(), web, origin=origin, secret=secret, handoff=service
    )
    with TestClient(app, base_url=origin, client=("127.0.0.1", 50001)) as client:
        client.cookies.set(SESSION_COOKIE, secret)
        assert (
            client.post(
                "/_desktop/handoffs",
                json={"ticket": str(ticket)},
                headers={"Origin": origin},
            ).status_code
            == 403
        )
        response = client.post(
            "/_desktop/handoffs",
            json={"ticket": str(ticket)},
            headers={SESSION_HEADER: secret},
        )
        assert response.status_code == 202
        assert finish(service, response.json()["id"])["status"] == "needs_review"
        assert (
            client.post(
                "/_desktop/handoffs",
                content=b"a" * 9000,
                headers={SESSION_HEADER: secret},
            ).status_code
            == 400
        )
