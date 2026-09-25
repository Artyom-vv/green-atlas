"""Keep a test-copy DXF open in Core for ordinary API/UI live-session tests.

Not a saved-file-per-query provider: all requests operate on one open document.
The user's GUI process is untouched. Stop the runner to discard its session.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
from uuid import uuid4

from run_direct_queries import CORE, quoted


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve(strict=True)
    if source.suffix.lower() != ".dxf" or not str(root).isascii():
        parser.error("Use a DXF and an ASCII output path")
    bundle = root / "LiveQuery.dbx"
    shutil.copytree(args.bundle.resolve(strict=True), bundle)
    script = root / "serve.scr"
    script.write_text(f'(setvar "TRUSTEDPATHS" {quoted(str(root)+"/")})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload {quoted(bundle)})\nGADEMOLOOP\n_QUIT\n_Y\n')
    with (root / "core.log").open("wb") as log:
        child = subprocess.Popen(["/usr/bin/arch", "-x86_64", str(CORE), "/i", str(source),
            "/s", str(script), "/isolate", "ga-demo-"+uuid4().hex, str(root / "profile")],
            cwd=root, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        queue = Path(f"/tmp/green-atlas-live-query-{os.getuid()}-{child.pid}")
        (root / "process.json").write_text(json.dumps({"pid": child.pid, "queue": str(queue),
            "source": str(source), "bundle": str(bundle)}))
        print(f"LIVE_DEMO_PID={child.pid}", flush=True)
        def stop_session(signum, _frame):
            if queue.is_dir():
                (queue / "stop-live-demo").touch()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGTERM)
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, stop_session)
        signal.signal(signal.SIGINT, stop_session)
        try:
            child.wait()
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGKILL)
                    child.wait()
        print(f"LIVE_DEMO_EXIT={child.returncode}", flush=True)
    return int(child.returncode != 0)


if __name__ == "__main__":
    raise SystemExit(main())
