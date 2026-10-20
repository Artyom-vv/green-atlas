"""Переносимые policy tests и Windows-only CLI smoke без записи в профиль пользователя."""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DESKTOP = ROOT / "tools/autocad-bridge/desktop"


@pytest.mark.skipif(os.name == "nt", reason="MSVC запускает эти тесты в build-desktop-windows.ps1")
def test_portable_windows_host_policy_compiles_and_runs(tmp_path):
    compiler = shutil.which("clang++")
    if compiler is None:
        pytest.skip("Нужен локальный C++17 compiler")
    binary = tmp_path / "windows-host-policy"
    subprocess.run(
        [compiler, "-std=c++17", "-Wall", "-Wextra", "-Werror",
         str(DESKTOP / "contracts.cpp"), str(DESKTOP / "windows-policies.cpp"),
         str(DESKTOP / "test_windows_policies.cpp"), "-o", str(binary)],
        check=True, timeout=60, capture_output=True,
    )
    result = subprocess.run([str(binary)], check=True, timeout=10, capture_output=True, text=True)
    receipt = json.loads(result.stdout)
    assert receipt["passed"] is True and receipt["real_windows"] is False


@pytest.mark.skipif(os.name != "nt", reason="Требуется собранный Windows EXE")
@pytest.mark.parametrize("flag,schema", [
    ("--self-test", "green-atlas.desktop-self-test/1"),
    ("--build-info", "green-atlas.desktop-build/1"),
    ("--status-json", "green-atlas.desktop-status/1"),
])
def test_windows_readonly_host_cli(flag, schema):
    configured = os.environ.get("GREEN_ATLAS_DESKTOP_TEST_EXE")
    if not configured:
        pytest.skip("Укажите GREEN_ATLAS_DESKTOP_TEST_EXE для собранного candidate")
    executable = Path(configured)
    assert executable.name == "GreenAtlasDesktop.exe" and executable.is_file()
    result = subprocess.run(
        [str(executable), flag], capture_output=True, text=True, encoding="utf-8", timeout=15, check=False,
    )
    assert len(result.stdout) <= 32768
    receipt = json.loads(result.stdout)
    assert receipt["schema"] == schema
    if flag == "--self-test":
        assert result.returncode == 0 and receipt["passed"] is True
        assert receipt["webview_launched"] is False
    else:
        assert receipt["qualification"] == "not_claimed"
        assert "session_token" not in receipt and "origin" not in receipt


@pytest.mark.skipif(os.name != "nt", reason="Требуется собранный Windows EXE")
@pytest.mark.parametrize("arguments", [["--ticket-id", ""], ["--ticket-id", "../ticket"], ["--ticket", "outside"]])
def test_windows_rejects_invalid_ticket_arguments(arguments):
    configured = os.environ.get("GREEN_ATLAS_DESKTOP_TEST_EXE")
    if not configured:
        pytest.skip("Укажите GREEN_ATLAS_DESKTOP_TEST_EXE для собранного candidate")
    executable = Path(configured)
    assert executable.name == "GreenAtlasDesktop.exe" and executable.is_file()
    result = subprocess.run([str(executable), *arguments], capture_output=True, timeout=15, check=False)
    assert result.returncode == 2 and not result.stdout
