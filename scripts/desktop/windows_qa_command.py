"""Run one CI Python phase in an owned Windows job with a bounded lifetime."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "apps/api"))


def run(arguments, log_path, timeout):
    from app.shared.windows_process import launch_owned

    started = time.monotonic()
    child = launch_owned([sys.executable, *arguments], cwd=REPOSITORY)
    code, reason = 1, "error"
    try:
        with log_path.open("xb") as log:
            while True:
                data = child.read_available(16384)
                if data:
                    log.write(data)
                    log.flush()
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                returned = child.poll()
                if returned is not None:
                    finishing = time.monotonic() + 2
                    while child.active_processes() and time.monotonic() < finishing:
                        time.sleep(0.02)
                    while data := child.read_available(16384):
                        log.write(data)
                        sys.stdout.buffer.write(data)
                    log.flush()
                    sys.stdout.buffer.flush()
                    if child.active_processes():
                        code, reason = 125, "python_exited_with_live_children"
                    else:
                        code, reason = returned, "exited"
                    break
                if time.monotonic() - started >= timeout:
                    code, reason = 124, "phase_deadline_exceeded"
                    break
                if not data:
                    time.sleep(0.02)
    finally:
        child.close()
        print(
            "\nGA_CI_COMMAND_END "
            + json.dumps(
                {
                    "exit_code": code,
                    "reason": reason,
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                    "owned_tree_closed": child.closed,
                }
            ),
            flush=True,
        )
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=1200)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if os.name != "nt" or not 0.1 <= args.timeout <= 1800:
        raise ValueError("A bounded real Windows phase is required")
    log = args.log.absolute()
    if not log.is_relative_to(REPOSITORY / ".runtime") or log.resolve() != log:
        raise ValueError("CI command log must stay in owned checkout .runtime")
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    if not arguments:
        raise ValueError("Missing Python arguments")
    return run(arguments, log, args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
