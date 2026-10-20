"""Ожидаемые Windows-блокировки и UTF-8 проверяются без установленных программ."""

import io
import json
import os
import queue
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
import test_windows_distribution
from test_windows_setup_payload import load_helper

from app.distribution import windows_install as target
from app.distribution import windows_shell
from app.distribution.preflight import manifest_identity

factory = test_windows_distribution.factory
ROOT = Path(__file__).resolve().parents[3]


def denied(code):
    error = PermissionError("private-user-path")
    if code is not None:
        error.winerror = code
    return error


def directories(tmp_path):
    source = tmp_path / "Частный пользователь/GreenAtlas.bundle"
    source.mkdir(parents=True)
    (source / "program.bin").write_bytes(b"verified-program")
    destination = source.with_name("GreenAtlas.backup-" + "a" * 32)
    return source, destination


@pytest.mark.parametrize("code", [5, 32, 33])
def test_transient_bundle_access_failures_retry_only_the_same_atomic_rename(
    tmp_path, monkeypatch, code
):
    source, destination = directories(tmp_path)
    calls, pauses = [], []
    replace = target.os.replace

    def transient(origin, final):
        calls.append((origin, final))
        if len(calls) < 3:
            raise denied(code)
        return replace(origin, final)

    monkeypatch.setattr(target.os, "replace", transient)
    monkeypatch.setattr(target, "sleep", pauses.append)
    target._replace_bundle_directory(source, destination)
    assert calls == [(source, destination)] * 3
    assert pauses == [0.1, 0.2]
    assert not source.exists()
    assert (destination / "program.bin").read_bytes() == b"verified-program"


@pytest.mark.parametrize("code", [5, 32, 33])
def test_persistent_bundle_access_failure_is_bounded_and_has_no_private_path(
    tmp_path, monkeypatch, code
):
    source, destination = directories(tmp_path)
    calls, pauses = [], []

    def blocked(origin, final):
        calls.append((origin, final))
        raise denied(code)

    monkeypatch.setattr(target.os, "replace", blocked)
    monkeypatch.setattr(target, "sleep", pauses.append)
    with pytest.raises(target.BundleAccessError) as error:
        target._replace_bundle_directory(source, destination)
    assert len(calls) == 5 and sum(pauses) == pytest.approx(1.5)
    assert error.value.winerror == code
    assert f"Win32 {code}" in str(error.value)
    assert "не завершены" in str(error.value)
    assert "private-user-path" not in str(error.value)
    assert "Частный пользователь" not in str(error.value)
    assert str(source) not in str(error.value)
    assert (source / "program.bin").read_bytes() == b"verified-program"
    assert not destination.exists()


@pytest.mark.parametrize("code", [None, 18, 87, 112])
def test_unrelated_os_errors_are_not_retried_or_relabelled(tmp_path, monkeypatch, code):
    source, destination = directories(tmp_path)
    error = denied(code)
    calls = []

    def fail(*args):
        calls.append(args)
        raise error

    monkeypatch.setattr(target.os, "replace", fail)
    monkeypatch.setattr(
        target, "sleep", lambda *_a: pytest.fail("Unexpected error must not retry")
    )
    with pytest.raises(PermissionError) as caught:
        target._replace_bundle_directory(source, destination)
    assert caught.value is error and len(calls) == 1


@pytest.mark.parametrize("changed", ["source", "destination"])
def test_retry_never_moves_a_substituted_directory_or_overwrites_a_new_destination(
    tmp_path, monkeypatch, changed
):
    source, destination = directories(tmp_path)
    replace = target.os.replace
    calls = []

    def race(origin, final):
        calls.append((origin, final))
        if changed == "source":
            replace(source, source.with_name("preserved-original"))
            source.mkdir()
            (source / "manual.dwg").write_bytes(b"foreign-drawing")
        else:
            destination.mkdir()
            (destination / "manual.dwg").write_bytes(b"foreign-drawing")
        raise denied(32)

    monkeypatch.setattr(target.os, "replace", race)
    monkeypatch.setattr(target, "sleep", lambda *_a: None)
    with pytest.raises(ValueError, match="изменились"):
        target._replace_bundle_directory(source, destination)
    assert len(calls) == 1
    foreign = source if changed == "source" else destination
    assert (foreign / "manual.dwg").read_bytes() == b"foreign-drawing"
    original = source.with_name("preserved-original") if changed == "source" else source
    assert (original / "program.bin").read_bytes() == b"verified-program"


