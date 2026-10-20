"""Контракт поиска DLL и гонки запуска; реальный Win32 проверяется отдельно."""

import os
import subprocess
import sys
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from app.shared import child_dll_search as dll


@pytest.fixture
def frozen_windows(monkeypatch):
    monkeypatch.setattr(dll, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", "другой bundle", raising=False)
    state = {"directory": "исходный путь DLL", "changes": []}
    monkeypatch.setattr(dll, "_get_directory", lambda: state["directory"])

    def set_directory(value):
        state["changes"].append(value)
        state["directory"] = value

    monkeypatch.setattr(dll, "_set_directory", set_directory)
    return state


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize(
    ("platform", "frozen"), [("posix", False), ("posix", True), ("nt", False)]
)
def test_mac_and_unfrozen_python_do_not_touch_dll_state(
    monkeypatch, platform, frozen, external
):
    monkeypatch.setattr(dll, "os", SimpleNamespace(name=platform))
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(dll, "_get_directory", lambda: pytest.fail("DLL read"))
    monkeypatch.setattr(dll, "_set_directory", lambda _: pytest.fail("DLL write"))
    with dll.child_dll_search(external=external):
        pass


def test_external_child_restores_actual_directory_not_assumed_bundle(frozen_windows):
    with dll.child_dll_search(external=True):
        assert frozen_windows["directory"] is None
    assert frozen_windows["directory"] == "исходный путь DLL"
    assert frozen_windows["changes"] == [None, "исходный путь DLL"]


def test_own_worker_keeps_bundle_search(frozen_windows):
    with dll.child_dll_search(external=False):
        assert frozen_windows["directory"] == "исходный путь DLL"
    assert frozen_windows["changes"] == []


def test_failed_spawn_restores_api_search(frozen_windows):
    with pytest.raises(OSError, match="creation failed"):
        with dll.child_dll_search(external=True):
            raise OSError("creation failed")
    assert frozen_windows["directory"] == "исходный путь DLL"


def test_nested_external_scope_restores_each_previous_state(frozen_windows):
    with dll.child_dll_search(external=True):
        with dll.child_dll_search(external=True):
            assert frozen_windows["directory"] is None
        assert frozen_windows["directory"] is None
    assert frozen_windows["directory"] == "исходный путь DLL"


def test_own_worker_waits_until_external_search_is_restored(frozen_windows):
    started, entered = Event(), Event()
    observed = []

    def own_spawn():
        started.set()
        with dll.child_dll_search(external=False):
            observed.append(frozen_windows["directory"])
            entered.set()

    thread = Thread(target=own_spawn)
    try:
        with dll.child_dll_search(external=True):
            thread.start()
            assert started.wait(1)
            assert not entered.wait(0.05)
        assert entered.wait(1)
    finally:
        thread.join(timeout=1)
    assert not thread.is_alive()
    assert observed == ["исходный путь DLL"]


def test_failed_reset_does_not_start_child(frozen_windows, monkeypatch):
    def reject(_):
        raise OSError("reset failed")

    monkeypatch.setattr(dll, "_set_directory", reject)
    with pytest.raises(OSError, match="reset failed"):
        with dll.child_dll_search(external=True):
            pytest.fail("child creation must not begin")


def test_failed_restore_is_not_reported_as_success(frozen_windows, monkeypatch):
    def reject_restore(value):
        if value is not None:
            raise OSError("restore failed")

    monkeypatch.setattr(dll, "_set_directory", reject_restore)
    with pytest.raises(OSError, match="restore failed"):
        with dll.child_dll_search(external=True):
            pass


@pytest.mark.skipif(os.name != "nt", reason="Требуется реальный Windows DLL loader")
def test_real_windows_external_program_and_restore(monkeypatch, tmp_path):
    previous = dll._get_directory()
    bundle = str(tmp_path)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    dll._set_directory(bundle)
    try:
        with dll.child_dll_search(external=True):
            assert dll._get_directory() is None
            child = subprocess.Popen(
                [
                    os.path.join(os.environ["SystemRoot"], "System32", "where.exe"),
                    "cmd.exe",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        assert dll._get_directory() == bundle
        _, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
    finally:
        dll._set_directory(previous)
