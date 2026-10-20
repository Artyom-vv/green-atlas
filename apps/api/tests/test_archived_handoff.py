"""Durable capture import must survive editor exit and transfer deletion."""

import json
import os
import shutil
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_desktop_handoff import finish, live_ticket
from test_desktop_handoff import local as local

from app.composition import create_runtime
from app.desktop.tickets import load_ticket
from app.native_query.archive.archive_client import ArchiveGeometryClient
from app.projects.workspace_backup import backup_workspace
from app.projects.workspace_restore import restore_workspace_backup


def archived_ticket(service, *, wrong_mapping=False, plugin_version="0.1.46"):
    path, _ = live_ticket(service)
    root = path.parent
    snapshot = root / "Drawing.autocad.json"
    probe = json.loads(snapshot.read_bytes())
    probe["plugin_version"] = plugin_version
    if plugin_version == "0.1.47":
        for row in probe["coverage"]:
            row.setdefault("source_layer", row["layer"])
    snapshot.write_text(json.dumps(probe))
    rows = probe["coverage"]
    inventory = {
        "schema": "green-atlas.live-inventory/1",
        "objects": [
            {
                "route": "/".join((*row["instance_chain"], row["handle"])),
                "layer": row["layer"],
                "entity_type": row["entity_type"],
                "context": row["status"] == "context",
                "bounds": None,
                "curve": row["entity_type"] == "AcDbLine",
                "error": "fixture has no bounds",
            }
            for row in rows
        ],
        "groups": [],
        "face_preparation_available": True,
    }
    inventory_path = root / "Drawing.autocad.json.inventory.json"
    inventory_path.write_text(json.dumps(inventory))
    native = root / "Native"
    native.mkdir()
    (native / "host.dwg").write_bytes(
        b"fixture CAD bytes, not a real AutoCAD qualification"
    )
    mapping = [
        {
            "source_route": item["route"],
            "archive_route": item["route"],
            "class": item["entity_type"],
            "layer": item["layer"],
            "unavailable": "",
            "transform": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
        }
        for item in inventory["objects"]
    ]
    if wrong_mapping:
        mapping[0]["layer"] = "another capture layer"
    if plugin_version == "0.1.47":
        for row in mapping:
            layer = row.pop("layer")
            row.update(authored_layer=layer, effective_layer=layer)
    (native / "instances.json").write_text(json.dumps(mapping))

    def entry(file, name=None):
        content = file.read_bytes()
        return {
            "name": name or file.relative_to(root).as_posix(),
            "bytes": len(content),
            "sha256": sha256(content).hexdigest(),
        }

    host = entry(native / "host.dwg")
    receipt = {
        "schema": "green-atlas.native-session/2"
        if plugin_version == "0.1.47"
        else "green-atlas.native-session/1",
        "plugin_version": plugin_version,
        "entry": "host.dwg",
        "units_code": 6,
        "source_path": probe["source"]["path"],
        "display_sha256": entry(snapshot)["sha256"],
        "source_dbmod_before": 0,
        "source_dbmod_after": 1,
        "source_disk_hashes_verified": True,
        "instance_count": len(mapping),
        "instances_sha256": entry(native / "instances.json")["sha256"],
        "files": [
            {"path": "host.dwg", "bytes": host["bytes"], "sha256": host["sha256"]}
        ],
        "references": [],
    }
    (native / "session.json").write_text(json.dumps(receipt))
    payload = json.loads(path.read_bytes())
    payload.update(
        schema="green-atlas.transfer/5",
        plugin_version=plugin_version,
        live_session={
            "session_id": "a" * 32,
            "pid": 99999999,
            "plugin_version": plugin_version,
            "source_path": probe["source"]["path"],
            "source_sha256": probe["source"]["sha256"],
            "snapshot_path": "/expired/map.json",
            "snapshot_sha256": entry(snapshot)["sha256"],
            "inventory_path": "/expired/map.json.inventory.json",
            "inventory_sha256": entry(inventory_path)["sha256"],
            "units_code": 6,
            "watched_databases": 1,
            "unavailable_xrefs": 0,
        },
        native_session={
            "entry": "Native/session.json",
            "files": [entry(file) for file in sorted(native.iterdir())]
            + [entry(snapshot), entry(inventory_path)],
        },
    )
    payload["manifest"]["files"] = [entry(snapshot) | {"kind": "live_capture"}]
    path.write_text(json.dumps(payload))
    return path


