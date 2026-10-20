"""Владельцы процессов выбирают DLL-политику и сохраняют cleanup при ошибке запуска."""

import os
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.cad_import.legacy import process as converter
from app.cad_import.policy import ConversionPolicy
from app.native_query.transport import process as core
from app.shared.python_worker import worker_command


@pytest.mark.parametrize("own", [False, True])
@pytest.mark.parametrize("fail_restore", [False, True])
def test_converter_selects_worker_libraries_and_owns_restore_failure(
    tmp_path, monkeypatch, own, fail_restore
):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    policy = []
    active = False

    @contextmanager
    def dll_search(*, external):
        nonlocal active
        policy.append(external)
        active = True
        try:
            yield
        finally:
            active = False
        if fail_restore:
            raise OSError("restore failed")

    child = SimpleNamespace(pid=123, poll=lambda: 0, returncode=0, wait=Mock())
    tree = SimpleNamespace(stop=Mock(), resident_bytes=lambda: 0)

    def spawn(command, **kwargs):
        assert active
        assert kwargs["cwd"] == tmp_path
        return child

    monkeypatch.setattr(converter, "child_dll_search", dll_search)
    monkeypatch.setattr(converter.subprocess, "Popen", spawn)
    monkeypatch.setattr(converter, "ProcessTree", lambda _: tree)
    command = (
        worker_command("app.dxf_import.native.live_worker", "task.json")
        if own
        else ["external.exe"]
    )
    if fail_restore:
        with pytest.raises(OSError, match="restore failed"):
            converter.run_converter(
                command, tmp_path / "out", tmp_path / "log", ConversionPolicy()
            )
    else:
        result = converter.run_converter(
            command, tmp_path / "out", tmp_path / "log", ConversionPolicy()
        )
        assert result.exit_code == 0
    assert policy == [not own]
    tree.stop.assert_called_once()
    child.wait.assert_called_once()


@pytest.mark.parametrize("fail_restore", [False, True])
@pytest.mark.skipif(os.name != "posix", reason="POSIX Core subprocess policy")
def test_core_external_policy_preserves_posix_spawn_and_owns_restore_failure(
    tmp_path, monkeypatch, fail_restore
):
    active = False

    @contextmanager
    def dll_search(*, external):
        nonlocal active
        assert external
        active = True
        try:
            yield
        finally:
            active = False
        if fail_restore:
            raise OSError("restore failed")

    child = object()

    def spawn(command, **kwargs):
        assert active
        assert command == ("Core",)
        assert kwargs["cwd"] == tmp_path
        assert kwargs["start_new_session"] is True
        assert kwargs["shell"] is False
        return child

    stop = Mock()
    monkeypatch.setattr(core, "child_dll_search", dll_search)
    monkeypatch.setattr(core.subprocess, "Popen", spawn)
    monkeypatch.setattr(core, "_stop_group", stop)
    if fail_restore:
        with pytest.raises(OSError, match="restore failed"):
            core._spawn(("Core",), tmp_path)
        stop.assert_called_once_with(child, 2, [])
    else:
        assert core._spawn(("Core",), tmp_path) is child
        stop.assert_not_called()
