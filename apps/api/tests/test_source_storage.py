import json
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest

from app.planning.contracts import Plan, PlanObject
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.projects.workspace_backup import backup_workspace
from app.projects.workspace_restore import restore_workspace_backup


def legacy_database(tmp_path: Path, layout: str) -> tuple[Path, str]:
    database = tmp_path / "projects.sqlite3"
    repository = SqliteProjectRepository(database)
    project = repository.create(
        Project(
            name="Сохранённый проект",
            plan=Plan(
                version=7,
                objects=[
                    PlanObject(
                        id="saved-tree",
                        kind="tree",
                        x=12,
                        y=24,
                        radius=1,
                        locked=True,
                    )
                ],
            ),
        )
    )
    repository.close()
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE project_sources")
        if layout in {"separate", "both", "conflict"}:
            db.execute(
                "CREATE TABLE project_sources(project_id TEXT PRIMARY KEY,content BLOB NOT NULL)"
            )
            db.execute(
                "INSERT INTO project_sources VALUES (?, ?)", (project.id, b"original")
            )
        if layout in {"inline", "both", "conflict"}:
            db.execute(
                "UPDATE projects SET source=? WHERE id=?",
                (b"different" if layout == "conflict" else b"original", project.id),
            )
    return database, project.id


@pytest.mark.parametrize("layout", ["inline", "separate", "both"])
def test_legacy_layout_reads_source_and_header_without_rewriting_bytes(
    tmp_path, layout
):
    database, identifier = legacy_database(tmp_path, layout)
    repository = SqliteProjectRepository(database)
    try:
        assert repository.get_source(identifier) == b"original"
        info = repository.get_source_info(identifier, prefix_bytes=3)
        assert (info.size, info.prefix) == (8, b"ori")
        restored = repository.get(identifier)
        assert restored.name == "Сохранённый проект"
        assert restored.plan.version == 7
        assert restored.plan.objects[0].id == "saved-tree"
        assert restored.plan.objects[0].locked
        with sqlite3.connect(database) as db:
            inline = db.execute("SELECT source FROM projects").fetchone()[0]
        assert inline == (b"original" if layout in {"inline", "both"} else None)
    finally:
        repository.close()


def test_conflicting_legacy_copies_cannot_be_selected_silently(tmp_path):
    database, identifier = legacy_database(tmp_path, "conflict")
    repository = SqliteProjectRepository(database)
    try:
        with pytest.raises(ValueError, match="не совпадают"):
            repository.get_source(identifier)
        with pytest.raises(ValueError, match="не совпадают"):
            repository.get_source_info(identifier, prefix_bytes=3)
        assert repository.get(identifier).name == "Сохранённый проект"
    finally:
        repository.close()


@pytest.mark.parametrize(
    "content", [b"", b"new", b"x" * (1024 * 1024 + 7)], ids=["empty", "small", "large"]
)
def test_new_source_is_frozen_checked_and_deleted_with_project(tmp_path, content):
    database, identifier = legacy_database(tmp_path, "inline")
    repository = SqliteProjectRepository(database)
    try:
        repository.save_source(identifier, bytearray(content))
        assert repository.get_source(identifier) == content
        assert (
            repository.get_source_info(identifier, prefix_bytes=2).prefix == content[:2]
        )
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT source FROM projects").fetchone()[0] is None
            assert (
                db.execute("SELECT content_sha256 FROM project_sources").fetchone()[0]
                == sha256(content).hexdigest()
            )
            db.execute("UPDATE project_sources SET content_sha256=?", ("0" * 64,))
        with pytest.raises(ValueError, match="целостность"):
            repository.get_source(identifier)
        project = repository.get(identifier)
        with pytest.raises(ProjectVersionConflict):
            repository.delete(identifier, expected_version=project.state_version + 1)
        repository.delete(identifier, expected_version=project.state_version)
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM project_sources").fetchone()[0] == 0
    finally:
        repository.close()


def test_source_write_failure_rolls_back_project_and_source(tmp_path, monkeypatch):
    database, identifier = legacy_database(tmp_path, "inline")
    repository = SqliteProjectRepository(database)
    try:
        project = repository.get(identifier)
        original_version, original_time = project.state_version, project.updated_at
        project.name = "Несохранённое изменение"

        def fail(*args):
            raise sqlite3.DatabaseError("test source write failure")

        monkeypatch.setattr(repository._sources, "write", fail)
        with pytest.raises(sqlite3.DatabaseError):
            repository.save(project, source=b"new")
        assert project.state_version == original_version
        assert project.updated_at == original_time
        assert repository.get(identifier).name == "Сохранённый проект"
        assert repository.get_source(identifier) == b"original"
    finally:
        repository.close()


