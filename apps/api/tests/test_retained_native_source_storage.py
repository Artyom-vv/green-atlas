"""Неизменяемый native-источник хранится один раз в допущенном архиве проекта."""

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest
from test_archived_handoff import archived_ticket
from test_desktop_handoff import finish
from test_desktop_handoff import local as local

from app.dxf_import.contracts import ImportMode, ImportStatus, SourceFile
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.projects.workspace_backup import backup_workspace
from app.projects.workspace_restore import restore_workspace_backup
from app.shared.failures import ProjectNotFound


def retained_source(tmp_path, *, content=None):
    """Storage-fixture проверяет байты и привязку, а не чтение CAD через SDK."""
    database = tmp_path / "projects.sqlite3"
    repository = SqliteProjectRepository(database)
    project = repository.create(Project(name="Native-источник"))
    content = content or b'{"schema":"storage-fixture","objects":["kept"]}'
    digest = sha256(content).hexdigest()
    root = Path(str(database) + ".cad-archives") / project.id / digest
    root.mkdir(parents=True)
    files = {
        "Drawing.autocad.json": content,
        "Drawing.autocad.json.inventory.json": b'{"objects":[]}',
        "Native/session.json": b'{"schema":"green-atlas.native-session/3"}',
        "Native/instances.json": b"[]",
        "Native/semantics.json": b'{"schema":"green-atlas.native-semantics/1"}',
        "Native/host.dwg": b"storage-only-CAD-bytes",
    }
    entries = []
    for name, value in files.items():
        (root / Path(name).name).write_bytes(value)
        entries.append(
            {"name": name, "bytes": len(value), "sha256": sha256(value).hexdigest()}
        )
    (root / "binding.json").write_text(
        json.dumps(
            {
                "project_id": project.id,
                "display_sha256": digest,
                "session_sha256": next(
                    row["sha256"]
                    for row in entries
                    if row["name"] == "Native/session.json"
                ),
                "transfer": {"entry": "Native/session.json", "files": entries},
            }
        )
    )
    project.source_file = SourceFile(
        name="Источник.dwg",
        content_sha256=digest,
        size=len(content),
        imported_at="2026-10-11",
        dxf_version="AutoCAD live snapshot",
        units="м",
        entity_count=1,
    )
    project.import_status = ImportStatus(mode=ImportMode.AUTOCAD_LIVE)
    return repository, project, root, content


def test_native_file_source_can_exceed_sqlite_blob_limit(tmp_path):
    repository, project, root, content = retained_source(
        tmp_path, content=b'{"fixture":"' + b"x" * 32768 + b'"}'
    )
    repository._connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16384)
    try:
        with pytest.raises(sqlite3.DataError):
            repository.save(project, source=content)
        assert repository.get(project.id).source_file is None
        repository.save_retained_source(project, root)
        assert repository.get_source(project.id) == content
        assert (
            repository.get_source_info(project.id, prefix_bytes=4).prefix == content[:4]
        )
    finally:
        repository.close()


def test_actual_archived_handoff_does_not_materialize_snapshot_bytes(
    local, monkeypatch
):
    service, runtime, _ = local
    path = archived_ticket(service)
    read_bytes = Path.read_bytes

    def refuse_snapshot_bytes(candidate):
        if candidate.name == "Drawing.autocad.json":
            raise AssertionError("Native import must stream its retained source")
        return read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", refuse_snapshot_bytes)
    transfer = service.submit(path)
    assert finish(service, transfer["id"])["status"] == "needs_review"
    identifier = service.journal.get(transfer["id"]).project_id
    assert runtime.project_repository.get_source(identifier)
    with sqlite3.connect(runtime.database_path) as database:
        assert (
            database.execute("SELECT count(*) FROM project_sources").fetchone()[0] == 0
        )


def test_retained_source_reopens_before_session_binding_without_blob(
    tmp_path, monkeypatch
):
    repository, project, root, content = retained_source(tmp_path)

    def no_blob(*args):
        raise AssertionError("Retained native source must not write a second BLOB")

    monkeypatch.setattr(repository._sources, "write", no_blob)
    repository.save_retained_source(project, root)
    with sqlite3.connect(repository.path) as database:
        assert (
            database.execute("SELECT count(*) FROM project_sources").fetchone()[0] == 0
        )
        assert database.execute("SELECT source FROM projects").fetchone()[0] is None
    repository.close()
    reopened = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        assert reopened.get(project.id).source_file.native_session is None
        assert reopened.get_source(project.id) == content
        assert (
            reopened.get_source_info(project.id, prefix_bytes=5).prefix == content[:5]
        )
        assert reopened.get_source_info(project.id, prefix_bytes=0).size == len(content)
        assert (root / "Drawing.autocad.json").read_bytes() == content
    finally:
        reopened.close()


