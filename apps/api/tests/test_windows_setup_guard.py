"""Владение процессами перед переключением bundle: без остановки чужих программ."""

import ctypes
import os
from collections.abc import Iterable

import pytest

from app.distribution.windows_setup_guard import assert_applications_closed


class Owners:
    def __init__(self, rows: list[tuple[int, str]], sids: dict[int, bytes | OSError]):
        self.rows, self.sids = rows, sids
        self.queried: list[int] = []

    def current_sid(self) -> bytes:
        return b"current-user"

    def processes(self) -> Iterable[tuple[int, str]]:
        return self.rows

    def owner_sid(self, pid: int) -> bytes:
        self.queried.append(pid)
        value = self.sids[pid]
        if isinstance(value, OSError):
            raise value
        return value


def test_unrelated_processes_are_not_opened() -> None:
    api = Owners([(1, "Explorer.exe"), (2, "GreenAtlasSetup.exe")], {})
    assert_applications_closed(api)
    assert api.queried == []


@pytest.mark.parametrize("name", ["Acad.exe", "accoreconsole.exe", "GreenAtlasDesktop.exe", "GreenAtlasRuntime.exe"])
def test_same_sid_blocks_update_even_in_another_session(name: str) -> None:
    api = Owners([(42, name)], {42: b"current-user"})
    with pytest.raises(RuntimeError, match="во всех своих сеансах"):
        assert_applications_closed(api)
    assert api.queried == [42]


def test_other_sid_does_not_block_and_exit_race_is_tolerated() -> None:
    api = Owners([(42, "acad.exe"), (43, "acad.exe")], {42: b"another-user", 43: ProcessLookupError()})
    assert_applications_closed(api)


def test_unknown_owner_refuses_switch() -> None:
    api = Owners([(42, "GreenAtlasDesktop.exe")], {42: PermissionError("access denied")})
    with pytest.raises(OSError, match="Не удалось проверить владельца"):
        assert_applications_closed(api)


def test_invalid_parameter_is_exit_race_only_when_opening_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.distribution.windows_setup_guard import _WindowsOwners

    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87, raising=False)
    assert isinstance(_WindowsOwners._error(allow_exited=True), ProcessLookupError)
    assert not isinstance(_WindowsOwners._error(), ProcessLookupError)


@pytest.mark.skipif(os.name != "nt", reason="Настоящий Win32 Toolhelp")
def test_real_windows_inventory_reads_current_sid() -> None:
    from app.distribution.windows_setup_guard import _WindowsOwners

    api = _WindowsOwners()
    assert api.current_sid() == api.owner_sid(os.getpid())
    assert any(pid == os.getpid() for pid, _ in api.processes())