def test_backup_precedes_migration_and_preserves_wal_and_immutable_geometry(tmp_path):
    database, identifier = legacy_database(tmp_path, "separate")
    prepared = Path(str(database) + ".prepared-geometry")
    prepared.mkdir()
    immutable = b'{"payload":"saved physical basis","sha256":"test"}'
    (prepared / "basis.json").write_bytes(immutable)
    (prepared / "project-reference.json").write_bytes(b'{"key":"basis"}')
    (prepared / "domains").mkdir()
    (prepared / "domains" / "derived.json").write_text("derived cache")
    # Keep the writer open so the backup must include committed WAL frames.
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute(
            "UPDATE projects SET updated_at='wal-committed' WHERE id=?", (identifier,)
        )
        writer.commit()
        repository = SqliteProjectRepository(database)
        repository.close()
    backups = list(Path(str(database) + ".backups").glob("*/manifest.json"))
    assert len(backups) == 1
    folder = backups[0].parent
    manifest = json.loads(backups[0].read_text())
    assert len(manifest["prepared_files"]) == 2
    assert (folder / prepared.name / "basis.json").read_bytes() == immutable
    assert not (folder / prepared.name / "domains").exists()
    copied = folder / database.name
    assert sha256(copied.read_bytes()).hexdigest() == manifest["database"]["sha256"]
    with sqlite3.connect(copied) as db:
        assert (
            db.execute("SELECT updated_at FROM projects").fetchone()[0]
            == "wal-committed"
        )
        assert "content_sha256" not in {
            row[1] for row in db.execute("PRAGMA table_info(project_sources)")
        }
        assert (
            db.execute("SELECT content FROM project_sources").fetchone()[0]
            == b"original"
        )
    # A normal reopen does not create another schema recovery point.
    repository = SqliteProjectRepository(database)
    repository.close()
    assert len(list(Path(str(database) + ".backups").glob("*/manifest.json"))) == 1


def test_backup_failure_leaves_schema_unchanged(tmp_path, monkeypatch):
    database, _ = legacy_database(tmp_path, "separate")

    def fail(*args, **kwargs):
        raise OSError("test unavailable backup destination")

    monkeypatch.setattr("app.projects.adapters.backup_workspace", fail)
    with pytest.raises(OSError):
        SqliteProjectRepository(database)
    with sqlite3.connect(database) as db:
        assert "content_sha256" not in {
            row[1] for row in db.execute("PRAGMA table_info(project_sources)")
        }


def test_explicit_backup_does_not_follow_prepared_symlinks(tmp_path):
    database, _ = legacy_database(tmp_path, "inline")
    prepared = Path(str(database) + ".prepared-geometry")
    prepared.mkdir()
    private = tmp_path / "elsewhere.json"
    private.write_text("private data")
    (prepared / "aliased.json").symlink_to(private)
    with pytest.raises(ValueError, match="файлом"):
        backup_workspace(database, reason="test")
    assert not list(Path(str(database) + ".backups").glob("*/manifest.json"))


def test_backup_refuses_uncommitted_connection_without_hanging(tmp_path):
    database, identifier = legacy_database(tmp_path, "inline")
    with sqlite3.connect(database) as db:
        db.execute(
            "UPDATE projects SET updated_at='uncommitted' WHERE id=?", (identifier,)
        )
        with pytest.raises(ValueError, match="транзакцию"):
            backup_workspace(database, reason="test", connection=db)


