"""Границы подключения native uninstall к существующей транзакции установки."""

from pathlib import Path

import pytest
from app.distribution.windows_shell import native_uninstaller


def test_only_own_uninstaller_can_be_registered(tmp_path: Path) -> None:
    folder = tmp_path / "GreenAtlas/Installer/Wizard"
    folder.mkdir(parents=True)
    executable = folder / "unins000.exe"
    executable.write_bytes(b"MZnative-fixture")
    assert native_uninstaller(tmp_path, executable) == executable
    foreign = tmp_path / "foreign.exe"
    foreign.write_bytes(b"MZforeign")
    with pytest.raises(ValueError, match="Неизвестный путь"):
        native_uninstaller(tmp_path, foreign)


@pytest.mark.parametrize("name", ["unins000.exe", "unins999.exe"])
def test_uninstaller_must_be_plain_real_executable(tmp_path: Path, name: str) -> None:
    folder = tmp_path / "GreenAtlas/Installer/Wizard"
    folder.mkdir(parents=True)
    path = folder / name
    path.write_bytes(b"invalid")
    with pytest.raises(ValueError, match="Windows-программой"):
        native_uninstaller(tmp_path, path)
    path.unlink()
    target = tmp_path / "target"
    target.write_bytes(b"MZnative-fixture")
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip("Windows runner не разрешает создание symlink")
    with pytest.raises(ValueError, match="ссылку"):
        native_uninstaller(tmp_path, path)
