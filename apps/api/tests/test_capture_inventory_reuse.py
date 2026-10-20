"""Привязка импортированного захвата использует проверку перечня без его копии."""

import os
from pathlib import Path
from unittest.mock import Mock
from uuid import uuid4
from weakref import ref

import pytest
from test_archived_handoff import archived_ticket
from test_desktop_handoff import LocalFixture
from test_desktop_handoff import local as local

from app.desktop.tickets import ArchivedTicket, load_ticket
from app.native_query.capture import capture_inputs
from app.native_query.capture.capture_store import NativeCaptureStore
from app.native_query.live import live_inventory
from app.operations.progress import OperationCancelled


def retain_fixture(local: LocalFixture, plugin_version: str = "0.1.47"):
    service, _, _ = local
    path = archived_ticket(service, plugin_version=plugin_version)
    _, ticket, _ = load_ticket(path, service.ticket_root)
    assert isinstance(ticket, ArchivedTicket)
    project_id = str(uuid4())
    store = service.native_captures
    root = store.retain(
        project_id,
        ticket.live_session.snapshot_sha256,
        ticket.plugin_version,
        path.parent,
        service.ticket_root,
        ticket.native_session,
        lambda: None,
    )
    return store, project_id, ticket.live_session, root


@pytest.mark.parametrize("plugin_version", ["0.1.46", "0.1.47"])
def test_metadata_binding_reuses_verified_inventory_without_retaining_objects(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch, plugin_version: str
) -> None:
    original = capture_inputs.read_inventory
    references = []

    def observe(*args, **kwargs):
        inventory = original(*args, **kwargs)
        references.append(ref(inventory))
        return inventory

    monkeypatch.setattr(capture_inputs, "read_inventory", observe)
    store, project_id, session, root = retain_fixture(local, plugin_version)
    assert len(references) == 1 and references[0]() is None
    loader = Mock(wraps=live_inventory.load_inventory)
    monkeypatch.setattr(live_inventory, "load_inventory", loader)
    retained = store.retained_session(project_id, session)
    assert retained.snapshot_path == str(root / "Drawing.autocad.json")
    assert retained.inventory_sha256 == session.inventory_sha256
    assert store.retained_session(project_id, session) == retained
    loader.assert_not_called()
    bound, inventory = store.retained_inputs(project_id, session)
    assert bound == retained and inventory.objects
    assert loader.call_count == 1


def test_reused_inventory_still_checks_requested_session_hash(
    local: LocalFixture,
) -> None:
    store, project_id, session, _ = retain_fixture(local)
    with pytest.raises(ValueError, match="Перечень объектов изменён"):
        store.retained_session(
            project_id, session.model_copy(update={"inventory_sha256": "0" * 64})
        )
    assert not store._sessions
    assert store.retained_session(project_id, session).inventory_sha256 == (
        session.inventory_sha256
    )


def test_inventory_changed_after_retain_cannot_reuse_validation(
    local: LocalFixture,
) -> None:
    store, project_id, session, root = retain_fixture(local)
    path = root / "Drawing.autocad.json.inventory.json"
    before = path.stat()
    content = path.read_bytes()
    path.write_bytes(b"X" + content[1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    with pytest.raises(ValueError, match="Снимок изменился"):
        store.retained_session(project_id, session)
    assert not store._sessions


def test_cold_store_checks_inventory_instead_of_trusting_file_presence(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, project_id, session, _ = retain_fixture(local)
    fresh = NativeCaptureStore(store.root)
    loader = Mock(wraps=live_inventory.load_inventory)
    monkeypatch.setattr(live_inventory, "load_inventory", loader)
    assert fresh.retained_session(project_id, session).inventory_sha256 == (
        session.inventory_sha256
    )
    assert loader.call_count == 1


def test_change_during_capture_validation_does_not_publish_proof(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    validate = capture_inputs.validate_inputs

    def changed(root: Path, *args, **kwargs):
        inventory = validate(root, *args, **kwargs)
        path = root / "Drawing.autocad.json.inventory.json"
        path.write_bytes(path.read_bytes() + b" ")
        return inventory

    monkeypatch.setattr(capture_inputs, "validate_inputs", changed)
    with pytest.raises(ValueError, match="изменился во время проверки"):
        retain_fixture(local)
    store = local[0].native_captures
    assert not store._validated_inventories
    assert not list(store.root.rglob("binding.json"))


def test_cancelled_validation_does_not_publish_proof(
    local: LocalFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        capture_inputs, "validate_inputs", Mock(side_effect=OperationCancelled("stop"))
    )
    with pytest.raises(OperationCancelled):
        retain_fixture(local)
    store = local[0].native_captures
    assert not store._validated_inventories
    assert not list(store.root.rglob("binding.json"))