@pytest.mark.parametrize("pass_connection", [False, True])
def test_compact_backup_keeps_wal_keys_blobs_and_revisions_without_changing_source(
    tmp_path, pass_connection
):
    database, identifier = legacy_database(tmp_path / "папка ' копии", "inline")
    with sqlite3.connect(database) as setup:
        setup.execute("CREATE TABLE removed_data(value BLOB NOT NULL)")
        setup.executemany(
            "INSERT INTO removed_data VALUES (?)",
            [(b"discarded pages" * 8192,) for _ in range(64)],
        )
        setup.execute("DELETE FROM removed_data")
        setup.execute(
            "CREATE TABLE explicit_keys(id INTEGER PRIMARY KEY, content BLOB NOT NULL)"
        )
        setup.executemany(
            "INSERT INTO explicit_keys VALUES (?, ?)",
            [(17, b"\x00\xfffirst"), (3000, b"\xff\x00last")],
        )
        setup.execute(
            "CREATE TABLE text_keys(id TEXT PRIMARY KEY, content BLOB NOT NULL)"
        )
        setup.executemany(
            "INSERT INTO text_keys(rowid,id,content) VALUES (?, ?, ?)",
            [(101, "first", b"\x00\xff"), (7001, "last", b"\xff\x00")],
        )
    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("PRAGMA user_version=73")
        writer.execute("PRAGMA application_id=1195463756")
        payload, projection = writer.execute(
            "SELECT payload,projection FROM projects WHERE id=?", (identifier,)
        ).fetchone()
        updated_at = "2026-10-17T12:00:00+00:00"
        updated = []
        for value in (payload, projection):
            record = json.loads(value)
            record.update(state_version=23, updated_at=updated_at)
            updated.append(json.dumps(record, ensure_ascii=False))
        writer.execute(
            "UPDATE projects SET payload=?,projection=?,state_version=23,updated_at=? "
            "WHERE id=?",
            (*updated, updated_at, identifier),
        )
        writer.commit()
        wal = Path(str(database) + "-wal")
        assert wal.stat().st_size > 0
        source_pages = writer.execute("PRAGMA page_count").fetchone()[0]
        assert writer.execute("PRAGMA freelist_count").fetchone()[0] > 1024
        source_schema = writer.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        source_project = writer.execute(
            "SELECT * FROM projects WHERE id=?", (identifier,)
        ).fetchone()
        source_text_keys = writer.execute(
            "SELECT rowid,id,content FROM text_keys ORDER BY rowid"
        ).fetchall()
        source_hashes = {
            path: sha256(path.read_bytes()).hexdigest() for path in (database, wal)
        }

        backup = backup_workspace(
            database,
            reason="compact-wal-control",
            connection=writer if pass_connection else None,
        )
        manifest = json.loads((backup / "manifest.json").read_text())
        copied = backup / manifest["database"]["path"]
        assert manifest["database"]["encoding"] == "identity"
        with sqlite3.connect(copied) as compact:
            assert compact.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert compact.execute("PRAGMA freelist_count").fetchone()[0] == 0
            assert compact.execute("PRAGMA page_count").fetchone()[0] < source_pages / 4
            assert compact.execute("PRAGMA user_version").fetchone()[0] == 73
            assert compact.execute("PRAGMA application_id").fetchone()[0] == 1195463756
            assert (
                compact.execute(
                    "SELECT * FROM projects WHERE id=?", (identifier,)
                ).fetchone()
                == source_project
            )
            assert compact.execute(
                "SELECT id,content FROM explicit_keys ORDER BY id"
            ).fetchall() == [(17, b"\x00\xfffirst"), (3000, b"\xff\x00last")]
            assert compact.execute(
                "SELECT id,content FROM text_keys ORDER BY id"
            ).fetchall() == [("first", b"\x00\xff"), ("last", b"\xff\x00")]
        assert {
            path: sha256(path.read_bytes()).hexdigest() for path in (database, wal)
        } == source_hashes
        assert (
            writer.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name"
            ).fetchall()
            == source_schema
        )
        assert (
            writer.execute(
                "SELECT rowid,id,content FROM text_keys ORDER BY rowid"
            ).fetchall()
            == source_text_keys
        )
        assert (
            writer.execute(
                "SELECT * FROM projects WHERE id=?", (identifier,)
            ).fetchone()
            == source_project
        )
    finally:
        writer.close()


