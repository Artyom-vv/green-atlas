"""Real standalone native executable against a loopback protocol simulator."""
import hashlib
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]
BINARY = Path(os.environ.get("GREEN_ATLAS_TEST_CONNECTOR_BINARY", str(
    ROOT / ".runtime/autocad-bridge/connector/Green Atlas Connect.app/Contents/MacOS/GreenAtlasConnect"
)))


@pytest.fixture
def ticket(tmp_path):
    directory = tmp_path.resolve()
    files = {"Street.dxf": b"drawing bytes", "Street.dxf.green-atlas.geometry.json": b"native probe"}
    manifest = []
    for name, content in files.items():
        (directory / name).write_bytes(content)
        manifest.append({"name": name, "kind": "drawing" if name.endswith(".dxf") else "native_probe",
                         "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    path = directory / "transfer.gatransfer"
    path.write_text(json.dumps({"schema": "green-atlas.transfer/1", "plugin_version": "0.1.21",
        "producer": {"autocad_version": "2027.0.1", "target": "macos-arm64"},
        "manifest": {"entry": "Street.dxf", "files": manifest}}))
    return path


@pytest.fixture
def server():
    state = {"received": {}, "requests": [], "put_count": 0, "fail_once": False, "redirect": False}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def respond(self, code, body):
            encoded = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        def progress(self):
            return {"status": "uploading", "chunk_bytes": 4 * 1024**2, "files": [
                {"index": i, "name": f["name"], "bytes": f["bytes"], "received_bytes": len(state["received"].get(i, b""))}
                for i, f in enumerate(state["manifest"]["files"])]}
        def do_GET(self):
            state["requests"].append(self.path)
            if state["redirect"]:
                self.send_response(307)
                self.send_header("Location", origin + "/leak")
                self.send_header("Content-Length", "0")
                self.end_headers()
            elif self.path.endswith("/publication"):
                self.respond(200, state["published"]) if "published" in state else self.respond(404, {})
            elif self.path.endswith("/upload"):
                self.respond(200, self.progress()) if state.get("started") else self.respond(404, {})
            else:
                self.respond(404, {})
        def do_POST(self):
            state["requests"].append(self.path)
            data = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if self.path.endswith("/transfers"):
                body = json.loads(data)
                state.update(id=body["request_id"], secret=body["client_secret"], manifest=body["manifest"])
                self.respond(201, {"status": "approved", "verification_url": origin + "/connect/autocad/" + state["id"]})
            elif self.path.endswith("/upload"):
                state["started"] = True
                self.respond(200, self.progress())
            elif self.path.endswith("/finish"):
                for i, f in enumerate(state["manifest"]["files"]):
                    assert hashlib.sha256(state["received"][i]).hexdigest() == f["sha256"]
                self.respond(200, {"status": "ready"})
            elif self.path.endswith("/publication"):
                project = str(uuid4())
                state["published"] = {"status": "needs_review", "project_id": project,
                    "project_path": f"/projects/{project}/import?source=cad"}
                self.respond(202, state["published"])
            else:
                self.respond(404, {})
        def do_PUT(self):
            state["requests"].append(self.path)
            assert self.headers["X-Green-Atlas-Transfer-Token"] == state["secret"]
            data = self.rfile.read(int(self.headers["Content-Length"]))
            assert hashlib.sha256(data).hexdigest() == self.headers["X-Chunk-SHA256"]
            index = int(self.path.split("/files/")[1].split("?")[0])
            offset = int(self.path.split("offset=")[1])
            assert offset == len(state["received"].get(index, b""))
            state["received"][index] = state["received"].get(index, b"") + data
            state["put_count"] += 1
            if state["fail_once"]:
                state["fail_once"] = False
                self.respond(503, {"message": "simulated lost acknowledgement"})
            else:
                self.respond(200, self.progress())
    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    origin = f"http://127.0.0.1:{instance.server_port}"
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    yield origin, state
    instance.shutdown()
    instance.server_close()
    thread.join()


def run(ticket, origin):
    assert BINARY.is_file(), "Run build-connector-macos.sh first"
    return subprocess.run([str(BINARY), "--headless", str(ticket), origin], capture_output=True, text=True, timeout=30)


def test_native_executable_resumes_after_lost_ack_without_duplicate(ticket, server):
    origin, state = server
    state["fail_once"] = True
    first = run(ticket, origin)
    assert first.returncode == 1
    assert state["put_count"] == 1
    second = run(ticket, origin)
    assert second.returncode == 0, second.stderr
    assert state["put_count"] == 2
    third = run(ticket, origin)
    assert third.returncode == 0
    assert second.stdout == third.stdout
    assert state["put_count"] == 2
    saved = Path(str(ticket) + ".state.json")
    assert saved.stat().st_mode & 0o777 == 0o600
    assert state["secret"] not in first.stdout + first.stderr + second.stdout + second.stderr + str(state["requests"])


def test_redirect_never_forwarded(ticket, server):
    origin, state = server
    state["redirect"] = True
    result = run(ticket, origin)
    assert result.returncode == 1
    assert "/leak" not in state["requests"]
    assert len(state["requests"]) == 1


def test_changed_file_not_sent(ticket, server):
    origin, state = server
    (ticket.parent / "Street.dxf").write_bytes(b"changed")
    assert run(ticket, origin).returncode == 1
    assert state["requests"] == []


def test_ticket_change_cannot_return_old_project(ticket, server):
    origin, state = server
    assert run(ticket, origin).returncode == 0
    before = len(state["requests"])
    body = json.loads(ticket.read_text())
    body["producer"]["autocad_version"] = "2027.1"
    ticket.write_text(json.dumps(body))
    assert run(ticket, origin).returncode == 1
    assert len(state["requests"]) == before
