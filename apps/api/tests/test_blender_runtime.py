"""Идентичность внешнего Blender, пути задания и освобождение границы запуска."""

import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.rendering import blender_runtime as runtime


@pytest.fixture
def executable(tmp_path):
    if os.name == "nt":
        pytest.skip("Тестовый executable использует POSIX shebang")

    def create(body):
        path = tmp_path / "Чужая папка" / "fake-blender"
        path.parent.mkdir(exist_ok=True)
        path.write_text(f"#!{sys.executable}\n{body}\n", encoding="utf-8")
        path.chmod(0o700)
        return path

    return create


def test_discovery_only_reads_the_configured_installation(monkeypatch, executable):
    path = executable("raise RuntimeError('Discovery must not execute Blender')")
    monkeypatch.setenv("GREEN_ATLAS_BLENDER", str(path))
    result = runtime.discover_blender()
    assert result.executable == path.resolve()
    assert result.version is None
    with pytest.raises(FrozenInstanceError):
        result.version = (4, 5, 11)


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_job_environment_owns_windows_temp_and_keeps_mac_contract(
    tmp_path, monkeypatch, platform
):
    parent = {"TEMP": "system-temp", "TMP": "system-tmp", "MARKER": "retained"}
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name=platform, environ=parent))
    before = dict(parent)
    environment = runtime.blender_job_environment(tmp_path)
    assert parent == before
    assert environment["MARKER"] == "retained"
    assert environment["TMPDIR"] == str(tmp_path)
    assert environment["BLENDER_USER_CONFIG"] == str(tmp_path / "blender-config")
    assert environment["TEMP"] == (str(tmp_path) if platform == "nt" else "system-temp")
    assert environment["TMP"] == (str(tmp_path) if platform == "nt" else "system-tmp")


def test_version_probe_waits_after_dll_search_restoration(monkeypatch, executable):
    path = executable("print('Blender 4.5.11\\nbuild hash: fixture')")
    state = {"active": False}
    original_spawn = subprocess.Popen

    @contextmanager
    def boundary(*, external):
        assert external
        state["active"] = True
        try:
            yield
        finally:
            state["active"] = False

    def spawn(*args, **kwargs):
        assert state["active"]
        child = original_spawn(*args, **kwargs)
        communicate = child.communicate

        def read(*args, **kwargs):
            assert not state["active"]
            return communicate(*args, **kwargs)

        child.communicate = read
        return child

    monkeypatch.setattr(runtime, "child_dll_search", boundary)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    discovered = runtime.BlenderInstallation(path)
    result = runtime.probe_blender(discovered)
    assert result.version == (4, 5, 11)
    assert result.version_output == "Blender 4.5.11\nbuild hash: fixture"
    assert discovered.version is None
    assert not state["active"]


@pytest.mark.parametrize(
    "prefix", [b" ArchWarn: native library diagnostic\n", b"GPU driver warning\r\n"]
)
def test_version_probe_accepts_native_diagnostic_before_the_version(
    monkeypatch, tmp_path, prefix
):
    child = Mock(returncode=0)
    child.communicate.return_value = (
        prefix + b"Blender 4.5.11 LTS\nbuild hash: fixture",
        None,
    )
    monkeypatch.setattr(runtime, "launch_blender", Mock(return_value=child))
    result = runtime.probe_blender(runtime.BlenderInstallation(tmp_path / "blender"))
    assert result.version == (4, 5, 11)
    assert result.version_output.startswith(prefix.decode().strip())


def test_version_probe_rejects_an_embedded_name_in_an_unrelated_diagnostic(
    monkeypatch, tmp_path
):
    child = Mock(returncode=0)
    child.communicate.return_value = (b"Not a Blender 4.5.11 version record", None)
    monkeypatch.setattr(runtime, "launch_blender", Mock(return_value=child))
    with pytest.raises(ValueError, match="распознаваемую версию"):
        runtime.probe_blender(runtime.BlenderInstallation(tmp_path / "blender"))


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_background_launch_suppresses_a_console_only_on_windows(monkeypatch, platform):
    flag = 4242
    child = Mock(poll=Mock(return_value=0))
    spawn = Mock(return_value=child)
    monkeypatch.setattr(runtime, "os", SimpleNamespace(name=platform))
    guard = Mock()
    monkeypatch.setattr(runtime, "_require_windows_blender_executable", guard)
    monkeypatch.setattr(runtime.subprocess, "CREATE_NO_WINDOW", flag, raising=False)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    assert (
        runtime.launch_blender(
            ["fixture-blender"], environment={}, stdout=subprocess.DEVNULL
        )
        is child
    )
    assert spawn.call_args.kwargs["creationflags"] == (flag if platform == "nt" else 0)
    assert guard.call_count == (1 if platform == "nt" else 0)


@pytest.mark.skipif(os.name != "nt", reason="Windows PE classification")
def test_windows_rejects_malformed_executable_before_spawn(tmp_path, monkeypatch):
    malformed = tmp_path / "blender.exe"
    malformed.write_bytes(b"deliberately invalid executable")
    spawn = Mock(side_effect=AssertionError("Malformed file must not run"))
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="64-разрядный blender.exe"):
        runtime.probe_blender(runtime.BlenderInstallation(malformed))
    spawn.assert_not_called()


@pytest.mark.skipif(os.name != "nt", reason="Windows PE classification")
def test_windows_valid_non_blender_pe_still_fails_version(tmp_path):
    executable = tmp_path / "blender.exe"
    shutil.copyfile(os.path.join(os.environ["SystemRoot"], "System32", "where.exe"), executable)
    with pytest.raises(ValueError, match="версию установки"):
        runtime.probe_blender(runtime.BlenderInstallation(executable))


def test_failed_dll_restore_terminates_the_created_blender(monkeypatch, executable):
    path = executable("import time; time.sleep(30)")
    children = []
    original_spawn = subprocess.Popen

    @contextmanager
    def boundary(*, external):
        assert external
        yield
        raise OSError("DLL restore failed")

    def spawn(*args, **kwargs):
        child = original_spawn(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(runtime, "child_dll_search", boundary)
    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    with pytest.raises(OSError, match="DLL restore failed"):
        runtime.launch_blender([str(path)], environment={}, stdout=subprocess.DEVNULL)
    assert len(children) == 1
    assert children[0].poll() is not None


def test_probe_timeout_reaps_the_owned_process(monkeypatch, executable):
    path = executable("import time; time.sleep(30)")
    children = []
    original_spawn = subprocess.Popen

    def spawn(*args, **kwargs):
        child = original_spawn(*args, **kwargs)
        children.append(child)
        return child

    monkeypatch.setattr(runtime.subprocess, "Popen", spawn)
    with pytest.raises(subprocess.TimeoutExpired):
        runtime.probe_blender(runtime.BlenderInstallation(path), timeout_seconds=0.1)
    assert children[0].poll() is not None


@pytest.mark.parametrize("output", ["Not Blender 4.5.11", "Blender unknown"])
def test_probe_rejects_an_unidentified_executable(executable, output):
    path = executable(f"print({output!r})")
    with pytest.raises(ValueError, match="версию"):
        runtime.probe_blender(runtime.BlenderInstallation(path))