def test_compressed_backup_restores_wal_source_plan_and_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.projects.backup_files.BACKUP_COMPRESSION_THRESHOLD_BYTES", 0
    )
    database, identifier = legacy_database(tmp_path, "separate")
    prepared = Path(str(database) + ".prepared-geometry")
    prepared.mkdir()
    immutable = b'{"physical_basis":"original coordinates"}'
    (prepared / "basis.json").write_bytes(immutable)
    with sqlite3.connect(database) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute(
            "UPDATE projects SET updated_at='in-wal' WHERE id=?", (identifier,)
        )
        writer.commit()
        backup = backup_workspace(database, reason="compressed-control")
        manifest = json.loads((backup / "manifest.json").read_text())
        assert manifest["database"]["encoding"] == "gzip"
        assert not (backup / database.name).exists()
        assert manifest["prepared_files"][0]["encoding"] == "gzip"
        restored = restore_workspace_backup(backup, tmp_path / "restored")
    assert (restored.parent / prepared.name / "basis.json").read_bytes() == immutable
    assert (prepared / "basis.json").read_bytes() == immutable
    with sqlite3.connect(restored) as db:
        row = db.execute(
            "SELECT payload,updated_at FROM projects WHERE id=?", (identifier,)
        ).fetchone()
        project = Project.model_validate_json(row[0])
        assert row[1] == "in-wal"
        assert project.plan.version == 7
        assert (
            project.plan.objects[0].id,
            project.plan.objects[0].x,
            project.plan.objects[0].y,
        ) == ("saved-tree", 12, 24)
        assert (
            db.execute(
                "SELECT content FROM project_sources WHERE project_id=?", (identifier,)
            ).fetchone()[0]
            == b"original"
        )
        assert "content_sha256" not in {
            row[1] for row in db.execute("PRAGMA table_info(project_sources)")
        }


def test_restore_accepts_original_backup_format(tmp_path):
    database, _ = legacy_database(tmp_path, "inline")
    backup = backup_workspace(database, reason="legacy-control")
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema"] = "green-atlas.workspace-backup/1"
    manifest["database"] = {
        key: manifest["database"][key] for key in ("path", "sha256")
    }
    manifest_path.write_text(json.dumps(manifest))
    restored = restore_workspace_backup(backup, tmp_path / "restored")
    with sqlite3.connect(restored) as db:
        assert db.execute("SELECT source FROM projects").fetchone()[0] == b"original"


def test_corrupt_compressed_backup_does_not_publish_restore(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "app.projects.backup_files.BACKUP_COMPRESSION_THRESHOLD_BYTES", 0
    )
    database, _ = legacy_database(tmp_path, "inline")
    backup = backup_workspace(database, reason="corrupt-control")
    manifest = json.loads((backup / "manifest.json").read_text())
    source = backup / manifest["database"]["path"]
    data = bytearray(source.read_bytes())
    data[-1] ^= 1
    source.write_bytes(data)
    destination = tmp_path / "restore-must-not-appear"
    with pytest.raises(ValueError, match="целостность"):
        restore_workspace_backup(backup, destination)
    assert not destination.exists()
    assert not list(tmp_path.glob(".restore-*"))


def test_backup_compression_verification_failure_keeps_original_and_schema(
    tmp_path, monkeypatch
):
    from io import BytesIO

    monkeypatch.setattr(
        "app.projects.backup_files.BACKUP_COMPRESSION_THRESHOLD_BYTES", 0
    )
    monkeypatch.setattr(
        "app.projects.backup_files.gzip.open",
        lambda *args, **kwargs: BytesIO(b"wrong decoded bytes"),
    )
    database, identifier = legacy_database(tmp_path, "inline")
    with pytest.raises(ValueError, match="сжатую"):
        backup_workspace(database, reason="failed-compression")
    with sqlite3.connect(database) as db:
        assert (
            db.execute(
                "SELECT source FROM projects WHERE id=?", (identifier,)
            ).fetchone()[0]
            == b"original"
        )
    assert not list(Path(str(database) + ".backups").glob("*/manifest.json"))
    assert not list(Path(str(database) + ".backups").glob(".pending-*"))


def test_restore_never_overwrites_existing_profile_or_accepts_path_escape(tmp_path):
    database, _ = legacy_database(tmp_path, "inline")
    backup = backup_workspace(database, reason="restore-boundary")
    destination = tmp_path / "existing-profile"
    destination.mkdir()
    user_file = destination / "user.txt"
    user_file.write_text("keep")
    with pytest.raises(ValueError, match="пустым"):
        restore_workspace_backup(backup, destination)
    assert user_file.read_text() == "keep"
    manifest_path = backup / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["database"]["original_path"] = "../outside.sqlite3"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="выходит"):
        restore_workspace_backup(backup, tmp_path / "restored")
    assert not (tmp_path / "outside.sqlite3").exists()
