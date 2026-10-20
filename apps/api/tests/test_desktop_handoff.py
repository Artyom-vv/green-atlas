import asyncio
import json
import plistlib
import re
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from hashlib import sha256
from pathlib import Path
from threading import Event
from typing import Literal, get_args
from uuid import uuid4
from xml.etree import ElementTree

import pytest
from starlette.types import Message, Scope
from test_cad_bridge_compiler import valid_probe

from app.cad_import.contracts import CadConversionMemoryExceeded
from app.composition import Runtime, create_runtime
from app.desktop.handoff import HandoffReceipt, LocalHandoff
from app.desktop.handoff_store import HandoffRecord, HandoffStore
from app.dxf_import.native import live_process
from app.native_query.capture.capture_contracts import SessionTransfer
from app.native_query.live.live_client import LiveSession
from app.projects.contracts import Project
from app.shared.failures import FailureReason
from app.shared.messages import message

LocalFixture = tuple[LocalHandoff, Runtime, Path]


@pytest.fixture
def local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LocalFixture]:
    tmp_path = tmp_path.resolve()
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    runtime = create_runtime(tmp_path / "projects.sqlite3")
    ticket_root = tmp_path / "transfers"
    ticket_root.mkdir()
    service = LocalHandoff(runtime, ticket_root)
    ticket, _ = live_ticket(service)
    yield service, runtime, ticket
    service.close()
    runtime.close()


def finish(service: LocalHandoff, identifier: str) -> HandoffReceipt:
    service.futures[identifier].result(timeout=30)
    return service.get(identifier)


