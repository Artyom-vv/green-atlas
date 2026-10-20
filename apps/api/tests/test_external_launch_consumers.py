"""Внешние версии и классификатор не держат DLL-lock во время ожидания ответа."""

import os
import signal
import subprocess
import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import BaseModel

from app.cad_import.references import identity
from app.layer_recognition import providers


def test_converter_identity_waits_outside_the_creation_boundary(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Тестовый конвертер использует POSIX shebang")
    executable = tmp_path / "fake-dwg2dxf"
    executable.write_text(
        f"#!{sys.executable}\nprint('dwg2dxf fixture version')\n", encoding="utf-8"
    )
    executable.chmod(0o700)
    active = False
    original_spawn = subprocess.Popen

    @contextmanager
    def boundary(*, external):
        nonlocal active
        assert external
        active = True
        try:
            yield
        finally:
            active = False

    def spawn(*args, **kwargs):
        assert active
        child = original_spawn(*args, **kwargs)
        communicate = child.communicate

        def read(*args, **kwargs):
            assert not active
            return communicate(*args, **kwargs)

        child.communicate = read
        return child

    monkeypatch.setattr(identity, "child_dll_search", boundary)
    monkeypatch.setattr(identity.subprocess, "Popen", spawn)
    result = identity.converter_identity(executable)
    assert result.version == "dwg2dxf fixture version"
    assert not active


def test_converter_restore_failure_reaps_child(monkeypatch, tmp_path):
    child = Mock(poll=Mock(return_value=None))

    @contextmanager
    def boundary(*, external):
        assert external
        yield
        raise OSError("DLL restore failed")

    monkeypatch.setattr(identity, "child_dll_search", boundary)
    monkeypatch.setattr(identity.subprocess, "Popen", Mock(return_value=child))
    with pytest.raises(OSError, match="DLL restore failed"):
        identity.converter_identity(tmp_path / "converter")
    child.kill.assert_called_once_with()
    child.communicate.assert_called_once_with()


@pytest.mark.parametrize("platform", ["darwin", "win32"])
def test_codex_restore_failure_cleans_up_the_created_child(
    monkeypatch, tmp_path, platform
):
    class Reply(BaseModel):
        ok: bool

    child = Mock(pid=123, poll=Mock(return_value=None))
    stop_group = Mock()

    @contextmanager
    def boundary(*, external):
        assert external
        yield
        raise OSError("DLL restore failed")

    monkeypatch.setattr(providers, "child_dll_search", boundary)
    monkeypatch.setattr(providers.subprocess, "Popen", Mock(return_value=child))
    monkeypatch.setattr(providers, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(
        providers, "os", SimpleNamespace(killpg=stop_group)
    )
    with pytest.raises(OSError, match="DLL restore failed"):
        providers.CodexLayerProvider("codex")._request(
            "Return JSON", "{}", Reply, "x", 128
        )
    child.wait.assert_called_once_with(timeout=2)
    if platform == "win32":
        child.terminate.assert_called_once_with()
        stop_group.assert_not_called()
    else:
        stop_group.assert_called_once_with(child.pid, signal.SIGTERM)
        child.terminate.assert_not_called()
