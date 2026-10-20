"""Сохранённый setup и сборочные входы проверяются без запуска EXE или оболочки."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]


def load_helper(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SETUP = load_helper("windows_setup_entry_test_helper", "tools/autocad-bridge/installer/windows/setup_entry.py")
BUNDLE = load_helper("windows_bundle_test_helper", "scripts/desktop/windows_bundle.py")


def test_saved_setups_cleanup_skips_current_and_unknown_files(tmp_path, monkeypatch):
    root = tmp_path / "Installer"
    external = tmp_path / "setup.exe"
    external.write_bytes(b"first-owned-setup")
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(external), frozen=True))
    first = SETUP.keep_setup(root)
    first_sha = hashlib.sha256(first.read_bytes()).hexdigest()
    external.write_bytes(b"second-owned-setup")
    current = SETUP.keep_setup(root)
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(current), frozen=True))
    foreign = root / "Versions/foreign"
    foreign.mkdir()
    sentinel = foreign / "user.dwg"
    sentinel.write_bytes(b"do-not-touch")
    assert SETUP.purge_setup_versions(root) == (first_sha,)
    assert current.read_bytes() == b"second-owned-setup"
    assert sentinel.read_bytes() == b"do-not-touch"
    assert SETUP.keep_setup(root) == current


def test_modified_saved_setup_is_not_deleted(tmp_path, monkeypatch):
    external = tmp_path / "setup.exe"
    external.write_bytes(b"owned-setup")
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(external), frozen=True))
    saved = SETUP.keep_setup(tmp_path / "Installer")
    saved.write_bytes(b"modified")
    with pytest.raises(ValueError, match="изменён"):
        SETUP.purge_setup_versions(tmp_path / "Installer")
    assert saved.read_bytes() == b"modified"


@pytest.mark.parametrize("mode", ["root-link", "nested-link", "userdata", "developer"])
def test_bundle_copy_refuses_nonportable_or_user_inputs(tmp_path, mode):
    source = tmp_path / "source"
    source.mkdir()
    (source / "own.js").write_text("own")
    if mode == "root-link":
        link = tmp_path / "source-link"
        if os.name == "nt":
            from _winapi import CreateJunction

            CreateJunction(str(source), str(link))
        else:
            link.symlink_to(source, target_is_directory=True)
        source = link
    elif mode == "nested-link":
        try:
            (source / "linked.js").symlink_to(source / "own.js")
        except OSError as error:
            if os.name == "nt" and error.winerror == 1314:
                pytest.skip("Windows account cannot create file symlinks")
            raise
    elif mode == "userdata":
        (source / "user.dwg").write_bytes(b"CAD")
    else:
        (source / "node_modules").mkdir()
    with pytest.raises(ValueError):
        BUNDLE.copy_tree(source, tmp_path / "destination")
    assert not (tmp_path / "destination").exists()
    assert (tmp_path / "source/own.js").read_text() == "own"


def test_saved_setup_marker_is_exact_and_cannot_redirect(tmp_path, monkeypatch):
    external = tmp_path / "setup.exe"
    external.write_bytes(b"own")
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(external), frozen=True))
    saved = SETUP.keep_setup(tmp_path / "Installer")
    marker = saved.with_name("setup-version.json")
    value = json.loads(marker.read_text())
    value["file"] = "../../foreign.exe"
    marker.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Маркер"):
        SETUP.keep_setup(tmp_path / "Installer")
    assert saved.read_bytes() == b"own"


def test_setup_copy_is_streamed_and_only_published_after_complete_hash(tmp_path, monkeypatch):
    external = tmp_path / "setup.exe"
    content = b"large-onefile-payload" * 100000
    external.write_bytes(content)
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(external), frozen=True))
    with monkeypatch.context() as context:
        context.setattr(Path, "read_bytes", lambda path: pytest.fail("EXE должен читаться блоками"))
        saved = SETUP.keep_setup(tmp_path / "Installer")
    assert saved.read_bytes() == content
    assert saved.parent.name == hashlib.sha256(content).hexdigest()
    assert not list(saved.parent.glob("*.staging-*"))


def test_failed_setup_copy_does_not_publish_partial_exe_or_marker(tmp_path, monkeypatch):
    external = tmp_path / "setup.exe"
    external.write_bytes(b"installer")
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(external), frozen=True))
    monkeypatch.setattr(SETUP.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        SETUP.keep_setup(tmp_path / "Installer")
    assert not list((tmp_path / "Installer/Versions").rglob("*.exe"))
    assert not list((tmp_path / "Installer/Versions").rglob("*.staging-*"))
    monkeypatch.undo()
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(external), frozen=True))
    saved = SETUP.keep_setup(tmp_path / "Installer")
    assert saved.read_bytes() == b"installer"


def test_failed_setup_marker_write_can_be_retried_without_damaged_exe(tmp_path, monkeypatch):
    external = tmp_path / "setup.exe"
    external.write_bytes(b"installer")
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(external), frozen=True))
    calls = []

    def flush(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError("marker disk full")

    with monkeypatch.context() as context:
        context.setattr(SETUP.os, "fsync", flush)
        with pytest.raises(OSError, match="marker disk full"):
            SETUP.keep_setup(tmp_path / "Installer")
    executables = list((tmp_path / "Installer/Versions").rglob("*.exe"))
    assert len(executables) == 1 and executables[0].read_bytes() == b"installer"
    assert not list((tmp_path / "Installer/Versions").rglob("*.staging-*"))
    saved = SETUP.keep_setup(tmp_path / "Installer")
    assert saved == executables[0]
    assert saved.with_name("setup-version.json").is_file()


@pytest.mark.parametrize("filename", ["GreenAtlasSetup.exe", "setup-version.json"])
def test_saved_setup_preserves_dangling_final_entry(tmp_path, monkeypatch, filename):
    executable = tmp_path / "setup.exe"
    executable.write_bytes(b"installer")
    monkeypatch.setattr(SETUP, "sys", SimpleNamespace(executable=str(executable), frozen=True))
    identity = hashlib.sha256(b"installer").hexdigest()
    directory = tmp_path / "Installer/Versions" / identity
    directory.mkdir(parents=True)
    final = directory / filename
    foreign = tmp_path / "foreign-missing"
    try:
        final.symlink_to(foreign)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows account cannot create dangling symlinks")
        raise
    original_target = final.readlink()
    with pytest.raises(ValueError):
        SETUP.keep_setup(tmp_path / "Installer")
    assert final.is_symlink()
    assert final.readlink() == original_target
    assert not foreign.exists()
    assert not list(directory.glob("*.staging-*"))


def test_exclusive_publication_preserves_target_created_by_another_writer(tmp_path):
    temporary, target = tmp_path / "staging", tmp_path / "final"
    temporary.write_bytes(b"own-complete-file")
    target.write_bytes(b"foreign-existing-file")
    with pytest.raises(FileExistsError):
        SETUP.publish_new_file(temporary, target)
    assert target.read_bytes() == b"foreign-existing-file"
    assert temporary.read_bytes() == b"own-complete-file"
