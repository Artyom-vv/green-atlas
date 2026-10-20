"""Отказ регистрации ярлыков и удаления возвращает собственные прежние данные."""

import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.distribution.windows_shell import WindowsShell


def test_failed_shell_publication_restores_shortcuts_and_registry(tmp_path, monkeypatch):
    import app.distribution.windows_shell as shell_module

    values = {"DisplayName": ("previous", 1), "UnknownOwnValue": (42, 4)}
    initial = dict(values)
    fail = [True]

    def open_key(*args):
        return nullcontext("own-key")

    def enum_value(key, index):
        try:
            name, (value, kind) = list(values.items())[index]
            return name, value, kind
        except IndexError as error:
            raise OSError("end") from error

    def set_value(key, name, reserved, kind, value):
        if name == "DisplayVersion" and fail[0]:
            fail[0] = False
            raise OSError("registration failed")
        values[name] = (value, kind)

    def query_value(key, name):
        try:
            return values[name]
        except KeyError as error:
            raise FileNotFoundError(name) from error

    fake = SimpleNamespace(HKEY_CURRENT_USER=object(), REG_SZ=1, REG_DWORD=4, OpenKey=open_key,
                           EnumValue=enum_value, QueryValueEx=query_value, CreateKey=open_key, SetValueEx=set_value,
                           DeleteKey=lambda *args: values.clear())
    monkeypatch.setitem(sys.modules, "winreg", fake)

    def shortcut(path: Path, executable: Path, *, arguments: str = "") -> None:
        path.write_text(str(executable) + arguments)

    monkeypatch.setattr(shell_module, "create_shortcut", shortcut)
    shell = WindowsShell(tmp_path / "Roaming", tmp_path / "Setup.exe")
    shell.shortcut.parent.mkdir(parents=True)
    shell.shortcut.write_bytes(b"previous-shortcut")
    shell.rollback_shortcut.write_bytes(b"legacy-rollback-shortcut")
    with pytest.raises(OSError, match="registration failed"):
        shell.publish(tmp_path / "GreenAtlas.bundle", "0.1.119")
    assert values == initial
    assert shell.shortcut.read_bytes() == b"previous-shortcut"
    assert shell.rollback_shortcut.read_bytes() == b"legacy-rollback-shortcut"
    shell.publish(tmp_path / "GreenAtlas.bundle", "0.1.119")
    assert values["DisplayVersion"] == ("0.1.119", 1)
    assert not shell.rollback_shortcut.exists()


def test_setup_mutex_is_sid_scoped_global_and_released_on_failure(monkeypatch):
    import app.distribution.windows_shell as target

    events = []
    error = [0]

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    def create(security, owner, name):
        events.append(("create", owner, name))
        return 123

    kernel = SimpleNamespace(CreateMutexW=Function(create),
                             ReleaseMutex=Function(lambda handle: events.append(("release", handle)) or True),
                             CloseHandle=Function(lambda handle: events.append(("close", handle)) or True))
    monkeypatch.setattr(target, "require_windows_user", lambda: None)
    monkeypatch.setattr(target, "current_user_sid", lambda: b"same-user-sid")
    monkeypatch.setattr(target.ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(target.ctypes, "get_last_error", lambda: error[0], raising=False)
    name = target.setup_mutex_name(b"same-user-sid")
    assert name.startswith("Global\\")
    assert name != target.setup_mutex_name(b"other-user-sid")
    with pytest.raises(RuntimeError, match="failure"):
        with target.installation_mutex():
            raise RuntimeError("failure")
    assert events == [("create", True, name), ("release", 123), ("close", 123)]
    events.clear()
    error[0] = 183
    with pytest.raises(ValueError, match="уже работает"):
        with target.installation_mutex():
            pytest.fail("Второй setup не должен начинать операцию")
    assert events == [("create", True, name), ("close", 123)]


@pytest.mark.parametrize("error", [False, True])
def test_windowed_setup_diagnostic_uses_inherited_native_pipe(monkeypatch, error):
    import ctypes

    import app.distribution.windows_shell as target

    chunks = []
    handles = []

    class Function:
        def __init__(self, call):
            self.call = call

        def __call__(self, *args):
            return self.call(*args)

    def write(handle, data, count, written, overlapped):
        assert handle == 123 and overlapped is None
        count = min(count, 3)
        chunks.append(ctypes.string_at(data, count))
        ctypes.cast(written, ctypes.POINTER(ctypes.wintypes.DWORD)).contents.value = count
        return True

    kernel = SimpleNamespace(GetStdHandle=Function(lambda kind: handles.append(kind) or 123), WriteFile=Function(write))
    monkeypatch.setattr(target, "sys", SimpleNamespace(stdout=None, stderr=None))
    monkeypatch.setattr(target, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(target.ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    message = '{"message":"кириллица и пробелы"}\n'
    assert target.write_diagnostic(message, error=error)
    assert b"".join(chunks).decode("utf-8") == message
    assert handles == [0xfffffff4 if error else 0xfffffff5]


def test_windowed_setup_without_console_does_not_allocate_or_crash(monkeypatch):
    import app.distribution.windows_shell as target

    class Function:
        def __call__(self, *args):
            return None

    monkeypatch.setattr(target, "sys", SimpleNamespace(stdout=None, stderr=None))
    monkeypatch.setattr(target, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(target.ctypes, "WinDLL", lambda *args, **kwargs: SimpleNamespace(GetStdHandle=Function(), WriteFile=Function()), raising=False)
    assert target.write_diagnostic("diagnostic\n") is False
