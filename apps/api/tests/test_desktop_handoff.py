import json
import re
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
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


@pytest.mark.parametrize("plugin_version", ["0.1.40", "0.1.43", "0.1.44"])
def test_live_query_ticket_binds_same_capture_to_project(local, monkeypatch, plugin_version):
    from app.native_query.live_client import LiveQueryClient
    from test_native_live_client import session
    service, runtime, _ = local
    path, _ = live_ticket(service)
    payload = json.loads(path.read_text())
    capture_path = path.parent / "Drawing.autocad.json"
    probe = json.loads(capture_path.read_text())
    probe["plugin_version"] = plugin_version
    capture_path.write_text(json.dumps(probe))
    digest = sha256(capture_path.read_bytes()).hexdigest()
    current = session(path.parent, plugin_version=plugin_version, snapshot_sha256=digest,
        inventory_path="/test/inventory.json", inventory_sha256="d" * 64)
    payload.update(schema="green-atlas.transfer/4", plugin_version=plugin_version, live_session=current.model_dump())
    payload["manifest"]["files"][0].update(sha256=digest, bytes=capture_path.stat().st_size)
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(LiveQueryClient, "reconnect", lambda self, token: current)
    monkeypatch.setattr(LiveQueryClient, "inspect", lambda self, session: None)
    monkeypatch.setattr("app.desktop.handoff.load_inventory", lambda session: None)
    result = service.submit(path)
    receipt = finish(service, result["id"])
    assert receipt["status"] == "needs_review", receipt
    project = runtime.project_repository.get(runtime.application.list_projects()[0].id)
    assert project.source_file.native_session == current
    assert project.geometry.feature_collection["features"]
    assert runtime.project_repository.get_source(project.id) == capture_path.read_bytes()


def test_packaged_plugin_version_is_admitted_by_desktop_and_compiler():
    from app.cad_bridge.compiler import (AREA_PROPOSAL_PLUGIN_VERSIONS,
                                         SUPPORTED_PLUGIN_VERSIONS,
                                         XREF_DEPENDENCY_PLUGIN_VERSIONS)
    from app.desktop.tickets import LiveQueryTicket

    root = Path(__file__).resolve().parents[3]
    header = (root / "tools/autocad-bridge/native/bridge_config.h").read_text()
    version = re.search(r'kPluginVersion\s*=\s*"([^"]+)"', header).group(1)
    assert version in LiveQueryTicket.model_fields["plugin_version"].annotation.__args__
    assert version in SUPPORTED_PLUGIN_VERSIONS
    assert version in XREF_DEPENDENCY_PLUGIN_VERSIONS
    assert version in AREA_PROPOSAL_PLUGIN_VERSIONS
    for path in ("tools/autocad-bridge/native/Info.plist",
                 "tools/autocad-bridge/desktop/Info.plist",
                 "tools/autocad-bridge/PackageContents.xml"):
        assert version in (root / path).read_text()


def test_live_query_ticket_rejects_different_capture(local):
    from app.desktop.tickets import load_ticket
    from test_native_live_client import session
    service, _, _ = local
    path, _ = live_ticket(service)
    payload = json.loads(path.read_text())
    current = session(path.parent, plugin_version="0.1.40", inventory_path="/test/inventory.json", inventory_sha256="d" * 64)
    payload.update(schema="green-atlas.transfer/4", plugin_version="0.1.40", live_session=current.model_dump())
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_ticket(path, service.ticket_root)


def live_ticket(service, *, changed=False):
    package = service.ticket_root / "live-ticket"
    package.mkdir()
    original = package / "Street.dwg"
    original.write_bytes(b"saved DWG context; native capture is authoritative")
    probe = valid_probe()
    probe["plugin_version"] = "0.1.37"
    probe["capture_mode"] = "live_document"
    probe["summary"].update({
        "paths": 0, "points": 0, "area_proposals": 0,
        "area_proposal_candidates": 0, "area_proposal_rejected": 0,
    })
    probe["source"].update({
        "path": str(original),
        "sha256": sha256(original.read_bytes()).hexdigest(),
        "database_modified_flags": 32 if changed else 0,
        "live_database_matches_disk": not changed,
    })
    capture = json.dumps(probe).encode()
    (package / "Drawing.autocad.json").write_bytes(capture)
    ticket = package / "transfer.gatransfer"
    ticket.write_text(json.dumps({
        "schema": "green-atlas.transfer/2",
        "plugin_version": probe["plugin_version"],
        "producer": {"autocad_version": "2027.0.1", "target": "macos-arm64"},
        "source_name": "Street.dwg",
        "manifest": {
            "entry": "Drawing.autocad.json",
            "files": [{
                "name": "Drawing.autocad.json", "kind": "live_capture",
                "sha256": sha256(capture).hexdigest(), "bytes": len(capture),
            }],
        },
    }))
    return ticket, capture


@pytest.mark.parametrize("changed", [False, True])
def test_live_ticket_opens_native_project_without_dxf_round_trip(local, changed):
    service, runtime, _ = local
    ticket, capture = live_ticket(service, changed=changed)
    before = {path.name: path.read_bytes() for path in ticket.parent.iterdir()}
    receipt = service.submit(ticket)
    result = finish(service, receipt["id"])
    assert result["status"] == "needs_review", result
    assert result["project_path"].endswith("/setup")
    project = runtime.application.get(runtime.application.list_projects()[0].id)
    assert project.source_file.content_sha256 == sha256(capture).hexdigest()
    assert project.source_file.name == "Street.dwg"
    assert project.geometry.feature_collection["features"]
    assert runtime.application.repository.get_source(project.id) == capture
    assert not list((service.storage / "uploads").rglob("*.dxf"))
    assert {path.name: path.read_bytes() for path in ticket.parent.iterdir()} == before
    assert service.submit(ticket) == result
    assert len(runtime.application.list_projects()) == 1


def test_live_ticket_rejects_mutated_capture_before_project_creation(local):
    service, runtime, _ = local
    ticket, _ = live_ticket(service)
    (ticket.parent / "Drawing.autocad.json").write_bytes(b"changed")
    result = finish(service, service.submit(ticket)["id"])
    assert result["status"] == "failed"
    assert runtime.application.list_projects() == []


def test_live_import_failure_preserves_evidence_and_retries_same_project(local, monkeypatch, caplog):
    service, runtime, _ = local
    ticket, capture = live_ticket(service)
    importer = runtime.application.import_autocad_live_file

    def fail_import(*args, **kwargs):
        raise RuntimeError("live worker failure control")

    monkeypatch.setattr(runtime.application, "import_autocad_live_file", fail_import)
    receipt = service.submit(ticket)
    failed = finish(service, receipt["id"])
    assert failed["status"] == "failed"
    assert failed["project_path"] is None
    assert "live worker failure control" not in failed["message"]
    evidence = next(record for record in caplog.records if record.name == "app.desktop.handoff")
    assert evidence.exc_info[0] is RuntimeError
    assert receipt["id"] in evidence.getMessage()
    project_id = runtime.application.list_projects()[0].id
    assert runtime.application.get(project_id).source_file is None

    monkeypatch.setattr(runtime.application, "import_autocad_live_file", importer)
    service.submit(ticket)
    recovered = finish(service, receipt["id"])
    assert recovered["status"] == "needs_review"
    assert recovered["project_path"] == f"/projects/{project_id}/setup"
    assert [project.id for project in runtime.application.list_projects()] == [project_id]
    assert runtime.application.get(project_id).source_file.content_sha256 == sha256(capture).hexdigest()


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
