"""Private child process of the desktop app; not an end-user command.

The parent must capture stdout as a private pipe: the single readiness record
contains a per-launch session token for its WebView. No token is written to a
URL, disk, access log or shared service configuration. No browser is launched.
Frozen packaging/worker dispatch is a separate acceptance gate.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import secrets
import socket
from pathlib import Path

import uvicorn

from app.desktop.lease import WorkspaceLease
from app.desktop.web import create_desktop_web


def configure_local_storage(directory: Path) -> Path:
    directory = directory.absolute()
    if any(path.is_symlink() for path in (directory, *directory.parents)):
        raise ValueError("Desktop storage must not be behind a symbolic link")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError("Desktop storage is not a directory")
    os.environ["GREEN_ATLAS_DB_PATH"] = str(directory / "projects.sqlite3")
    os.environ["GREEN_ATLAS_CAD_INTAKE_PATH"] = str(directory / "cad-intake")
    os.environ["GREEN_ATLAS_CAD_ROOTS_JSON"] = "{}"
    # Never inherit a previous developer/remote bridge destination or credentials.
    for key in (
        "GREEN_ATLAS_BRIDGE_ORIGIN",
        "GREEN_ATLAS_BRIDGE_PROXY_KEY",
        "GREEN_ATLAS_DWG_CONVERTER",
    ):
        os.environ.pop(key, None)
    return directory


def main() -> int:
    parser = argparse.ArgumentParser(description="Green Atlas private desktop runtime")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--web-dir", type=Path, required=True)
    parser.add_argument("--ticket-root", type=Path)
    parser.add_argument("--cad-worker", type=Path)
    args = parser.parse_args()
    data_root = configure_local_storage(args.data_dir)
    # Desktop uses OpenAI or exact-input packaged proposals, never a local
    # developer's Codex session as an implicit dependency.
    os.environ.setdefault("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    os.environ["GREEN_ATLAS_LAYER_RECOGNITION_PRESETS"] = str(
        args.web_dir.parent / "LayerRecognition"
    )
    if args.cad_worker:
        os.environ["GREEN_ATLAS_CAD_RELEASE_WORKER"] = str(args.cad_worker.absolute())
    if not (args.web_dir / "index.html").is_file():
        parser.error("Built desktop UI is missing from the application package")
    lease = WorkspaceLease(data_root)
    atexit.register(lease.close)
    # Configure process-owned persistence before constructing the existing API.
    from app.composition import get_runtime
    from app.desktop.handoff import LocalHandoff
    from app.main import app

    handoff = (
        LocalHandoff(get_runtime(), args.ticket_root) if args.ticket_root else None
    )

    secret = secrets.token_urlsafe(32)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        desktop = create_desktop_web(
            app, args.web_dir, origin=origin, secret=secret, handoff=handoff
        )

        class DesktopServer(uvicorn.Server):
            async def startup(self, sockets=None):
                await super().startup(sockets=sockets)
                if self.started:
                    print(
                        json.dumps(
                            {
                                "service": "green-atlas-desktop",
                                "protocol": 1,
                                "origin": origin,
                                "session_token": secret,
                            }
                        ),
                        flush=True,
                    )

        server = DesktopServer(
            uvicorn.Config(
                desktop,
                host="127.0.0.1",
                port=port,
                access_log=False,
                proxy_headers=False,
                server_header=False,
                log_level="warning",
            )
        )
        try:
            server.run(sockets=[listener])
        finally:
            if handoff:
                handoff.close()
            lease.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