def test_complete_transfer_uses_no_editor_and_retains_inputs(local, monkeypatch):
    service, runtime, _ = local
    path = archived_ticket(service)
    client = Mock(
        side_effect=AssertionError("Import must not reach the old editor PID")
    )
    monkeypatch.setattr("app.desktop.handoff.LiveQueryClient", client)
    transfer = service.submit(path)
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    session = project.source_file.native_session
    assert Path(session.snapshot_path).is_relative_to(
        Path(runtime.database_path + ".cad-archives")
    )
    assert service.native_captures.retained_session(project.id, session) == session
    duplicate = service.submit(path)
    assert duplicate["id"] == transfer["id"]
    shutil.rmtree(path.parent)
    assert (
        service.native_captures.package(project.id, session.snapshot_sha256) is not None
    )
    assert service.native_captures.retained_session(project.id, session) == session
    engine = runtime.application.geometry.live_engine(project)
    assert isinstance(engine.client, ArchiveGeometryClient)
    engine.client.inspect(engine.session)
    client.assert_not_called()


def test_new_capture_version_publishes_without_reaching_editor(local, monkeypatch):
    service, runtime, _ = local
    path = archived_ticket(service, plugin_version="0.1.47")
    client = Mock(side_effect=AssertionError("New archive must not use editor PID"))
    monkeypatch.setattr("app.desktop.handoff.LiveQueryClient", client)
    transfer = service.submit(path)
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    session = project.source_file.native_session
    assert session.plugin_version == "0.1.47"
    assert service.native_captures.retained_session(project.id, session) == session
    client.assert_not_called()


def test_missing_inventory_is_rejected_before_project_publication(local):
    service, runtime, _ = local
    path = archived_ticket(service)
    (path.parent / "Drawing.autocad.json.inventory.json").unlink()
    transfer = service.submit(path)
    assert finish(service, transfer["id"])["status"] == "failed"
    assert runtime.application.list_projects() == []


def test_retained_session_reuses_validation_only_for_unchanged_files(
    local, monkeypatch
):
    from app.native_query.live import live_inventory

    service, runtime, _ = local
    transfer = service.submit(archived_ticket(service))
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    session = project.source_file.native_session
    store = service.native_captures
    store.retained_session(project.id, session)
    loader = Mock(
        side_effect=AssertionError("Unchanged capture must not be parsed again")
    )
    monkeypatch.setattr(live_inventory, "load_inventory", loader)
    assert store.retained_session(project.id, session) == session
    loader.assert_not_called()

    # Размер и mtime сохранены: изменение ctime всё равно отменяет проверку.
    path = store.directory(project.id, session.snapshot_sha256) / "host.dwg"
    observed = path.stat()
    content = path.read_bytes()
    path.write_bytes(b"X" + content[1:])
    os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns))
    with pytest.raises(ValueError, match="Снимок изменился"):
        store.retained_session(project.id, session)


def test_retained_session_rejects_symlink_after_cached_validation(local):
    service, runtime, _ = local
    transfer = service.submit(archived_ticket(service))
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    session = project.source_file.native_session
    store = service.native_captures
    store.retained_session(project.id, session)
    path = store.directory(project.id, session.snapshot_sha256) / "host.dwg"
    moved = path.with_name("replaced.dwg")
    path.rename(moved)
    try:
        path.symlink_to(moved)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows account cannot create file symlinks")
        raise
    with pytest.raises(ValueError, match="каталоге"):
        store.retained_session(project.id, session)


