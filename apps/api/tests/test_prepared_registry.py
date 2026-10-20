"""Хеш производного файла берётся из базы, отдельно от самого кеша."""

import sqlite3
from unittest.mock import Mock

import pytest

from app.native_query.preparation import prepared_registry
from app.native_query.preparation.prepared_registry import (
    read_prepared_registration,
    save_prepared_registration,
)


def registration(**updates):
    return {
        "key": "a" * 64,
        "source_sha256": "b" * 64,
        "payload_sha256": "c" * 64,
        "sha256": "d" * 64,
        "bytes": 500,
        **updates,
    }


def test_missing_catalog_read_does_not_create_database(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    assert read_prepared_registration(path, "a" * 64, "b" * 64) is None
    assert not path.exists()


def test_registry_pins_source_and_replaces_only_matching_resource(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    first = registration()
    second = registration(source_sha256="e" * 64, sha256="f" * 64)
    save_prepared_registration(path, first)
    save_prepared_registration(path, second)
    assert (
        read_prepared_registration(path, first["key"], first["source_sha256"]) == first
    )
    assert (
        read_prepared_registration(path, second["key"], second["source_sha256"])
        == second
    )
    updated = registration(sha256="1" * 64, bytes=1000)
    save_prepared_registration(path, updated)
    assert (
        read_prepared_registration(path, first["key"], first["source_sha256"])
        == updated
    )
    assert read_prepared_registration(path, "0" * 64, first["source_sha256"]) is None
    with sqlite3.connect(path) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM prepared_binary_resources"
            ).fetchone()[0]
            == 2
        )


def test_workspace_backed_up_before_first_registry_table(tmp_path, monkeypatch):
    path = tmp_path / "projects.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE projects(id TEXT,payload TEXT)")

    def backup(database, **kwargs):
        assert database == path
        assert kwargs["prepared_directory"] == tmp_path / "prepared"
        assert kwargs["reason"] == "prepared-binary-resources-v1"
        assert not kwargs["connection"].in_transaction
        assert (
            kwargs["connection"]
            .execute(
                "SELECT name FROM sqlite_master WHERE name='prepared_binary_resources'"
            )
            .fetchone()
            is None
        )

    spy = Mock(side_effect=backup)
    monkeypatch.setattr(prepared_registry, "backup_workspace", spy)
    save_prepared_registration(
        path, registration(), prepared_directory=tmp_path / "prepared"
    )
    save_prepared_registration(path, registration(bytes=600))
    spy.assert_called_once()


def test_failed_backup_does_not_create_table_or_registration(tmp_path, monkeypatch):
    path = tmp_path / "projects.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE projects(id TEXT,payload TEXT)")
    monkeypatch.setattr(
        prepared_registry, "backup_workspace", Mock(side_effect=OSError("disk full"))
    )
    with pytest.raises(OSError, match="disk full"):
        save_prepared_registration(path, registration())
    assert read_prepared_registration(path, "a" * 64, "b" * 64) is None


def test_catalog_symlink_rejected_for_read_and_write(tmp_path):
    path = tmp_path / "catalog.sqlite3"
    save_prepared_registration(path, registration())
    link = tmp_path / "link.sqlite3"
    link.symlink_to(path)
    with pytest.raises(ValueError):
        read_prepared_registration(link, "a" * 64, "b" * 64)
    with pytest.raises(ValueError):
        save_prepared_registration(link, registration())
