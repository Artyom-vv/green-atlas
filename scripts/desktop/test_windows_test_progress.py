"""Диагностика сохраняет реальные исходы pytest и экранирует workflow-команды."""

import os
import subprocess
import sys
from pathlib import Path

from scripts.desktop.windows_test_progress import _notice


def test_notice_cannot_split_into_a_second_workflow_command(capsys):
    _notice("start", "case%\r\n::error::injected")
    assert capsys.readouterr().out == (
        "::notice title=Windows source test::start: case%25%0D%0A::error::injected\n"
    )


def test_progress_does_not_turn_a_failed_case_into_success(tmp_path):
    target = tmp_path / "test_progress_fixture.py"
    target.write_text("def test_ok(): pass\ndef test_fail(): assert False\n")
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "-s",
            "-p",
            "scripts.desktop.windows_test_progress",
            "-p",
            "no:cacheprovider",
            "--confcutdir",
            str(tmp_path),
            str(target),
        ],
        cwd=root,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("::notice title=Windows source test::start:") == 2
    assert result.stdout.count("::notice title=Windows source test::finish:") == 2
    assert "1 failed, 1 passed" in result.stdout