def test_archived_handoff_does_not_read_published_cad_graph_for_session_binding(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_archived_handoff import archived_ticket

    service, runtime, _ = local
    path = archived_ticket(service)
    repository = runtime.project_repository
    original_get = repository.get
    original_bind = runtime.application.bind_native_session
    post_import_reads: list[bool] = []
    bound_projects: list[Project] = []

    def metadata_only(project_id: str, *, lightweight: bool = False) -> Project:
        compact = original_get(project_id, lightweight=True)
        if compact.source_file is not None:
            post_import_reads.append(lightweight)
            assert lightweight, "После публикации привязка не читает полный CAD-граф"
        return original_get(project_id, lightweight=lightweight)

    def bind_through_application(project: Project, session: LiveSession) -> Project:
        bound_projects.append(project)
        return original_bind(project, session)

    monkeypatch.setattr(repository, "get", metadata_only)
    monkeypatch.setattr(
        runtime.application, "bind_native_session", bind_through_application
    )
    transfer = service.submit(path)
    receipt = finish(service, transfer["id"])
    assert receipt["status"] == "needs_review", receipt
    project = original_get(service.journal.get(transfer["id"]).project_id)
    assert project.source_file is not None
    session = project.source_file.native_session
    assert session is not None
    assert service.native_captures.retained_session(project.id, session) == session
    assert (
        project.geometry is not None and project.geometry.feature_collection["features"]
    )
    assert (
        repository.get_source(project.id)
        == (path.parent / "Drawing.autocad.json").read_bytes()
    )
    assert post_import_reads and all(post_import_reads)
    assert len(bound_projects) == 1
    assert bound_projects[0].id == project.id
    assert bound_projects[0].geometry is not None
    assert bound_projects[0].geometry.feature_collection["features"] == []


def test_close_interrupts_native_ticket_without_implicit_restart(
    local: LocalFixture,
) -> None:
    service, runtime, _ = local
    ticket, _ = live_ticket(service)
    gate = Event()
    service.pool.submit(lambda: gate.wait(10))
    receipt = service.submit(ticket)
    with ThreadPoolExecutor(max_workers=1) as closer:
        closing = closer.submit(service.close)
        deadline = time.monotonic() + 5
        while service.get(receipt["id"])["status"] != "interrupted":
            assert time.monotonic() < deadline
            time.sleep(0.01)
        gate.set()
        closing.result(timeout=10)
    resumed = LocalHandoff(runtime, service.ticket_root)
    try:
        assert resumed.list() == [resumed.get(receipt["id"])]
        assert resumed.submit(ticket)["status"] == "interrupted"
        assert not resumed.futures
        assert not runtime.application.list_projects()
        resumed.resume(receipt["id"])
        assert finish(resumed, receipt["id"])["status"] == "needs_review"
        assert len(runtime.application.list_projects()) == 1
    finally:
        resumed.close()


def test_explicit_cancel_requires_explicit_native_resume(local: LocalFixture) -> None:
    service, runtime, _ = local
    ticket, _ = live_ticket(service)
    gate = Event()
    service.pool.submit(lambda: gate.wait(5))
    receipt = service.submit(ticket)
    service.cancel(receipt["id"])
    gate.set()
    assert finish(service, receipt["id"])["status"] == "cancelled"
    previous = service.futures[receipt["id"]]
    assert service.submit(ticket)["status"] == "cancelled"
    assert service.futures[receipt["id"]] is previous
    assert not runtime.application.list_projects()
    service.resume(receipt["id"])
    assert finish(service, receipt["id"])["status"] == "needs_review"
    assert len(runtime.application.list_projects()) == 1


def test_native_publication_gap_resumes_same_project_without_reimport(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, runtime, _ = local
    ticket, capture = live_ticket(service)
    published = service.journal.publish
    imported = runtime.application.import_autocad_live_file
    calls: list[str] = []

    def import_once(
        project_id: str,
        filename: str,
        capture_path: Path,
        capture_sha256: str,
        *,
        autocad_version: str,
        target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
        check_cancelled: Callable[[], None] | None = None,
        native_capture_root: Path | None = None,
    ) -> Project:
        calls.append(project_id)
        return imported(
            project_id,
            filename,
            capture_path,
            capture_sha256,
            autocad_version=autocad_version,
            target=target,
            check_cancelled=check_cancelled,
            native_capture_root=native_capture_root,
        )

    def interrupted_receipt(
        identifier: str, text: str, operation_id: str | None = None
    ) -> None:
        service.journal.interrupt_active("test publication gap")
        raise InterruptedError("test publication gap")

    monkeypatch.setattr(runtime.application, "import_autocad_live_file", import_once)
    monkeypatch.setattr(service.journal, "publish", interrupted_receipt)
    receipt = service.submit(ticket)
    assert finish(service, receipt["id"])["status"] == "interrupted"
    first = runtime.application.list_projects()[0]
    assert runtime.project_repository.get_source(first.id) == capture
    assert service.submit(ticket)["status"] == "interrupted"
    monkeypatch.setattr(service.journal, "publish", published)
    service.resume(receipt["id"])
    assert (
        finish(service, receipt["id"])["project_path"] == f"/projects/{first.id}/setup"
    )
    assert calls == [first.id]
    assert [item.id for item in runtime.application.list_projects()] == [first.id]


def test_native_resume_checks_source_hash_when_retained_session_matches(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from test_archived_handoff import archived_ticket

    service, runtime, _ = local
    ticket = archived_ticket(service)
    repository = runtime.project_repository
    published = service.journal.publish
    ensure_project = runtime.application.ensure_import_project

    def interrupted_receipt(
        identifier: str, text: str, operation_id: str | None = None
    ) -> None:
        service.journal.interrupt_active("test publication gap")
        raise InterruptedError("test publication gap")

    monkeypatch.setattr(service.journal, "publish", interrupted_receipt)
    transfer = service.submit(ticket)
    assert finish(service, transfer["id"])["status"] == "interrupted"
    project_id = service.journal.get(transfer["id"]).project_id
    original = repository.get(project_id, lightweight=True)
    assert original.source_file is not None
    assert original.source_file.native_session is not None
    source_bytes = repository.get_source(project_id)

    def change_source_after_lookup(project_id: str, name: str) -> Project:
        observed = ensure_project(project_id, name)
        winner = repository.get(project_id)
        assert winner.source_file is not None
        winner.source_file = winner.source_file.model_copy(
            update={"content_sha256": "e" * 64}
        )
        repository.save(winner)
        return observed

    monkeypatch.setattr(
        runtime.application, "ensure_import_project", change_source_after_lookup
    )
    monkeypatch.setattr(service.journal, "publish", published)
    service.resume(transfer["id"])
    receipt = finish(service, transfer["id"])
    assert receipt["status"] == "failed", receipt
    assert receipt["project_path"] is None
    assert receipt["message"] == message(FailureReason.HANDOFF_SOURCE_CHANGED.value)
    stored = repository.get(project_id, lightweight=True)
    assert stored.source_file is not None
    assert stored.source_file.content_sha256 == "e" * 64
    assert stored.source_file.native_session == original.source_file.native_session
    assert stored.state_version == original.state_version + 1
    assert stored.geometry_version == original.geometry_version
    assert repository.get_source(project_id) == source_bytes


def test_native_resume_revalidates_changed_capture(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, runtime, _ = local
    ticket, _ = live_ticket(service)
    gate = Event()
    service.pool.submit(lambda: gate.wait(5))
    receipt = service.submit(ticket)
    service.journal.interrupt_active("test interruption")
    gate.set()
    assert finish(service, receipt["id"])["status"] == "interrupted"
    (ticket.parent / "Drawing.autocad.json").write_bytes(b"changed")
    service.resume(receipt["id"])
    assert finish(service, receipt["id"])["status"] == "failed"
    assert not runtime.application.list_projects()


def test_native_retention_gap_preserves_package_and_reserved_project(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, runtime, _ = local
    ticket, _ = live_ticket(service)
    payload = json.loads(ticket.read_text())
    capture_path = ticket.parent / "Drawing.autocad.json"
    probe = json.loads(capture_path.read_text())
    probe["plugin_version"] = "0.1.39"
    capture = json.dumps(probe).encode()
    capture_path.write_bytes(capture)
    capture_sha = sha256(capture).hexdigest()
    native = ticket.parent / "Native"
    native.mkdir()
    host, instances = b"native captured CAD bytes", b"[]"
    (native / "host.dwg").write_bytes(host)
    (native / "instances.json").write_bytes(instances)
    (native / "session.json").write_text(
        json.dumps(
            {
                "schema": "green-atlas.native-session/1",
                "plugin_version": "0.1.39",
                "entry": "host.dwg",
                "units_code": 6,
                "source_path": str(ticket.parent / "Street.dwg"),
                "display_sha256": capture_sha,
                "source_dbmod_before": 0,
                "source_dbmod_after": 0,
                "source_disk_hashes_verified": True,
                "instance_count": 0,
                "instances_sha256": sha256(instances).hexdigest(),
                "files": [
                    {
                        "path": "host.dwg",
                        "sha256": sha256(host).hexdigest(),
                        "bytes": len(host),
                    }
                ],
                "references": [],
            }
        )
    )
    payload.update(schema="green-atlas.transfer/3", plugin_version="0.1.39")
    payload["manifest"]["files"][0].update(sha256=capture_sha, bytes=len(capture))
    payload["native_session"] = {
        "entry": "Native/session.json",
        "files": [
            {
                "name": "Native/" + path.name,
                "bytes": path.stat().st_size,
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(native.iterdir())
        ],
    }
    ticket.write_text(json.dumps(payload))
    retain = service.native_captures.retain

    def interrupt_after_retention(
        project_id: str,
        display_sha256: str,
        plugin_version: str,
        transfer_root: Path,
        private_root: Path,
        manifest: SessionTransfer,
        check: Callable[[], None],
    ) -> Path:
        result = retain(
            project_id,
            display_sha256,
            plugin_version,
            transfer_root,
            private_root,
            manifest,
            check,
        )
        service.journal.interrupt_active("test gap after native retention")
        return result

    monkeypatch.setattr(service.native_captures, "retain", interrupt_after_retention)
    receipt = service.submit(ticket)
    assert finish(service, receipt["id"])["status"] == "interrupted"
    reserved = service.journal.get(receipt["id"]).project_id
    retained = service.native_captures.directory(reserved, capture_sha)
    assert (retained / "host.dwg").read_bytes() == host
    assert not runtime.application.list_projects()
    # This legacy producer is tested at the retention boundary. Its display
    # import compatibility is separate from the durable package journal.
    service.resume(receipt["id"])
    assert finish(service, receipt["id"])["status"] == "interrupted"
    assert service.journal.get(receipt["id"]).project_id == reserved
    assert not runtime.application.list_projects()
    assert (retained / "host.dwg").read_bytes() == host


@pytest.mark.parametrize(
    "plugin_version", ["0.1.40", "0.1.42", "0.1.43", "0.1.44", "0.1.45"]
)
@pytest.mark.skipif(sys.platform == "win32", reason="legacy POSIX live query socket")
def test_live_query_ticket_binds_same_capture_to_project(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch, plugin_version: str
) -> None:
    from test_native_live_client import session

    from app.native_query.live.live_client import LiveQueryClient

    service, runtime, _ = local
    path, _ = live_ticket(service)
    payload = json.loads(path.read_text())
    capture_path = path.parent / "Drawing.autocad.json"
    probe = json.loads(capture_path.read_text())
    probe["plugin_version"] = plugin_version
    capture_path.write_text(json.dumps(probe))
    digest = sha256(capture_path.read_bytes()).hexdigest()
    current = session(
        path.parent,
        plugin_version=plugin_version,
        snapshot_sha256=digest,
        inventory_path="/test/inventory.json",
        inventory_sha256="d" * 64,
    )
    payload.update(
        schema="green-atlas.transfer/4",
        plugin_version=plugin_version,
        live_session=current.model_dump(),
    )
    payload["manifest"]["files"][0].update(
        sha256=digest, bytes=capture_path.stat().st_size
    )
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(LiveQueryClient, "reconnect", lambda self, token: current)
    monkeypatch.setattr(LiveQueryClient, "inspect", lambda self, session: None)
    monkeypatch.setattr("app.desktop.handoff.load_inventory", lambda session: None)
    result = service.submit(path)
    receipt = finish(service, result["id"])
    assert receipt["status"] == "needs_review", receipt
    assert receipt["autocad_required"] is True
    project = runtime.project_repository.get(runtime.application.list_projects()[0].id)
    assert project.source_file is not None
    assert project.source_file.native_session == current
    assert project.geometry is not None
    assert project.geometry.feature_collection["features"]
    assert (
        runtime.project_repository.get_source(project.id) == capture_path.read_bytes()
    )


def test_packaged_plugin_version_is_admitted_by_desktop_and_compiler() -> None:
    from app.cad_bridge.config import PRODUCER_CAPABILITIES, ProbeCapability
    from app.desktop.tickets import ArchivedTicket

    root = Path(__file__).resolve().parents[3]
    header = (root / "tools/autocad-bridge/native/bridge_config.h").read_text()
    match = re.search(r'kPluginVersion\s*=\s*"([^"]+)"', header)
    assert match is not None
    version = match.group(1)
    assert version in get_args(ArchivedTicket.model_fields["plugin_version"].annotation)
    assert version in PRODUCER_CAPABILITIES
    assert ProbeCapability.XREF_DEPENDENCIES in PRODUCER_CAPABILITIES[version]
    assert ProbeCapability.AREA_PROPOSALS in PRODUCER_CAPABILITIES[version]
    bridge = root / "tools/autocad-bridge"
    native_info = plistlib.loads((bridge / "native/Info.plist").read_bytes())
    assert native_info["CFBundleShortVersionString"] == version
    desktop_info = plistlib.loads((bridge / "desktop/Info.plist").read_bytes())
    desktop_version = desktop_info["CFBundleShortVersionString"]
    windows_version = bridge / "desktop/windows-version.h"
    package_path = bridge / (
        "windows/PackageContents.xml"
        if windows_version.exists()
        else "PackageContents.xml"
    )
    assert (
        ElementTree.parse(package_path).getroot().get("AppVersion") == desktop_version
    )
    if windows_version.exists():
        resource_version = re.search(
            r'GREEN_ATLAS_DESKTOP_VERSION_STRING\s+"([^"]+)"',
            windows_version.read_text(),
        )
        assert resource_version and resource_version.group(1) == desktop_version
    else:
        for relative in ("desktop/Launcher-Info.plist", "installer/macos/Info.plist"):
            assert (
                plistlib.loads((bridge / relative).read_bytes())[
                    "CFBundleShortVersionString"
                ]
                == desktop_version
            )


@pytest.mark.parametrize(
    "failed_contract",
    [None, "ticket", "producer", "xref", "area_proposals", "package_version"],
)
def test_macos_package_script_executes_current_api_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_contract: str | None
) -> None:
    from app.cad_bridge.config import PRODUCER_CAPABILITIES, ProbeCapability
    from app.desktop.tickets import ArchivedTicket

    bridge = Path(__file__).resolve().parents[3] / "tools/autocad-bridge"
    script_path = bridge / "package-macos.sh"
    python_block = re.search(r"<<'PY'\n(.*?)\nPY\n", script_path.read_text(), re.DOTALL)
    assert python_block is not None
    version = plistlib.loads((bridge / "native/Info.plist").read_bytes())[
        "CFBundleShortVersionString"
    ]
    package_version = plistlib.loads((bridge / "desktop/Info.plist").read_bytes())[
        "CFBundleShortVersionString"
    ]
    required = {ProbeCapability.XREF_DEPENDENCIES, ProbeCapability.AREA_PROPOSALS}
    if failed_contract == "ticket":
        accepted = get_args(ArchivedTicket.model_fields["plugin_version"].annotation)
        version = next(
            candidate
            for candidate, capabilities in PRODUCER_CAPABILITIES.items()
            if candidate not in accepted and required.issubset(capabilities)
        )
    elif failed_contract == "producer":
        monkeypatch.delitem(PRODUCER_CAPABILITIES, version)
    elif failed_contract in {"xref", "area_proposals"}:
        capability = (
            ProbeCapability.XREF_DEPENDENCIES
            if failed_contract == "xref"
            else ProbeCapability.AREA_PROPOSALS
        )
        monkeypatch.setitem(
            PRODUCER_CAPABILITIES,
            version,
            PRODUCER_CAPABILITIES[version] - {capability},
        )
    xml_path = tmp_path / "PackageContents.xml"
    xml_version = (
        "different" if failed_contract == "package_version" else package_version
    )
    ElementTree.ElementTree(
        ElementTree.Element("ApplicationPackage", AppVersion=xml_version)
    ).write(xml_path)
    monkeypatch.setattr(sys, "argv", ["-", version, package_version, str(xml_path)])
    code = compile(python_block.group(1), str(script_path), "exec")
    if failed_contract is None:
        exec(code, {})
    else:
        message = (
            "PackageContents.xml must match the desktop package version"
            if failed_contract == "package_version"
            else f"Desktop API cannot accept the packaged plugin {version}"
        )
        with pytest.raises(SystemExit) as failure:
            exec(code, {})
        assert str(failure.value) == message


def test_live_query_ticket_rejects_different_capture(local: LocalFixture) -> None:
    from test_native_live_client import session

    from app.desktop.tickets import load_ticket

    service, _, _ = local
    path, _ = live_ticket(service)
    payload = json.loads(path.read_text())
    current = session(
        path.parent,
        plugin_version="0.1.40",
        inventory_path="/test/inventory.json",
        inventory_sha256="d" * 64,
    )
    payload.update(
        schema="green-atlas.transfer/4",
        plugin_version="0.1.40",
        live_session=current.model_dump(),
    )
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_ticket(path, service.ticket_root)


def live_ticket(service: LocalHandoff, *, changed: bool = False) -> tuple[Path, bytes]:
    package = service.ticket_root / f"live-ticket-{uuid4().hex}"
    package.mkdir()
    original = package / "Street.dwg"
    original.write_bytes(b"saved DWG context; native capture is authoritative")
    probe = valid_probe()
    probe["plugin_version"] = "0.1.37"
    probe["capture_mode"] = "live_document"
    probe["summary"].update(
        {
            "paths": 0,
            "points": 0,
            "area_proposals": 0,
            "area_proposal_candidates": 0,
            "area_proposal_rejected": 0,
        }
    )
    probe["source"].update(
        {
            "path": str(original),
            "sha256": sha256(original.read_bytes()).hexdigest(),
            "database_modified_flags": 32 if changed else 0,
            "live_database_matches_disk": not changed,
        }
    )
    capture = json.dumps(probe).encode()
    (package / "Drawing.autocad.json").write_bytes(capture)
    ticket = package / "transfer.gatransfer"
    ticket.write_text(
        json.dumps(
            {
                "schema": "green-atlas.transfer/2",
                "plugin_version": probe["plugin_version"],
                "producer": {"autocad_version": "2027.0.1", "target": "macos-arm64"},
                "source_name": "Street.dwg",
                "manifest": {
                    "entry": "Drawing.autocad.json",
                    "files": [
                        {
                            "name": "Drawing.autocad.json",
                            "kind": "live_capture",
                            "sha256": sha256(capture).hexdigest(),
                            "bytes": len(capture),
                        }
                    ],
                },
            }
        )
    )
    return ticket, capture


@pytest.mark.parametrize("changed", [False, True])
def test_live_ticket_opens_native_project_without_dxf_round_trip(
    local: LocalFixture, changed: bool
) -> None:
    service, runtime, _ = local
    ticket, capture = live_ticket(service, changed=changed)
    before = {path.name: path.read_bytes() for path in ticket.parent.iterdir()}
    receipt = service.submit(ticket)
    result = finish(service, receipt["id"])
    assert result["status"] == "needs_review", result
    assert result["project_path"] is not None
    assert result["project_path"].endswith("/setup")
    project = runtime.application.get(runtime.application.list_projects()[0].id)
    assert project.source_file is not None
    assert project.source_file.content_sha256 == sha256(capture).hexdigest()
    assert project.source_file.name == "Street.dwg"
    assert project.geometry is not None
    assert project.geometry.feature_collection["features"]
    assert runtime.application.repository.get_source(project.id) == capture
    assert not list((service.storage / "uploads").rglob("*.dxf"))
    assert {path.name: path.read_bytes() for path in ticket.parent.iterdir()} == before
    assert service.submit(ticket) == result
    assert len(runtime.application.list_projects()) == 1


def test_live_ticket_rejects_mutated_capture_before_project_creation(
    local: LocalFixture,
) -> None:
    service, runtime, _ = local
    ticket, _ = live_ticket(service)
    (ticket.parent / "Drawing.autocad.json").write_bytes(b"changed")
    result = finish(service, service.submit(ticket)["id"])
    assert result["status"] == "failed"
    assert runtime.application.list_projects() == []


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError])
def test_live_import_failure_preserves_evidence_and_retries_same_project(
    local: LocalFixture,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
) -> None:
    service, runtime, _ = local
    ticket, capture = live_ticket(service)
    importer = runtime.application.import_autocad_live_file

    def fail_import(*args: object, **kwargs: object) -> Project:
        raise error_type("live worker failure control")

    monkeypatch.setattr(runtime.application, "import_autocad_live_file", fail_import)
    receipt = service.submit(ticket)
    failed = finish(service, receipt["id"])
    assert failed["status"] == "failed"
    assert failed["project_path"] is None
    assert failed["message"] is not None
    assert "live worker failure control" not in failed["message"]
    evidence = next(
        record for record in caplog.records if record.name == "app.desktop.handoff"
    )
    assert evidence.exc_info is not None
    assert evidence.exc_info[0] is error_type
    assert receipt["id"] in evidence.getMessage()
    project_id = runtime.application.list_projects()[0].id
    assert runtime.application.get(project_id).source_file is None

    monkeypatch.setattr(runtime.application, "import_autocad_live_file", importer)
    service.submit(ticket)
    recovered = finish(service, receipt["id"])
    assert recovered["status"] == "needs_review"
    assert recovered["project_path"] == f"/projects/{project_id}/setup"
    assert [project.id for project in runtime.application.list_projects()] == [
        project_id
    ]
    source = runtime.application.get(project_id).source_file
    assert source is not None
    assert source.content_sha256 == sha256(capture).hexdigest()


def test_live_import_memory_failure_is_terminal_and_keeps_metrics_local(
    local: LocalFixture,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    service, runtime, _ = local
    ticket, capture = live_ticket(service)
    error = CadConversionMemoryExceeded(limit_bytes=500, peak_memory_bytes=1000)

    def exceed_memory(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(live_process, "run_converter", exceed_memory)
    failed = finish(service, service.submit(ticket)["id"])
    assert failed["status"] == "failed"
    assert failed["project_path"] is None
    assert failed["message"] == "Открытие остановлено: превышен лимит памяти импорта"
    project = runtime.application.get(runtime.application.list_projects()[0].id)
    assert project.source_file is None
    assert (ticket.parent / "Drawing.autocad.json").read_bytes() == capture
    assert str(error) in caplog.text
    assert "500" not in failed["message"] and "1000" not in failed["message"]


def test_repeat_concurrent_ticket_never_duplicates_project(local: LocalFixture) -> None:
    service, runtime, ticket = local
    with ThreadPoolExecutor(max_workers=4) as workers:
        receipts = list(workers.map(lambda _: service.submit(ticket), range(4)))
    assert len({item["id"] for item in receipts}) == 1
    result = finish(service, receipts[0]["id"])
    assert service.submit(ticket) == result
    assert len(runtime.application.list_projects()) == 1


def test_mutated_file_and_symlink_never_become_projects(local: LocalFixture) -> None:
    service, runtime, ticket = local
    drawing = ticket.parent / "Drawing.autocad.json"
    drawing.write_bytes(b"changed")
    result = finish(service, service.submit(ticket)["id"])
    assert result["status"] == "failed"
    assert not runtime.application.list_projects()
    link = ticket.parent / "alias"
    if sys.platform == "win32":
        from _winapi import CreateJunction

        CreateJunction(str(ticket.parent), str(link))
    else:
        link.symlink_to(ticket.parent, target_is_directory=True)
    try:
        with pytest.raises(ValueError):
            service.submit(link / ticket.name)
    finally:
        if sys.platform == "win32":
            link.rmdir()


def test_cancel_queued_ticket_does_not_create_project(local: LocalFixture) -> None:
    service, runtime, ticket = local
    gate = Event()
    service.pool.submit(lambda: gate.wait(5))
    receipt = service.submit(ticket)
    assert service.cancel(receipt["id"])["status"] == "cancelled"
    gate.set()
    assert finish(service, receipt["id"])["status"] == "cancelled"
    assert not runtime.application.list_projects()


def test_deleted_project_is_not_recreated_by_repeat(local: LocalFixture) -> None:
    service, runtime, ticket = local
    finish(service, service.submit(ticket)["id"])
    runtime.application.delete_project(runtime.application.list_projects()[0].id)
    with pytest.raises(KeyError):
        service.submit(ticket)
    assert not runtime.application.list_projects()


def test_changed_file_after_publication_does_not_recover_stale_receipt(
    local: LocalFixture,
) -> None:
    service, runtime, ticket = local
    finish(service, service.submit(ticket)["id"])
    (ticket.parent / "Drawing.autocad.json").write_bytes(b"changed")
    with pytest.raises(ValueError, match="изменился"):
        service.submit(ticket)
    assert len(runtime.application.list_projects()) == 1


def test_browser_cookie_cannot_submit_local_paths(
    local: LocalFixture, tmp_path: Path
) -> None:
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


def test_legacy_dxf_ticket_is_rejected_before_reserving_project(
    local: LocalFixture,
) -> None:
    service, runtime, ticket = local
    payload = json.loads(ticket.read_text())
    payload["schema"] = "green-atlas.transfer/1"
    ticket.write_text(json.dumps(payload))
    before = {path.name: path.read_bytes() for path in ticket.parent.iterdir()}
    with pytest.raises(ValueError):
        service.submit(ticket)
    assert service.list() == []
    assert runtime.application.list_projects() == []
    assert {path.name: path.read_bytes() for path in ticket.parent.iterdir()} == before


def test_native_handoff_admission_reports_editor_independence(
    local: LocalFixture,
) -> None:
    service, _, ticket = local
    gate = Event()
    service.pool.submit(lambda: gate.wait(5))
    try:
        receipt = service.submit(ticket)
        assert receipt["status"] == "queued"
        assert receipt["autocad_required"] is False
        assert service.get(receipt["id"])["autocad_required"] is False
    finally:
        gate.set()
        finish(service, receipt["id"])


def test_handoff_journal_migration_keeps_receipt_and_consistent_backup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "handoffs.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute(
            "CREATE TABLE local_handoffs(id TEXT PRIMARY KEY, path TEXT NOT NULL, "
            "digest TEXT NOT NULL, project_id TEXT NOT NULL, entry TEXT NOT NULL, "
            "status TEXT NOT NULL, stage TEXT NOT NULL, message TEXT, operation_id TEXT)"
        )
        db.execute(
            "INSERT INTO local_handoffs VALUES(?,?,?,?,?,?,?,?,?)",
            (
                "a" * 64,
                "/saved/transfer.gatransfer",
                "b" * 64,
                "project-1",
                "Drawing.autocad.json",
                "needs_review",
                "Готово",
                None,
                "operation-1",
            ),
        )
    journal = HandoffStore(database)
    receipt = journal.get("a" * 64)
    assert receipt.project_id == "project-1"
    assert receipt.operation_id == "operation-1"
    assert receipt.autocad_required is True
    backup = database.with_name(database.name + ".before-autocad-required")
    original = backup.read_bytes()
    with sqlite3.connect(backup) as db:
        assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert db.execute(
            "SELECT id,project_id,operation_id FROM local_handoffs"
        ).fetchone() == ("a" * 64, "project-1", "operation-1")
        assert "autocad_required" not in {
            row[1] for row in db.execute("PRAGMA table_info(local_handoffs)")
        }
    HandoffStore(database)
    assert backup.read_bytes() == original


def waiting_journal(tmp_path: Path) -> tuple[HandoffStore, str]:
    journal = HandoffStore(tmp_path / "handoffs.sqlite3")
    identifier = "a" * 64
    journal.reserve(
        identifier,
        tmp_path / "transfer.gatransfer",
        "b" * 64,
        "project",
        "Drawing.autocad.json",
    )
    return journal, identifier


@pytest.mark.parametrize(
    "change", ["claim", "stage", "publish", "fail", "cancel", "interrupt"]
)
def test_handoff_wait_is_woken_only_after_committed_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    journal, identifier = waiting_journal(tmp_path)
    if change in {"stage", "publish"}:
        journal.claim(identifier)
    before = journal.get(identifier)
    waiting = Event()
    original_wait = journal._changes.wait

    def observe_wait(timeout: float | None = None) -> bool:
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(journal._changes, "wait", observe_wait)
    original_notify = journal._notify
    committed: list[tuple[str, str]] = []

    def observe_commit(key: str) -> None:
        # Отдельное соединение видит данные до пробуждения клиента.
        with sqlite3.connect(journal.database) as db:
            row = db.execute(
                "SELECT status,stage FROM local_handoffs WHERE id=?", (key,)
            ).fetchone()
            assert row is not None
            committed.append((str(row[0]), str(row[1])))
        original_notify(key)

    monkeypatch.setattr(journal, "_notify", observe_commit)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(journal.wait, identifier, before.revision, Event())
        assert waiting.wait(2)
        assert not result.done()
        if change == "claim":
            journal.claim(identifier)
        elif change == "stage":
            journal.stage(identifier, "Читаем объекты")
        elif change == "publish":
            journal.publish(identifier, "Геометрия открыта")
        elif change == "fail":
            journal.fail(identifier, "Не удалось подготовить")
        elif change == "cancel":
            journal.cancel(identifier)
        else:
            journal.interrupt_active("Приложение закрыто")
        record = result.result(timeout=2)
    assert committed == [(record.status.value, record.stage)]
    assert record.revision == before.revision + 1


@pytest.mark.parametrize("terminal", ["publish", "fail", "cancel", "interrupt"])
def test_handoff_wait_returns_terminal_receipt_without_waiting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, terminal: str
) -> None:
    journal, identifier = waiting_journal(tmp_path)
    if terminal == "publish":
        journal.claim(identifier)
        journal.publish(identifier, "Геометрия открыта")
    elif terminal == "fail":
        journal.fail(identifier, "Не удалось подготовить")
    elif terminal == "cancel":
        journal.cancel(identifier)
    else:
        journal.interrupt_active("Приложение закрыто")
    record = journal.get(identifier)

    def unexpected_wait(timeout: float | None = None) -> bool:
        raise AssertionError("Завершённая передача не должна ожидать изменения")

    monkeypatch.setattr(journal._changes, "wait", unexpected_wait)
    assert journal.wait(identifier, record.revision, Event()) == record


def test_handoff_wait_does_not_lose_change_before_subscription(tmp_path: Path) -> None:
    journal, identifier = waiting_journal(tmp_path)
    before = journal.get(identifier)
    journal.claim(identifier)
    assert journal.wait(identifier, before.revision, Event()) == journal.get(identifier)
    # Перезапуск меняет активную запись на interrupted; прежняя ревизия не блокирует чтение.
    restarted = HandoffStore(journal.database)
    assert restarted.wait(identifier, before.revision, Event()).status == "interrupted"


@pytest.mark.parametrize("close", [False, True])
def test_handoff_wait_releases_on_disconnect_or_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, close: bool
) -> None:
    journal, identifier = waiting_journal(tmp_path)
    record = journal.get(identifier)
    waiting, stop = Event(), Event()
    original_wait = journal._changes.wait

    def observe_wait(timeout: float | None = None) -> bool:
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(journal._changes, "wait", observe_wait)
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(journal.wait, identifier, record.revision, stop)
        assert waiting.wait(2)
        journal.close() if close else journal.cancel_wait(stop)
        assert result.result(timeout=2) == record
    assert stop.is_set() is not close


def test_handoff_wait_timeout_returns_unchanged_receipt(tmp_path: Path) -> None:
    journal, identifier = waiting_journal(tmp_path)
    record = journal.get(identifier)
    assert journal.wait(identifier, record.revision, Event(), timeout=0.02) == record
    with pytest.raises(KeyError):
        journal.wait("c" * 64, 0, Event(), timeout=0.02)


def test_native_wait_preserves_auth_and_query_admission(
    local: LocalFixture, tmp_path: Path
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.desktop.web import SESSION_COOKIE, SESSION_HEADER, create_desktop_web

    service, _, ticket = local
    terminal = finish(service, service.submit(ticket)["id"])
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>fixture</title>")
    origin, secret = "http://127.0.0.1:18421", "a" * 43
    app = create_desktop_web(
        FastAPI(), web, origin=origin, secret=secret, handoff=service
    )
    path = f"/_desktop/handoffs/{terminal['id']}"
    with TestClient(app, base_url=origin, client=("127.0.0.1", 50001)) as client:
        client.cookies.set(SESSION_COOKIE, secret)
        assert client.get(path + "?after=0").status_code == 403
        assert (
            client.get(
                path,
                headers={SESSION_HEADER: secret, "Origin": "https://foreign.invalid"},
            ).status_code
            == 403
        )
        assert client.get(path, headers={SESSION_HEADER: "x" * 43}).status_code == 403
        response = client.get(
            path + f"?after={terminal['revision']}", headers={SESSION_HEADER: secret}
        )
        assert response.status_code == 200
        assert response.json() == terminal
        assert response.headers["cache-control"] == "no-store"
        for query in [
            "after=-1",
            "after=1.5",
            "after=",
            "after=1&after=2",
            "after=1&wait=10",
            "after=9223372036854775808",
        ]:
            assert (
                client.get(
                    path + "?" + query, headers={SESSION_HEADER: secret}
                ).status_code
                == 400
            )
        assert (
            client.get(
                "/_desktop/handoffs/" + "c" * 64 + "?after=0",
                headers={SESSION_HEADER: secret},
            ).status_code
            == 404
        )
    assert service.get(terminal["id"]) == terminal


@pytest.mark.parametrize("cancel_request", [False, True])
def test_native_wait_disconnect_or_request_cancellation_releases_thread(
    local: LocalFixture,
    monkeypatch: pytest.MonkeyPatch,
    cancel_request: bool,
) -> None:
    from fastapi import FastAPI

    from app.desktop.control import DesktopControl

    service, _, ticket = local
    gate = Event()
    service.pool.submit(lambda: gate.wait(10))
    receipt = service.submit(ticket)
    journal = service.journal
    original_wait = journal._changes.wait
    original_receipt = service.wait
    cancelled: list[Event] = []

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        waiting, finished = asyncio.Event(), asyncio.Event()
        queue: asyncio.Queue[Message] = asyncio.Queue()
        frames: list[Message] = []

        def observe_wait(timeout: float | None = None) -> bool:
            loop.call_soon_threadsafe(waiting.set)
            return original_wait(timeout)

        def observe_receipt(identifier: str, after: int, stop: Event) -> HandoffReceipt:
            cancelled.append(stop)
            try:
                return original_receipt(identifier, after, stop)
            finally:
                loop.call_soon_threadsafe(finished.set)

        monkeypatch.setattr(journal._changes, "wait", observe_wait)
        monkeypatch.setattr(service, "wait", observe_receipt)

        async def receive() -> Message:
            return await queue.get()

        async def send(frame: Message) -> None:
            frames.append(frame)

        path = f"/_desktop/handoffs/{receipt['id']}"
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": f"after={receipt['revision']}".encode(),
            "headers": [],
            "green_atlas.native": True,
        }
        await queue.put({"type": "http.request", "body": b"", "more_body": False})
        request = asyncio.create_task(
            DesktopControl(FastAPI(), service)(scope, receive, send)
        )
        try:
            await asyncio.wait_for(waiting.wait(), 2)
            if cancel_request:
                request.cancel()
            else:
                await queue.put({"type": "http.disconnect"})
            await asyncio.wait_for(asyncio.gather(request, return_exceptions=True), 2)
            await asyncio.wait_for(finished.wait(), 2)
            assert len(cancelled) == 1 and cancelled[0].is_set()
            assert frames == []
        finally:
            if not request.done():
                request.cancel()
                await asyncio.gather(request, return_exceptions=True)

    try:
        asyncio.run(scenario())
        assert service.get(receipt["id"]) == receipt
        assert len(service.futures) == 1
    finally:
        service.cancel(receipt["id"])
        gate.set()
        service.futures[receipt["id"]].result(timeout=5)


def test_handoff_reserve_and_queue_publish_committed_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = HandoffStore(tmp_path / "handoffs.sqlite3")
    identifier = "a" * 64
    original_notify = journal._notify
    statuses: list[str] = []

    def observe_commit(key: str) -> None:
        with sqlite3.connect(journal.database) as db:
            row = db.execute(
                "SELECT status FROM local_handoffs WHERE id=?", (key,)
            ).fetchone()
            assert row is not None
            statuses.append(str(row[0]))
        original_notify(key)

    monkeypatch.setattr(journal, "_notify", observe_commit)
    first = journal.reserve(
        identifier,
        tmp_path / "transfer.gatransfer",
        "b" * 64,
        "project",
        "Drawing.autocad.json",
    )
    assert first.revision == 1
    assert (
        journal.reserve(
            identifier,
            tmp_path / "transfer.gatransfer",
            "b" * 64,
            "project",
            "Drawing.autocad.json",
        )
        == first
    )
    journal.cancel(identifier)
    cancelled = journal.get(identifier)
    journal.queue(identifier)
    assert journal.wait(identifier, cancelled.revision, Event()).status == "queued"
    assert statuses == ["queued", "cancelled", "queued"]


def test_handoff_wait_does_not_read_sql_on_unchanged_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal, identifier = waiting_journal(tmp_path)
    before = journal.get(identifier)
    calls: list[str] = []
    original_get = journal.get

    def record_get(key: str) -> HandoffRecord:
        calls.append(key)
        return original_get(key)

    monkeypatch.setattr(journal, "get", record_get)
    assert journal.wait(identifier, before.revision, Event(), timeout=0.02) == before
    assert calls == [identifier]


def test_service_close_wakes_observer_with_durable_interruption(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, _, ticket = local
    gate, waiting = Event(), Event()
    service.pool.submit(lambda: gate.wait(10))
    before = service.submit(ticket)
    original_wait = service.journal._changes.wait

    def observe_wait(timeout: float | None = None) -> bool:
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(service.journal._changes, "wait", observe_wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        observed = pool.submit(service.wait, before["id"], before["revision"], Event())
        assert waiting.wait(2)
        closing = pool.submit(service.close)
        try:
            receipt = observed.result(timeout=2)
            assert receipt["status"] == "interrupted"
            with sqlite3.connect(service.database) as db:
                assert db.execute(
                    "SELECT status FROM local_handoffs WHERE id=?", (before["id"],)
                ).fetchone() == ("interrupted",)
        finally:
            gate.set()
            closing.result(timeout=5)


@pytest.mark.parametrize("action", ["stage", "cancel"])
def test_native_long_poll_returns_changed_json_without_resubmitting_ticket(
    local: LocalFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.desktop.web import SESSION_HEADER, create_desktop_web

    service, _, ticket = local
    gate, waiting = Event(), Event()
    service.pool.submit(lambda: gate.wait(10))
    receipt = service.submit(ticket)
    service.journal.claim(receipt["id"])
    receipt = service.get(receipt["id"])
    original_wait = service.journal._changes.wait

    def observe_wait(timeout: float | None = None) -> bool:
        waiting.set()
        return original_wait(timeout)

    monkeypatch.setattr(service.journal._changes, "wait", observe_wait)
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>fixture</title>")
    origin, secret = "http://127.0.0.1:18421", "a" * 43
    app = create_desktop_web(
        FastAPI(), web, origin=origin, secret=secret, handoff=service
    )
    path = f"/_desktop/handoffs/{receipt['id']}"
    try:
        with TestClient(app, base_url=origin, client=("127.0.0.1", 50001)) as client:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(
                    client.get,
                    path + f"?after={receipt['revision']}",
                    headers={SESSION_HEADER: secret},
                )
                assert waiting.wait(2)
                assert not pending.done()
                if action == "stage":
                    service.stage(receipt["id"], "Сохраняем проект")
                else:
                    assert (
                        client.post(
                            path + "/cancel", headers={SESSION_HEADER: secret}
                        ).status_code
                        == 200
                    )
                response = pending.result(timeout=2)
                assert response.status_code == 200
                assert response.headers["cache-control"] == "no-store"
                assert response.json() == service.get(receipt["id"])
                assert response.json()["revision"] == receipt["revision"] + 1
                assert len(service.futures) == 1
    finally:
        service.cancel(receipt["id"])
        gate.set()
        service.futures[receipt["id"]].result(timeout=5)


def test_native_wait_disconnect_keeps_event_loop_live_during_sqlite_lock(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import FastAPI

    from app.desktop.control import DesktopControl

    service, _, ticket = local
    record = service.journal.reserve(
        "a" * 64, ticket, "b" * 64, "project", "Drawing.autocad.json"
    )
    journal = service.journal
    original_connect = journal.connect
    monkeypatch.setattr("app.desktop.handoff_store.JOURNAL_BUSY_TIMEOUT_SECONDS", 0.3)
    blocker = sqlite3.connect(service.database)
    blocker.execute("BEGIN EXCLUSIVE")

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        entered, tick = asyncio.Event(), asyncio.Event()
        times: dict[str, float] = {}

        @contextmanager
        def observe_connect() -> Iterator[sqlite3.Connection]:
            loop.call_soon_threadsafe(entered.set)
            try:
                with original_connect() as db:
                    yield db
            finally:
                times["journal_released"] = time.monotonic()

        monkeypatch.setattr(journal, "connect", observe_connect)

        def event_loop_tick() -> None:
            times["event_loop_tick"] = time.monotonic()
            tick.set()

        async def receive() -> Message:
            await entered.wait()
            # Callback обязан выполниться, пока SELECT ждёт настоящий SQLite lock.
            loop.call_later(0.02, event_loop_tick)
            return {"type": "http.disconnect"}

        assert (
            await DesktopControl(FastAPI(), service).wait_receipt(
                record.id, record.revision, receive
            )
            is None
        )
        await asyncio.wait_for(tick.wait(), 1)
        assert times["event_loop_tick"] < times["journal_released"]

    try:
        asyncio.run(scenario())
    finally:
        blocker.rollback()
        blocker.close()
        monkeypatch.setattr(journal, "connect", original_connect)
    assert service.get(record.id)["status"] == "queued"