@pytest.mark.parametrize("phase", ["old-to-backup", "stage-to-installed"])
def test_locked_update_restores_old_bundle_and_can_retry_after_unlock(
    factory, tmp_path, monkeypatch, phase
):
    first, _, _ = factory("first")
    second, _, _ = factory("second")
    engine = target.BundleInstaller(tmp_path / "plugins")
    old = engine.install(first).package_sha256
    expected = manifest_identity(target.load_owned_bundle(second))
    workspace = tmp_path / "GreenAtlas/Workspace/projects.sqlite3"
    workspace.parent.mkdir(parents=True)
    workspace.write_bytes(b"working-project-no-read-no-write")
    replace = target.os.replace
    blocked_calls = []

    def blocked(source, destination):
        if (
            phase == "old-to-backup"
            and source == engine.installed
            and destination.name.startswith("GreenAtlas.backup-")
        ) or (
            phase == "stage-to-installed"
            and source.name.startswith("GreenAtlas.staging-")
            and destination == engine.installed
        ):
            blocked_calls.append((source, destination))
            raise denied(5)
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(target.os, "replace", blocked)
        patch.setattr(target, "sleep", lambda *_a: None)
        with pytest.raises(target.BundleAccessError):
            engine.install(second)
    assert len(blocked_calls) == 5
    assert manifest_identity(target.load_owned_bundle(engine.installed)) == old
    assert not list(engine.plugins.glob("GreenAtlas.staging-*"))
    assert not list(engine.plugins.glob("GreenAtlas.backup-*"))
    assert not list(engine.plugins.glob("GreenAtlas.failed-*"))
    assert not (engine.plugins / ".green-atlas-install.json").exists()
    assert workspace.read_bytes() == b"working-project-no-read-no-write"
    updated = engine.install(second)
    assert updated.package_sha256 == expected
    assert manifest_identity(target.load_owned_bundle(engine.installed)) == expected
    assert not list(engine.plugins.glob("GreenAtlas.backup-*"))
    assert workspace.read_bytes() == b"working-project-no-read-no-write"


def test_locked_uninstall_preserves_registration_and_bundle_until_success(
    factory, tmp_path, monkeypatch
):
    package, _, _ = factory("uninstall")
    engine = target.BundleInstaller(tmp_path / "plugins")
    expected = engine.install(package).package_sha256
    callbacks = []
    replace = target.os.replace

    def blocked(source, destination):
        if source == engine.installed:
            raise denied(32)
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(target.os, "replace", blocked)
        patch.setattr(target, "sleep", lambda *_a: None)
        with pytest.raises(target.BundleAccessError):
            engine.uninstall(remove_shell=lambda: callbacks.append("remove"))
    assert callbacks == []
    assert manifest_identity(target.load_owned_bundle(engine.installed)) == expected
    assert not list(engine.plugins.glob("GreenAtlas.uninstalled-*"))
    receipt = engine.uninstall(remove_shell=lambda: callbacks.append("remove"))
    assert callbacks == ["remove"]
    assert manifest_identity(target.load_owned_bundle(Path(receipt.backup))) == expected
    assert len(list(engine.plugins.glob("GreenAtlas.uninstalled-*"))) == 1


@pytest.mark.parametrize("encoding", ["cp1251", "cp866", "ascii"])
@pytest.mark.parametrize("error", [False, True])
def test_diagnostics_emit_utf8_bytes_even_when_python_text_stream_uses_another_encoding(
    monkeypatch, encoding, error
):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding=encoding)
    monkeypatch.setattr(
        windows_shell, "sys", SimpleNamespace(stdout=stream, stderr=stream)
    )
    text = "GA_ERROR:Win32 5, файл «Чертёж» в C:\\Users\\Тест\\GreenAtlas.bundle\n"
    assert windows_shell.write_diagnostic(text, error=error)
    assert raw.getvalue() == text.encode("utf-8")
    assert raw.getvalue().decode("utf-8") == text
    stream.detach()


def test_diagnostic_partial_binary_writes_keep_utf8_complete(monkeypatch):
    chunks = []

    class Binary:
        def write(self, data):
            chunks.append(data[:3])
            return len(chunks[-1])

        def flush(self):
            pass

    monkeypatch.setattr(
        windows_shell, "sys", SimpleNamespace(stdout=SimpleNamespace(buffer=Binary()))
    )
    text = "GA_ERROR:проверка кириллицы\n"
    assert windows_shell.write_diagnostic(text)
    assert b"".join(chunks).decode("utf-8") == text


@pytest.mark.parametrize("code", [5, 32, 33])
def test_setup_public_error_keeps_win32_code_without_private_os_path(monkeypatch, code):
    setup = load_helper(
        "setup_access_error_test",
        "tools/autocad-bridge/installer/windows/setup_entry.py",
    )
    error = denied(code)
    diagnostics = []
    monkeypatch.setattr(sys, "argv", ["Setup.exe", "--quiet", "--progress"])
    monkeypatch.setattr(
        setup, "require_windows_user", lambda: (_ for _ in ()).throw(error)
    )
    monkeypatch.setattr(
        setup, "write_diagnostic", lambda text, **_kw: diagnostics.append(text) or True
    )
    assert setup.main() == 1
    assert diagnostics[0].startswith("GA_ERROR:") and f"Win32 {code}" in diagnostics[0]
    assert "private-user-path" not in "".join(diagnostics)
    assert json.loads(diagnostics[-1])["ok"] is False