def test_retained_session_survives_package_cache_eviction(local, monkeypatch):
    service, runtime, _ = local
    transfer = service.submit(archived_ticket(service))
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    session = project.source_file.native_session
    store = service.native_captures
    verified_package = store._verified_package

    def evicted_after_verification(project_id, display_sha256):
        verified = verified_package(project_id, display_sha256)
        # Параллельная проверка другого проекта вправе вытеснить запись LRU.
        with store._cache_lock:
            store._packages.clear()
        return verified

    monkeypatch.setattr(store, "_verified_package", evicted_after_verification)
    assert store.retained_session(project.id, session) == session
    assert store.package(project.id, session.snapshot_sha256) is not None


def test_declared_inventory_cannot_belong_to_another_capture(local):
    service, _, _ = local
    path = archived_ticket(service)
    payload = json.loads(path.read_bytes())
    payload["live_session"]["inventory_sha256"] = "f" * 64
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="архив|Архив"):
        load_ticket(path, service.ticket_root)


def test_backup_restore_keeps_archive_and_resolves_new_profile_paths(local, tmp_path):
    service, runtime, _ = local
    path = archived_ticket(service)
    transfer = service.submit(path)
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project_id = service.journal.get(transfer["id"]).project_id
    original = runtime.project_repository.get(project_id)
    backup = backup_workspace(
        runtime.database_path, reason="archive recovery qualification"
    )
    manifest = json.loads((backup / "manifest.json").read_bytes())
    assert len(manifest["archive_files"]) == 6
    database = restore_workspace_backup(backup, tmp_path / "restored-profile")
    shutil.rmtree(service.native_captures.root)
    restored = create_runtime(database)
    try:
        project = restored.project_repository.get(project_id)
        assert project.source_file.native_session == original.source_file.native_session
        engine = restored.application.geometry.live_engine(project)
        engine.client.inspect(engine.session)
        assert Path(engine.session.snapshot_path).is_relative_to(
            Path(str(database) + ".cad-archives")
        )
    finally:
        restored.close()


def test_backup_refuses_a_damaged_bound_archive(local):
    service, runtime, _ = local
    path = archived_ticket(service)
    transfer = service.submit(path)
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    root = service.native_captures.directory(
        project.id, project.source_file.content_sha256
    )
    (root / "host.dwg").write_bytes(b"corrupt archive")
    before = set(Path(runtime.database_path + ".backups").glob("*/manifest.json"))
    with pytest.raises(ValueError, match="архив изменён"):
        backup_workspace(runtime.database_path, reason="damaged archive qualification")
    assert (
        set(Path(runtime.database_path + ".backups").glob("*/manifest.json")) == before
    )


def test_valid_file_hashes_do_not_allow_another_object_catalogue(local):
    service, runtime, _ = local
    path = archived_ticket(service, wrong_mapping=True)
    transfer = service.submit(path)
    assert finish(service, transfer["id"])["status"] == "failed"
    assert runtime.application.list_projects() == []


