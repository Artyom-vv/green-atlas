"""Real Windows command exit and subtree cleanup before trusting CI receipts."""

import json
import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import psutil
import pytest

from scripts.desktop import windows_qa_command as command

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Real Windows Job Object control"
)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_native_command_preserves_utf8_and_nonzero_exit(exit_code):
    with TemporaryDirectory(
        dir=command.REPOSITORY / ".runtime", prefix="qa-command-"
    ) as directory:
        root = Path(directory)
        log = root / "phase.log"
        result = subprocess.run(
            [
                sys.executable,
                str(Path(command.__file__)),
                "--log",
                str(log),
                "--timeout",
                "10",
                "--",
                "-c",
                "import sys;print('Готово',flush=True);sys.exit(int(sys.argv[1]))",
                str(exit_code),
            ],
            capture_output=True,
            timeout=20,
            check=False,
            env={**os.environ, "PYTHONUTF8": "1"},
        )
        assert result.returncode == exit_code
        assert log.read_text(encoding="utf-8").strip() == "Готово"
        assert "Готово" in result.stdout.decode("utf-8")


@pytest.mark.parametrize("early_exit,expected", [(False, 124), (True, 125)])
def test_watchdog_or_orphan_detection_reaps_the_owned_descendant(early_exit, expected):
    with TemporaryDirectory(
        dir=command.REPOSITORY / ".runtime", prefix="qa-command-"
    ) as directory:
        root = Path(directory)
        pids = root / "owned-pids.json"
        source = (
            "import json,os,subprocess,sys,time;from pathlib import Path;"
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']);"
            "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]));"
            + ("sys.exit(0)" if early_exit else "time.sleep(60)")
        )
        result = subprocess.run(
            [
                sys.executable,
                str(Path(command.__file__)),
                "--log",
                str(root / "phase.log"),
                "--timeout",
                "5",
                "--",
                "-c",
                source,
                str(pids),
            ],
            capture_output=True,
            timeout=20,
            check=False,
        )
        assert result.returncode == expected, result.stdout.decode(
            "utf-8", errors="replace"
        )
        assert all(not psutil.pid_exists(pid) for pid in json.loads(pids.read_bytes()))