def test_wizard_expected_backend_failures_use_existing_failed_finish_contract():
    source = (ROOT / "tools/autocad-bridge/installer/windows/wizard.iss").read_text()
    install = source.split("procedure CurStepChanged", 1)[1].split(
        "procedure CancelButtonClick", 1
    )[0]
    uninstall = source.split("procedure CurUninstallStepChanged", 1)[1]
    assert "RaiseException(BackendError)" not in install + uninstall
    assert "SuppressibleMsgBox(BackendError" in install and "Exit;" in install
    assert "Abort;" not in install
    assert uninstall.count("Abort;") == 2
    assert "if BackendSucceeded then Result := 0 else Result := 1" in source
    assert "if (CurPageID = wpFinished) and not BackendSucceeded" in source
    assert "WizardForm.RunList.Visible := False" in source
    assert "Check: InstallationSucceeded" in source


def stop_cwd_child(child):
    """Завершить и дождаться только созданного этой фикстурой процесса."""
    try:
        if child.poll() is None:
            try:
                child.communicate(input=b"exit\n", timeout=5)
            except subprocess.TimeoutExpired:
                child.terminate()
                child.communicate(timeout=5)
    finally:
        try:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=5)
        finally:
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()


@contextmanager
def cwd_child(directory):
    """Подтверждать запуск отдельного Python с CWD внутри проверяемого bundle."""
    environment = os.environ.copy()
    environment["PYTHONUNBUFFERED"] = "1"
    child = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-u",
            "-c",
            "import sys; sys.stdout.buffer.write(b'GA_CWD_READY\\n'); "
            "sys.stdout.buffer.flush(); "
            "sys.exit(0 if sys.stdin.buffer.readline() == b'exit\\n' else 3)",
        ],
        cwd=directory,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    ready = queue.Queue(maxsize=1)
    reader = Thread(target=lambda: ready.put(child.stdout.readline()), daemon=True)
    try:
        reader.start()
        try:
            greeting = ready.get(timeout=10)
        except queue.Empty:
            # Закрываем собственный процесс до второго читателя stdout в communicate.
            child.terminate()
            child.wait(timeout=5)
            pytest.fail("Дочерний процесс не подтвердил готовность CWD за 10 секунд")
        reader.join(timeout=5)
        assert not reader.is_alive()
        assert greeting == b"GA_CWD_READY\n"
        assert child.poll() is None
        yield child
    finally:
        stop_cwd_child(child)
        reader.join(timeout=5)
        assert not reader.is_alive()


def test_cwd_child_handshake_and_cleanup_preserve_the_directory_on_failure(tmp_path):
    source, destination = directories(tmp_path)
    child = None
    with pytest.raises(RuntimeError, match="fixture body failed"):
        with cwd_child(source) as child:
            assert child.poll() is None
            raise RuntimeError("fixture body failed")
    assert child is not None and child.returncode == 0
    assert child.stdin.closed and child.stdout.closed
    assert (source / "program.bin").read_bytes() == b"verified-program"
    assert not destination.exists()


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Нужен Windows-процесс с CWD внутри переименовываемого каталога",
)
@pytest.mark.parametrize("release_on_retry", [False, True])
def test_real_windows_process_cwd_blocks_switch_and_retry_is_safe(
    tmp_path, monkeypatch, release_on_retry
):
    source, destination = directories(tmp_path)
    pauses = []
    with cwd_child(source) as child:
        def wait(delay):
            pauses.append(delay)
            if release_on_retry:
                stop_cwd_child(child)

        monkeypatch.setattr(target, "sleep", wait)
        if release_on_retry:
            target._replace_bundle_directory(source, destination)
            assert pauses == [0.1]
            assert child.returncode == 0
            assert not source.exists()
            assert (destination / "program.bin").read_bytes() == b"verified-program"
        else:
            with pytest.raises(target.BundleAccessError) as error:
                target._replace_bundle_directory(source, destination)
            assert error.value.winerror in {5, 32, 33}
            assert pauses == [0.1, 0.2, 0.4, 0.8]
            assert child.poll() is None
            assert (source / "program.bin").read_bytes() == b"verified-program"
            assert not destination.exists()
    assert child.returncode == 0
    if not release_on_retry:
        target._replace_bundle_directory(source, destination)
        assert pauses == [0.1, 0.2, 0.4, 0.8]
        assert not source.exists()
        assert (destination / "program.bin").read_bytes() == b"verified-program"
