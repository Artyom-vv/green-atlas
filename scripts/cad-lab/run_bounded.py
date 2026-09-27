"""Run an isolated CAD experiment; never publish its output to a project.

Usage: python run_bounded.py REPORT.json -- executable arguments...
Resource limits cover the owned process tree, including Windows venv launchers.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'apps/api'))
from app.cad_import.memory import ProcessTree


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('report', type=Path)
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--memory-mib', type=int, default=4096)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.report.exists():
        raise FileExistsError(args.report)
    started = time.monotonic()
    peak = 0
    status = 'running'
    with args.report.with_suffix('.log').open('wb') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        tree = ProcessTree(process.pid)
        try:
            while process.poll() is None:
                peak = max(peak, tree.resident_bytes())
                if peak > args.memory_mib * 1024**2:
                    status = 'memory_limit'; break
                if time.monotonic() - started > args.seconds:
                    status = 'timeout'; break
                time.sleep(0.05)
            if status == 'running':
                status = 'completed' if process.returncode == 0 else 'failed'
        finally:
            tree.stop()
            process.wait()
    result = dict(command=command, status=status, exit_code=process.returncode,
        elapsed_seconds=time.monotonic()-started, peak_tree_rss_bytes=peak,
        memory_limit_bytes=args.memory_mib * 1024**2, timeout_seconds=args.seconds,
        measurement='sampled process-tree RSS every 50 ms; not OS hard commit cap')
    args.report.write_text(json.dumps(result, indent=2)+'\n', encoding='utf8')
    print(json.dumps(result))
    return 0 if status == 'completed' else 1


if __name__ == '__main__':
    sys.exit(main())
