"""Windows GUI ticket проходит те же native file/reparse проверки, что Core вход."""

import os
from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_windows_job_files import _FakeApi

from app.composition import create_runtime
from app.desktop import tickets
from app.desktop.handoff import LocalHandoff
from app.native_query.transport import job_files
from app.native_query.transport import windows_file_api as winapi
from app.native_query.transport import windows_job_files as windows_files


@pytest.fixture
def windows_paths(monkeypatch: pytest.MonkeyPatch) -> Iterator[_FakeApi]:
    api = _FakeApi()
    monkeypatch.setattr(tickets, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(job_files, "_windows_platform", lambda: True)
    monkeypatch.setattr(windows_files, "_file_api", lambda: api)
    yield api
    assert not api.handles


def test_windows_handoff_initializes_from_native_validated_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, windows_paths: _FakeApi
) -> None:
    root = tmp_path.resolve() / "Transfers"
    root.mkdir()
    monkeypatch.setenv("GREEN_ATLAS_CAD_ROOTS_JSON", "{}")
    monkeypatch.setenv("GREEN_ATLAS_CAD_INTAKE_PATH", str(tmp_path / "intake"))
    runtime = create_runtime(tmp_path / "workspace.sqlite3")
    try:
        service = LocalHandoff(runtime, root)
        try:
            assert service.ticket_root == root
            assert service.list() == []
        finally:
            service.close()
    finally:
        runtime.close()
    assert any(
        path == root and options["directory"] for path, options in windows_paths.opened
    )
    assert not windows_paths.handles
    with pytest.raises(ValueError, match="canonical"):
        job_files.package_file(root, ".")


@pytest.mark.parametrize("where", ["root", "ancestor"])
def test_windows_ticket_root_keeps_native_reparse_rejection(
    tmp_path: Path, windows_paths: _FakeApi, where: str
) -> None:
    root = tmp_path.resolve() / "Transfers"
    root.mkdir()
    bad = root if where == "root" else root.parent
    windows_paths.overrides[bad] = {
        "attributes": winapi.FILE_ATTRIBUTE_DIRECTORY
        | winapi.FILE_ATTRIBUTE_REPARSE_POINT
    }
    with pytest.raises(ValueError, match="reparse"):
        tickets.direct_path(root, root)
    assert not windows_paths.handles


def test_windows_ticket_root_is_not_created_by_validation(
    tmp_path: Path, windows_paths: _FakeApi
) -> None:
    root = tmp_path.resolve() / "missing-transfers"
    with pytest.raises(FileNotFoundError):
        tickets.direct_path(root, root)
    assert not root.exists()
    assert not windows_paths.handles


@pytest.mark.skipif(
    os.name != "nt", reason="Требуется настоящий Windows directory HANDLE"
)
def test_real_windows_ticket_root_directory_and_missing_root(tmp_path: Path) -> None:
    root = tmp_path / "Transfers"
    root.mkdir()
    assert tickets.direct_path(root, root) == root
    missing = root / "missing"
    with pytest.raises(FileNotFoundError):
        tickets.direct_path(missing, missing)
    assert not missing.exists()
    with pytest.raises(ValueError, match="canonical"):
        job_files.package_file(root, ".")


@pytest.mark.skipif(
    os.name != "nt", reason="Требуется настоящий Windows directory HANDLE"
)
def test_real_windows_destination_checks_existing_prefix(tmp_path: Path) -> None:
    root = tmp_path / "Archives"
    root.mkdir()
    destination = root / "project" / "capture"
    assert tickets.direct_path(destination, root) == destination
    assert not destination.exists()

    (root / "project").mkdir()
    assert tickets.direct_path(destination, root) == destination
    destination.mkdir()
    assert tickets.direct_path(destination, root) == destination
    (destination / "session.json").write_text("{}")
    assert (
        tickets.direct_path(destination / "session.json", root)
        == destination / "session.json"
    )


@pytest.mark.skipif(os.name != "nt", reason="Требуется настоящая Windows junction")
def test_real_windows_destination_rejects_reparse_prefix(tmp_path: Path) -> None:
    from _winapi import CreateJunction

    root = tmp_path / "Archives"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    alias = root / "project"
    CreateJunction(str(outside), str(alias))
    try:
        with pytest.raises(ValueError, match="reparse"):
            tickets.direct_path(alias / "capture", root)
    finally:
        alias.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="Требуется настоящая Windows junction")
@pytest.mark.parametrize("where", ["root", "ancestor"])
def test_real_windows_ticket_root_rejects_junction(tmp_path: Path, where: str) -> None:
    from _winapi import CreateJunction

    source, alias = tmp_path / "real", tmp_path / "junction"
    (source / "nested").mkdir(parents=True)
    CreateJunction(str(source), str(alias))
    try:
        assert alias.is_junction()
        root = alias if where == "root" else alias / "nested"
        with pytest.raises(ValueError, match="reparse"):
            tickets.direct_path(root, root)
    finally:
        alias.rmdir()
    assert (source / "nested").is_dir()


def test_windows_ticket_uses_shared_native_path_and_handle_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path.resolve()
    path = root / "ticket" / "transfer.gatransfer"
    calls: list[object] = []

    def package_file(directory: Path, relative: str) -> Path:
        calls.append((directory, relative))
        return directory / relative

    def open_regular(selected: Path) -> BytesIO:
        calls.append(selected)
        return BytesIO(b"ticket-bytes")

    monkeypatch.setattr(tickets, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(job_files, "checked_path", package_file)
    monkeypatch.setattr(job_files, "open_regular", open_regular)
    assert tickets.direct_path(path, root) == path
    with tickets.read_regular(path) as stream:
        assert stream.read() == b"ticket-bytes"
    assert calls == [(root, "ticket/transfer.gatransfer"), path]


def test_windows_ticket_reparse_failure_is_not_retried_with_os_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject(_path: Path) -> BytesIO:
        raise ValueError("reparse point")

    monkeypatch.setattr(tickets, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(job_files, "open_regular", reject)
    with pytest.raises(ValueError, match="reparse"):
        tickets.read_regular(tmp_path / "capture.json")
