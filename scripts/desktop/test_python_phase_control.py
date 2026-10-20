"""Аргументы, код выхода и аварийная диагностика незавершённого потока."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

CONTROL = Path(__file__).with_name("python_phase_control.py")


def execute(command, *, deadline=10, environment=None):
    return subprocess.run(
        [
            sys.executable,
            "-u",
            str(CONTROL),
            "--deadline",
            str(deadline),
            "--",
            *command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
        env={**os.environ, "PYTHONUTF8": "1", **(environment or {})},
    )


def test_inline_code_preserves_arguments_and_nonzero_exit():
    result = execute(
        ["-c", "import json,sys;print(json.dumps(sys.argv));sys.exit(7)", "с пробелом"]
    )
    assert result.returncode == 7, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == ["-c", "с пробелом"]


@pytest.mark.parametrize("module", [False, True])
def test_module_and_file_keep_their_actual_entry_and_arguments(tmp_path, module):
    path = tmp_path / ("phase_fixture.py" if module else "Контроль с пробелом.py")
    path.write_text(
        "import json,sys;print(json.dumps({'name':__name__,'args':sys.argv[1:]}))",
        encoding="utf-8",
    )
    command = ["-m", "phase_fixture"] if module else [str(path)]
    result = execute(
        [*command, "a b", "кириллица"], environment={"PYTHONPATH": str(tmp_path)}
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.splitlines()[-1]) == {
        "name": "__main__",
        "args": ["a b", "кириллица"],
    }


def test_watchdog_catches_a_thread_that_hangs_during_interpreter_shutdown():
    result = execute(
        [
            "-c",
            "import threading,time;threading.Thread(target=lambda:time.sleep(30)).start();print('thread-live',flush=True)",
        ],
        deadline=0.3,
    )
    assert result.returncode == 1
    assert "thread-live" in result.stdout
    assert "Timeout" in result.stderr and "Thread" in result.stderr


def test_watchdog_stays_armed_after_pytest_has_reported_success(tmp_path):
    target = tmp_path / "test_unjoined_thread.py"
    target.write_text(
        "import threading,time\ndef test_complete():\n"
        "    threading.Thread(target=lambda:time.sleep(30)).start()\n",
        encoding="utf-8",
    )
    result = execute(
        [
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "-p",
            "no:cacheprovider",
            "--confcutdir",
            str(tmp_path),
            str(target),
        ],
        deadline=3,
        environment={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert result.returncode == 1
    assert "1 passed" in result.stdout
    assert "Timeout" in result.stderr


def test_watchdog_dump_escapes_active_pytest_fd_capture(tmp_path):
    target = tmp_path / "test_blocked_body.py"
    target.write_text(
        "import time\ndef test_blocked():\n    time.sleep(30)\n", encoding="utf-8"
    )
    result = execute(
        [
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "-p",
            "no:cacheprovider",
            "--confcutdir",
            str(tmp_path),
            str(target),
        ],
        deadline=3,
        environment={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert result.returncode == 1
    assert "Timeout" in result.stderr and "test_blocked" in result.stderr


def test_file_transport_finishes_without_a_reader_for_the_native_pipes(tmp_path):
    log = tmp_path / "phase.log"
    command = [
        sys.executable,
        "-u",
        str(CONTROL),
        "--deadline",
        "3",
        "--log",
        str(log),
        "--",
        "-c",
        "import os;os.write(1,b'A'*131072);os.write(2,b'B'*131072)",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        code = process.wait(timeout=10)
        stdout, stderr = process.communicate(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
    assert code == 0
    assert stdout == stderr == b""
    content = log.read_bytes()
    assert b"A" * 131072 in content and b"B" * 131072 in content


def test_timeout_and_its_stack_remain_in_the_owned_log(tmp_path):
    log = tmp_path / "timeout.log"
    result = subprocess.run(
        [
            sys.executable,
            "-u",
            str(CONTROL),
            "--deadline",
            "0.3",
            "--log",
            str(log),
            "--",
            "-c",
            "import time;time.sleep(30)",
        ],
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 1
    assert result.stdout == result.stderr == b""
    assert "Timeout" in log.read_text(encoding="utf-8")