@pytest.mark.parametrize(
    "fault",
    ["missing", "changed", "binding-sha", "symlink", "binding-symlink", "root-symlink"],
)
def test_retained_source_never_masks_corrupt_or_aliased_inputs(tmp_path, fault):
    repository, project, root, content = retained_source(tmp_path)
    repository.save_retained_source(project, root)
    source = root / "Drawing.autocad.json"
    if fault == "missing":
        source.unlink()
    elif fault == "changed":
        source.write_bytes(content.replace(b"kept", b"lost"))
    elif fault == "binding-sha":
        binding = json.loads((root / "binding.json").read_text())
        binding["transfer"]["files"][0]["sha256"] = "0" * 64
        (root / "binding.json").write_text(json.dumps(binding))
    elif fault in {"symlink", "binding-symlink"}:
        candidate = source if fault == "symlink" else root / "binding.json"
        elsewhere = tmp_path / "aliased-file"
        candidate.rename(elsewhere)
        candidate.symlink_to(elsewhere)
    else:
        elsewhere = tmp_path / "aliased-root"
        root.rename(elsewhere)
        root.symlink_to(elsewhere, target_is_directory=True)
    try:
        for read in (
            lambda: repository.get_source(project.id),
            lambda: repository.get_source_info(project.id, prefix_bytes=5),
        ):
            with pytest.raises((ValueError, OSError)):
                read()
    finally:
        repository.close()


def test_save_retained_source_checks_scope_and_identity_before_cas(tmp_path):
    repository, project, root, content = retained_source(tmp_path)
    original = repository.get(project.id)
    try:
        with pytest.raises(ValueError, match="CAD-архиве"):
            repository.save_retained_source(project, root.parent)
        project.source_file.size += 1
        with pytest.raises(ValueError, match="привязке"):
            repository.save_retained_source(project, root)
        project.source_file.size = len(content)
        project.import_status.mode = ImportMode.SOURCE_DXF
        with pytest.raises(ValueError, match="native-проект"):
            repository.save_retained_source(project, root)
        assert repository.get(project.id) == original
    finally:
        repository.close()


def test_retained_source_cas_and_rollback_preserve_existing_blob(tmp_path, monkeypatch):
    repository, project, root, content = retained_source(tmp_path)
    original = repository.get(project.id)
    repository.save(original, source=b"previous")
    try:
        with pytest.raises(ProjectVersionConflict):
            repository.save_retained_source(project, root)
        assert repository.get_source(project.id) == b"previous"
        project.state_version = original.state_version

        def fail(*args):
            raise sqlite3.DatabaseError("retained-source write failed")

        monkeypatch.setattr(repository._sources, "delete", fail)
        with pytest.raises(sqlite3.DatabaseError):
            repository.save_retained_source(project, root)
        assert repository.get(project.id).source_file is None
        assert repository.get_source(project.id) == b"previous"
        assert project.state_version == original.state_version
        assert (root / "Drawing.autocad.json").read_bytes() == content
    finally:
        repository.close()


def test_blob_precedes_retained_file_but_its_corruption_is_not_masked(tmp_path):
    repository, project, root, content = retained_source(tmp_path)
    repository.save(project, source=content)
    (root / "Drawing.autocad.json").unlink()
    try:
        assert repository.get_source(project.id) == content
        assert (
            repository.get_source_info(project.id, prefix_bytes=3).prefix == content[:3]
        )
        with sqlite3.connect(repository.path) as database:
            database.execute("UPDATE project_sources SET content_sha256=?", ("0" * 64,))
        with pytest.raises(ValueError, match="целостность"):
            repository.get_source(project.id)
    finally:
        repository.close()


def test_retained_source_backup_restores_identity_and_semantics_before_bind(tmp_path):
    repository, project, root, content = retained_source(tmp_path)
    repository.save_retained_source(project, root)
    backup = backup_workspace(repository.path, reason="retained-source-control")
    manifest = json.loads((backup / "manifest.json").read_text())
    assert any("semantics.json" in row["path"] for row in manifest["archive_files"])
    restored = restore_workspace_backup(backup, tmp_path / "restored")
    reopened = SqliteProjectRepository(restored)
    try:
        assert reopened.get_source(project.id) == content
        restored_root = (
            Path(str(restored) + ".cad-archives")
            / project.id
            / project.source_file.content_sha256
        )
        for original in root.iterdir():
            assert (restored_root / original.name).read_bytes() == original.read_bytes()
        before = reopened.get(project.id)
        with pytest.raises(ProjectVersionConflict):
            reopened.delete(project.id, expected_version=before.state_version + 1)
        assert reopened.get_source(project.id) == content
        reopened.delete(project.id, expected_version=before.state_version)
        with pytest.raises(ProjectNotFound):
            reopened.get_source(project.id)
    finally:
        reopened.close()
        repository.close()


@pytest.mark.parametrize("blob", [False, True])
def test_backup_requires_archive_when_it_is_the_only_source(tmp_path, blob):
    repository, project, root, content = retained_source(tmp_path)
    if blob:
        repository.save(project, source=content)
    else:
        repository.save_retained_source(project, root)
    root.rename(tmp_path / "missing-package")
    try:
        if blob:
            backup = backup_workspace(repository.path, reason="legacy-blob-control")
            restored = restore_workspace_backup(backup, tmp_path / "restored")
            reopened = SqliteProjectRepository(restored)
            try:
                assert reopened.get_source(project.id) == content
            finally:
                reopened.close()
        else:
            with pytest.raises(ValueError, match="единственный"):
                backup_workspace(repository.path, reason="missing-native-source")
            assert not list(Path(repository.path + ".backups").glob("*/manifest.json"))
    finally:
        repository.close()
