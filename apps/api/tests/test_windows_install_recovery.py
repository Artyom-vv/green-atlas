"""Update crashes use real child process termination, never user installations."""

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import test_windows_distribution

from app.distribution.preflight import manifest_identity
from app.distribution.windows_install import BundleInstaller, load_owned_bundle
from app.distribution.windows_install_journal import JOURNAL_NAME, read_journal

factory = test_windows_distribution.factory


def identity(path):
    return manifest_identity(load_owned_bundle(path))


@pytest.mark.parametrize("boundary", ["partial-copy", "old-moved", "new-published", "shell-published", "committed", "partial-cleanup"])
def test_process_crash_recovers_before_commit_and_retries_cleanup_after_commit(factory, tmp_path, boundary):
    first, _, _ = factory("old")
    second, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)
    shell = tmp_path / "fake-shell.txt"
    shell.write_text("old")
    script = r'''
import os,sys
from pathlib import Path
from app.distribution.windows_install import BundleInstaller
import app.distribution.windows_install_journal as journal
root,source,shell=map(Path,sys.argv[1:4]);boundary=sys.argv[4]
replace=os.replace;unlink=Path.unlink;write=journal.write_journal;fsync=os.fsync;calls=0
def replaced(a,b):
 replace(a,b)
 if boundary=='old-moved' and Path(b).name.startswith('GreenAtlas.backup-'):os._exit(73)
 if boundary=='new-published' and Path(b).name=='GreenAtlas.bundle':os._exit(73)
def written(root,value):
 write(root,value)
 if boundary=='committed' and value['phase']=='committed':os._exit(73)
def unlinked(path,*args,**kwargs):
 unlink(path,*args,**kwargs)
 if boundary=='partial-cleanup' and any(p.name.startswith('GreenAtlas.backup-') for p in path.parents):os._exit(73)
def synced(fd):
 global calls
 fsync(fd);calls+=1
 if boundary=='partial-copy' and calls==(3 if os.name=='nt' else 5):os._exit(73)
def publish(*args):
 shell.write_text('new')
 if boundary=='shell-published':os._exit(73)
os.replace=replaced;Path.unlink=unlinked;journal.write_journal=written;os.fsync=synced
BundleInstaller(root).install(source,publish_shell=publish)
raise SystemExit('crash boundary was not reached')
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    process = subprocess.run([sys.executable, "-c", script, str(engine.plugins), str(second), str(shell), boundary], env=environment, capture_output=True, text=True, timeout=30)
    assert process.returncode == 73, process.stdout + process.stderr
    committed = boundary in ("committed", "partial-cleanup")
    # This is a newly constructed engine after a real process exit. No Python
    # finally/except ran in the terminated installer.
    restarted = BundleInstaller(engine.plugins)
    initial_names = {path.name for path in engine.plugins.iterdir()}
    with pytest.raises(RuntimeError, match="busy"):
        restarted.recover(before_publish=lambda: (_ for _ in ()).throw(RuntimeError("busy runtime")))
    assert {path.name for path in engine.plugins.iterdir()} == initial_names
    assert restarted.recover(publish_shell=lambda *_: shell.write_text("old"))
    assert identity(restarted.installed) == identity(second if committed else first)
    assert not (engine.plugins / JOURNAL_NAME).exists()
    assert not list(engine.plugins.glob("GreenAtlas.staging-*"))
    assert not list(engine.plugins.glob("GreenAtlas.backup-*"))
    assert shell.read_text() == ("new" if committed else "old")
    assert restarted.recover() is False


def test_cleanup_failure_keeps_verified_new_install_and_resumes_partial_deletion(factory, tmp_path, monkeypatch):
    first, _, _ = factory("old")
    second, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)
    original = Path.unlink
    count = 0

    def fail_after_one(path, *args, **kwargs):
        nonlocal count
        if any(parent.name.startswith("GreenAtlas.backup-") for parent in path.parents):
            count += 1
            if count == 2:
                raise PermissionError("synthetic Windows locked old file")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", fail_after_one)
        receipt = engine.install(second)
    assert receipt.cleanup_pending and identity(engine.installed) == identity(second)
    assert read_journal(engine.plugins)["phase"] == "committed"
    assert engine.recover()
    assert not list(engine.plugins.glob("GreenAtlas.backup-*"))
    assert identity(engine.installed) == identity(second)


def test_failed_final_validation_quarantines_our_invalid_candidate_and_restores_old(factory, tmp_path, monkeypatch):
    import app.distribution.windows_install as target

    first, _, _ = factory("old")
    second, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)
    replace = target.os.replace

    def corrupt_after_publication(source, destination):
        replace(source, destination)
        if Path(destination) == engine.installed:
            (engine.installed / "Contents/Windows/GreenAtlasDesktop.exe").write_bytes(b"invalid candidate")

    with monkeypatch.context() as patch:
        patch.setattr(target.os, "replace", corrupt_after_publication)
        # The recovery rename must not re-corrupt the restored old program.
        def corrupt_once(source, destination):
            if Path(source).name.startswith("GreenAtlas.staging-"):
                corrupt_after_publication(source, destination)
            else:
                replace(source, destination)
        patch.setattr(target.os, "replace", corrupt_once)
        with pytest.raises(ValueError, match="состав"):
            engine.install(second)
    assert identity(engine.installed) == identity(first)
    assert not list(engine.plugins.glob("GreenAtlas.failed-*"))
    assert not (engine.plugins / JOURNAL_NAME).exists()


def test_failed_candidate_unknown_manual_file_is_preserved_with_old_restored(factory, tmp_path):
    first, _, _ = factory("old")
    second, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)

    def fail(installed, version):
        (installed / "manual-source.dwg").write_bytes(b"unique human CAD")
        raise OSError("shell failed")

    with pytest.raises(ValueError, match="неизвестные файлы"):
        engine.install(second, publish_shell=fail)
    assert identity(engine.installed) == identity(first)
    failed = list(engine.plugins.glob("GreenAtlas.failed-*"))
    assert len(failed) == 1
    assert (failed[0] / "manual-source.dwg").read_bytes() == b"unique human CAD"
    assert (engine.plugins / JOURNAL_NAME).exists()
    # Moving the human file out explicitly allows recovery to finish; the
    # installer never treats that file as a disposable program component.
    (failed[0] / "manual-source.dwg").rename(tmp_path / "manual-source.dwg")
    assert engine.recover()
    assert not failed[0].exists()


def test_current_corruption_after_commit_is_error_not_cleanup_success_and_old_is_kept(factory, tmp_path, monkeypatch):
    import app.distribution.windows_install_journal as journal

    first, _, _ = factory("old")
    second, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)
    write = journal.write_journal

    def damage_committed(root, value):
        write(root, value)
        if value["phase"] == "committed":
            (engine.installed / "Contents/Windows/GreenAtlasDesktop.exe").write_bytes(b"post-commit corruption")

    with monkeypatch.context() as patch:
        patch.setattr(journal, "write_journal", damage_committed)
        with pytest.raises(ValueError, match="состав"):
            engine.install(second)
    assert read_journal(engine.plugins)["phase"] == "committed"
    backups = list(engine.plugins.glob("GreenAtlas.backup-*"))
    assert len(backups) == 1 and identity(backups[0]) == identity(first)


def test_success_removes_all_sealed_program_copies_preserves_profile_and_manual_files(factory, tmp_path):
    first, _, _ = factory("old")
    second, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)
    for i, prefix in enumerate(("backup", "failed", "uninstalled")):
        shutil.copytree(first, engine.plugins / ("GreenAtlas." + prefix + "-" + str(i) * 32))
    profile = tmp_path / "GreenAtlas"
    sentinels = []
    for name in ("Workspace/projects.sqlite3", "Workspace/projects.sqlite3.backups/migration.db", "Transfers/source.dwg", "manual-archives/plan.json"):
        path = profile / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("human:" + name).encode())
        sentinels.append((path, path.read_bytes()))
    unrelated = engine.plugins / "OtherVendor.bundle"
    unrelated.mkdir()
    (unrelated / "user.dwg").write_bytes(b"unrelated")
    receipt = engine.install(second)
    assert not receipt.cleanup_pending and len(receipt.removed_program_copies) == 4
    assert {p.name for p in engine.plugins.iterdir()} == {"GreenAtlas.bundle", "OtherVendor.bundle"}
    assert all(path.read_bytes() == expected for path, expected in sentinels)
    assert (unrelated / "user.dwg").read_bytes() == b"unrelated"


def test_unknown_retained_files_are_not_deleted_and_do_not_undo_commit(factory, tmp_path):
    first, _, _ = factory("old")
    second, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(first)
    foreign = engine.plugins / ("GreenAtlas.backup-" + "e" * 32)
    shutil.copytree(first, foreign)
    (foreign / "user.dwg").write_bytes(b"keep")
    receipt = engine.install(second)
    assert receipt.cleanup_pending and identity(engine.installed) == identity(second)
    assert (foreign / "user.dwg").read_bytes() == b"keep"
    assert read_journal(engine.plugins)["phase"] == "committed"
    with pytest.raises(ValueError, match="Очистка"):
        engine.install(first)
    assert identity(engine.installed) == identity(second)


def test_symlink_retained_directory_and_journal_traversal_never_touch_target(factory, tmp_path):
    package, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    foreign = tmp_path / "Workspace"
    foreign.mkdir()
    note = foreign / "source.dwg"
    note.write_bytes(b"keep")
    engine.plugins.mkdir()
    link = engine.plugins / ("GreenAtlas.backup-" + "f" * 32)
    try:
        link.symlink_to(foreign, target_is_directory=True)
    except OSError as error:
        if os.name == "nt" and error.winerror == 1314:
            pytest.skip("Windows account cannot create symlinks")
        raise
    receipt = engine.install(package)
    assert receipt.cleanup_pending and note.read_bytes() == b"keep"
    journal_path = engine.plugins / JOURNAL_NAME
    value = json.loads(journal_path.read_text())
    value["backup"] = "../Workspace"
    journal_path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="журнал"):
        engine.recover()
    assert note.read_bytes() == b"keep" and identity(engine.installed) == identity(package)


def test_legacy_explicit_rollback_uses_same_transaction_then_keeps_no_old_payload(factory, tmp_path):
    first, _, _ = factory("old")
    second, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(second)
    legacy = engine.plugins / ("GreenAtlas.backup-" + "c" * 32)
    shutil.copytree(first, legacy)
    receipt = engine.rollback(legacy)
    assert receipt.action == "rollback" and receipt.backup is None
    assert identity(engine.installed) == identity(first)
    assert not list(engine.plugins.glob("GreenAtlas.backup-*"))


@pytest.mark.parametrize("corruption", [None, "foreign", ["not-an-entry"], [{"name": "../Workspace"}]])
def test_malformed_cleanup_journal_refuses_recovery_without_mutating_current(factory, tmp_path, corruption):
    package, _, _ = factory("new")
    engine = BundleInstaller(tmp_path / "plugins")
    engine.install(package)
    value = {"schema": "green-atlas.windows-install/1", "phase": "committed",
             "stage": "GreenAtlas.staging-" + "1" * 32,
             "backup": "GreenAtlas.backup-" + "2" * 32,
             "failed": "GreenAtlas.failed-" + "3" * 32,
             "old_kind": "absent", "old_identity": None,
             "new_identity": identity(package),
             "manifest": load_owned_bundle(package).model_dump(mode="json", by_alias=True),
             "cleanup": corruption}
    (engine.plugins / JOURNAL_NAME).write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Журнал|журнал"):
        engine.recover()
    assert identity(engine.installed) == identity(package)


def load_setup_module():
    path = Path(__file__).resolve().parents[3] / "tools/autocad-bridge/installer/windows/setup_entry.py"
    spec = importlib.util.spec_from_file_location("setup_cleanup_test", path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_setup_version_cleanup_keeps_current_maintenance_and_unknown_files(tmp_path, monkeypatch):
    setup = load_setup_module()
    root = tmp_path / "Installer"
    versions = root / "Versions"
    binaries = []
    for payload in (b"old", b"saved-current", b"running", b"unknown"):
        digest = hashlib.sha256(payload).hexdigest()
        directory = versions / digest
        directory.mkdir(parents=True)
        executable = directory / "GreenAtlasSetup.exe"
        executable.write_bytes(payload)
        (directory / "setup-version.json").write_text(json.dumps({"schema": "green-atlas.saved-setup/1", "file": executable.name, "sha256": digest}))
        binaries.append(executable)
    (binaries[3].parent / "manual.dwg").write_bytes(b"keep")
    monkeypatch.setattr(setup.sys, "executable", str(binaries[2]))
    assert len(setup.purge_setup_versions(root, keep=(binaries[1],))) == 1
    assert not binaries[0].exists()
    assert all(path.exists() for path in binaries[1:])


def test_locked_old_setup_does_not_prevent_other_verified_old_versions_cleanup(tmp_path, monkeypatch):
    setup = load_setup_module()
    root = tmp_path / "Installer"
    source = tmp_path / "setup.exe"
    saved = []
    monkeypatch.setattr(setup.sys, "executable", str(source))
    monkeypatch.setattr(setup.sys, "frozen", True, raising=False)
    for content in (b"locked-old", b"other-old", b"maintenance"):
        source.write_bytes(content)
        saved.append(setup.keep_setup(root))
    original = Path.unlink

    def locked(path, *args, **kwargs):
        if path == saved[0]:
            raise PermissionError("synthetic locked EXE")
        return original(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", locked)
        with pytest.raises(ValueError, match="locked EXE"):
            setup.purge_setup_versions(root, keep=(saved[2],))
    assert saved[0].exists() and saved[2].exists() and not saved[1].exists()
    assert len(setup.purge_setup_versions(root, keep=(saved[2],))) == 1