@pytest.mark.parametrize("relocated", [False, True])
def test_archive_engine_shares_one_validated_catalogue_with_requests(
    local, monkeypatch, relocated
):
    from app.native_query.archive import archive_client
    from app.native_query.live import live_inventory
    from app.native_query.measurement import live_provider
    from app.native_query.transport.process_contracts import NativeQueryProcessConfig

    service, runtime, _ = local
    transfer = service.submit(archived_ticket(service, plugin_version="0.1.47"))
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    if relocated:
        project.source_file.native_session = project.source_file.native_session.model_copy(
            update={
                "snapshot_path": "/previous-profile/Drawing.autocad.json",
                "inventory_path": "/previous-profile/Drawing.autocad.json.inventory.json",
            }
        )
    geometry = runtime.application.geometry
    store = geometry.archive_store
    with store._cache_lock:
        store._sessions.clear()
        store._packages.clear()
    loader = Mock(wraps=live_inventory.load_inventory)
    monkeypatch.setattr(live_inventory, "load_inventory", loader)
    duplicate = Mock(side_effect=AssertionError("Проверенный каталог уже прочитан"))
    monkeypatch.setattr(live_provider, "load_inventory", duplicate)
    monkeypatch.setattr(archive_client, "load_inventory", duplicate)
    engine = geometry.live_engine(project)
    assert loader.call_count == 1
    before = engine.inventory.model_dump(mode="json", by_alias=True)
    assert engine.client.inventory(engine.session) is engine.inventory
    assert geometry.live_engine(project) is engine
    root = Path(runtime.database_path).parent
    config = NativeQueryProcessConfig(
        core_executable=root / "fixture-core",
        worker_bundle=root / "fixture-worker",
        job_root=root / "fixture-jobs",
        bootstrap_template=root / "fixture-bootstrap",
        worker_binary_sha256="f" * 64,
        plugin_version="0.1.49",
    )
    engine.client.config_factory = lambda: config
    layers = tuple(sorted({item.layer for item in engine.inventory.objects}))
    first = engine.client.inputs(engine.session, layers)
    assert engine.client.inputs(engine.session, layers)[2] == first[2]
    assert engine.inventory.model_dump(mode="json", by_alias=True) == before
    assert loader.call_count == 1
    duplicate.assert_not_called()


@pytest.mark.parametrize("read", ["inventory", "inputs", "engine"])
def test_cached_archive_catalogue_still_rejects_changed_input(local, read):
    service, runtime, _ = local
    transfer = service.submit(archived_ticket(service))
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    geometry = runtime.application.geometry
    engine = geometry.live_engine(project)
    inventory = engine.client.inventory(engine.session)
    path = Path(engine.session.inventory_path)
    observed, content = path.stat(), path.read_bytes()
    path.write_bytes(b"X" + content[1:])
    os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns))
    with pytest.raises(ValueError, match="Снимок изменился"):
        if read == "engine":
            geometry.live_engine(project)
        elif read == "inputs":
            engine.client.inputs(engine.session, ())
        else:
            engine.client.inventory(engine.session)
    assert engine.inventory is inventory


def test_archive_catalogue_cancelled_load_is_not_cached(local, monkeypatch):
    from app.native_query.archive import archive_client
    from app.operations.progress import OperationCancelled

    service, runtime, _ = local
    transfer = service.submit(archived_ticket(service))
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    engine = runtime.application.geometry.live_engine(project)
    client = ArchiveGeometryClient(
        engine.client.store, project.id, engine.session, engine.client.config_factory
    )
    original = archive_client.load_inventory
    loader = Mock(side_effect=OperationCancelled("stop"))
    monkeypatch.setattr(archive_client, "load_inventory", loader)
    with pytest.raises(OperationCancelled):
        client.inventory(engine.session)
    assert client._inventory is None
    loader.side_effect = original
    inventory = client.inventory(engine.session)
    assert client.inventory(engine.session) is inventory
    assert inventory == engine.inventory
    assert loader.call_count == 2


def test_retained_catalogue_cannot_change_during_typed_load(local, monkeypatch):
    from app.native_query.live import live_inventory

    service, runtime, _ = local
    transfer = service.submit(archived_ticket(service))
    assert finish(service, transfer["id"])["status"] == "needs_review"
    project = runtime.project_repository.get(
        service.journal.get(transfer["id"]).project_id
    )
    store, session = service.native_captures, project.source_file.native_session
    store.retained_session(project.id, session)
    original = live_inventory.load_inventory

    def changed_after_parse(retained, *, check):
        inventory = original(retained, check=check)
        path = Path(retained.inventory_path)
        path.write_bytes(path.read_bytes() + b" ")
        return inventory

    monkeypatch.setattr(live_inventory, "load_inventory", changed_after_parse)
    with pytest.raises(ValueError, match="изменился во время чтения каталога"):
        store.retained_inputs(project.id, session)
