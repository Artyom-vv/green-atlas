"""Developer-only live bridge harness: real nginx, API and web, no mocked routes.

No production configuration is changed. All listeners bind to loopback, data and
credentials are new per run, and only this run's child processes/container stop.
Requires the existing API venv, Node dependencies and Docker Desktop on macOS.
"""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18180)
    parser.add_argument("--api-port", type=int, default=18181)
    parser.add_argument("--web-port", type=int, default=18182)
    args = parser.parse_args()
    ports = [args.port, args.api_port, args.web_port]
    if len(set(ports)) != 3 or any(not 1024 <= port <= 65535 for port in ports):
        parser.error("Use three different unprivileged ports")
    for port in ports:
        with socket.socket() as check:
            check.bind(("127.0.0.1", port))
    repo = Path(__file__).resolve().parents[2]
    node, docker, openssl = (
        shutil.which(name) for name in ("node", "docker", "openssl")
    )
    if not all((node, docker, openssl)):
        parser.error("Node, Docker and openssl are required on the development machine")
    parent = repo / ".runtime/autocad-bridge"
    parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="live-delivery-", dir=parent))
    password, proxy_key = secrets.token_urlsafe(18), secrets.token_urlsafe(40)
    user = "bridge-e2e"
    password_hash = subprocess.check_output(
        [openssl, "passwd", "-apr1", "-stdin"], input=password + "\n", text=True
    ).strip()
    (root / "htpasswd").write_text(f"{user}:{password_hash}\n")
    (root / "htpasswd").chmod(0o644)  # nginx worker reads only the salted hash
    origin = f"http://127.0.0.1:{args.port}"
    config = f"""server {{
    listen 80;
    server_name localhost;
    auth_basic "Green Atlas isolated bridge test";
    auth_basic_user_file /etc/nginx/bridge.htpasswd;
    client_max_body_size 5m;
    add_header Referrer-Policy no-referrer always;
    location ^~ /api/cad-bridge/device/ {{
        auth_basic off;
        proxy_pass http://host.docker.internal:{args.api_port};
        proxy_set_header Host $http_host;
        proxy_set_header Authorization "";
        proxy_set_header X-Green-Atlas-User "";
        proxy_set_header X-Green-Atlas-Proxy-Key "";
        proxy_read_timeout 300s;
    }}
    location /api/ {{
        proxy_pass http://host.docker.internal:{args.api_port};
        proxy_set_header Host $http_host;
        proxy_set_header Authorization "";
        proxy_set_header X-Green-Atlas-User $remote_user;
        proxy_set_header X-Green-Atlas-Proxy-Key "{proxy_key}";
        proxy_read_timeout 300s;
    }}
    location / {{
        proxy_pass http://host.docker.internal:{args.web_port};
        proxy_set_header Host $http_host;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }}
}}
"""
    config_path = root / "nginx.conf"
    config_path.write_text(config)
    config_path.chmod(0o600)
    environment = dict(os.environ)
    environment.update(
        PYTHONPATH=str(repo / "apps/api"),
        GREEN_ATLAS_DB_PATH=str(root / "projects.sqlite3"),
        GREEN_ATLAS_CAD_INTAKE_PATH=str(root / "cad-intake"),
        GREEN_ATLAS_CAD_ROOTS_JSON="{}",
        GREEN_ATLAS_BRIDGE_ORIGIN=origin,
        GREEN_ATLAS_BRIDGE_PROXY_KEY=proxy_key,
        VITE_API_URL="",
    )
    environment.pop("GREEN_ATLAS_DWG_CONVERTER", None)
    children, logs = [], []
    container = "green-atlas-bridge-e2e-" + secrets.token_hex(5)
    container_started = False

    def stop_signal(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_signal)
    signal.signal(signal.SIGINT, stop_signal)
    try:
        for name, command, cwd in (
            (
                "api",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "app.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.api_port),
                ],
                repo,
            ),
            (
                "web",
                [
                    node,
                    "node_modules/vite/bin/vite.js",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(args.web_port),
                    "--strictPort",
                ],
                repo / "apps/web",
            ),
        ):
            log = (root / f"{name}.log").open("w")
            logs.append(log)
            children.append(
                subprocess.Popen(
                    command,
                    cwd=cwd,
                    env=environment,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
            )
        subprocess.run(
            [
                docker,
                "run",
                "--rm",
                "-d",
                "--name",
                container,
                "-p",
                f"127.0.0.1:{args.port}:80",
                "-v",
                f"{config_path}:/etc/nginx/conf.d/default.conf:ro",
                "-v",
                f"{root / 'htpasswd'}:/etc/nginx/bridge.htpasswd:ro",
                "nginx:1.28-alpine",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        container_started = True
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(40):
            if any(child.poll() is not None for child in children):
                raise RuntimeError(f"A test server stopped; see logs in {root}")
            try:
                opener.open(origin + "/api/cad-bridge/device/transfers", timeout=2)
            except urllib.error.HTTPError as error:
                if error.code == 405:  # real FastAPI route, not an auth challenge
                    break
            except OSError:
                pass
            time.sleep(0.25)
        else:
            raise RuntimeError("nginx cannot reach the local API")
        print(f"Runtime: {root}\nContainer: {container}\nService: {origin}", flush=True)
        print(f"Local test login: {user}\nLocal test password: {password}", flush=True)
        print(
            "Ready. No source files were sent. Ctrl+C stops only this harness.",
            flush=True,
        )
        while all(child.poll() is None for child in children):
            time.sleep(1)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if container_started:
            subprocess.run(
                [docker, "stop", "--time", "3", container],
                stdout=subprocess.DEVNULL,
                check=False,
            )
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        for log in logs:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
