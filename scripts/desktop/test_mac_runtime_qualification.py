"""Границы изолированного frozen smoke и снимков без чтения пользовательских БД."""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import mac_runtime_qualification as qa
import pytest


def test_live_snapshot_accepts_bounded_large_inventory(tmp_path):
    snapshot = tmp_path / "live.json"
    expected = {
        "paths": {
            "workspace": {
                str(number): {"bytes": number, "mtime_ns": 123456789}
                for number in range(2000)
            }
        }
    }
    snapshot.write_text(json.dumps(expected))
    assert snapshot.stat().st_size > 65536
    assert qa.read_object(snapshot, maximum=64 * 1024**2) == expected
    with pytest.raises(ValueError, match="предел чтения"):
        qa.read_object(snapshot)


def qa_root(tmp_path: Path, phase: str = "installed") -> Path:
    session = str(uuid.uuid4()).upper()
    root = tmp_path / ("GA-Mac-Qualification-" + session)
    root.mkdir(mode=0o700)
    marker = root / qa.MARKER
    marker.write_text(
        json.dumps(
            {
                "schema": qa.SCHEMA,
                "uid": os.getuid(),
                "session_uuid": session,
                "phase": phase,
                "backup": None,
            }
        )
    )
    marker.chmod(0o600)
    return root


def installed_package(root: Path) -> Path:
    package = root / "ApplicationAddins/GreenAtlasBridge.bundle"
    resources = package / "Contents/Applications/Green Atlas.app/Contents/Resources"
    (resources / "Runtime").mkdir(parents=True)
    (resources / "Web").mkdir()
    (resources / "Web/index.html").write_bytes(b'<script src="/app.js"></script>')
    (resources / "Web/app.js").write_bytes(b"console.log('compiled fixture')")
    worker = (
        package
        / "Contents/Workers/GreenAtlasQuery.bundle/Contents/MacOS/GreenAtlasBridge"
    )
    worker.parent.mkdir(parents=True)
    worker.write_bytes(b"synthetic worker; never executed")
    runtime = resources / "Runtime/GreenAtlasRuntime"
    runtime.write_text(
        "#!"
        + sys.executable
        + "\n"
        + r"""
import argparse,json,os,sqlite3,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
p=argparse.ArgumentParser()
for n in ('data-dir','web-dir','ticket-root','cad-worker'):p.add_argument('--'+n,required=True)
a=p.parse_args()
assert os.environ['PATH']=='/usr/bin:/bin:/usr/sbin:/sbin'
assert not any(k in os.environ for k in ('HOME','CFFIXED_USER_HOME','PYTHONPATH','DYLD_LIBRARY_PATH'))
data=Path(a.data_dir);data.mkdir();sqlite3.connect(data/'projects.sqlite3').close()
secret='q'*43
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*a):pass
 def do_GET(self):self.handle_request()
 def do_POST(self):self.handle_request()
 def handle_request(self):
  if self.headers.get('x-green-atlas-local-session')!=secret:status,body=403,b'{}'
  elif self.path=='/_desktop/health':status,body=200,json.dumps({'service':'green-atlas-desktop','protocol':1}).encode()
  elif self.path=='/api/projects':status,body=200,b'[]'
  elif self.path=='/_desktop/shutdown':status,body=202,b'{"state":"stopping"}'
  else:status,body=200,(Path(a.web_dir)/('index.html' if self.path=='/' else self.path[1:])).read_bytes()
  self.send_response(status);self.end_headers();self.wfile.write(body);self.wfile.flush()
  if self.path=='/_desktop/shutdown':threading.Thread(target=server.shutdown).start()
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
print(json.dumps({'service':'green-atlas-desktop','protocol':1,'origin':'http://127.0.0.1:'+str(server.server_port),'session_token':secret}),flush=True)
server.serve_forever();server.server_close()
"""
    )
    runtime.chmod(0o700)
    return package


def test_actual_owned_process_http_web_and_graceful_shutdown(tmp_path, monkeypatch):
    root = qa_root(tmp_path)
    package = installed_package(root)
    monkeypatch.setenv("PYTHONPATH", "private-developer-modules")
    monkeypatch.setenv("GREEN_ATLAS_PRIVATE", "must not propagate")
    monkeypatch.setenv("DYLD_LIBRARY_PATH", "private dylibs")
    output = root / "frozen-api.json"
    report = qa.frozen_api(package, root / "FrozenProfile", output)
    assert report["passed"] and report["shutdown_202_exit_0"]
    assert report["web"]["resources_checked"] == 1
    assert report["database_sha256"] and "session_token" not in output.read_text()
    assert "http://127.0.0.1" not in output.read_text()
    assert (root / "FrozenProfile").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "kind",
    ["existing", "symlink", "live-package", "bad-phase", "marker-mode", "marker-link"],
)
def test_unsafe_inputs_fail_before_profile_creation(tmp_path, kind):
    root = qa_root(tmp_path)
    package = installed_package(root)
    profile = root / "FrozenProfile"
    if kind == "existing":
        profile.mkdir()
    elif kind == "symlink":
        profile.symlink_to(tmp_path)
    elif kind == "live-package":
        package = tmp_path / "LiveInstalled.bundle"
    elif kind == "bad-phase":
        marker = root / qa.MARKER
        value = json.loads(marker.read_text())
        value["phase"] = "init"
        marker.write_text(json.dumps(value))
    elif kind == "marker-mode":
        (root / qa.MARKER).chmod(0o644)
    elif kind == "marker-link":
        marker = root / qa.MARKER
        target = root / "private-marker"
        marker.rename(target)
        marker.symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        qa.frozen_api(package, profile, root / "report.json")
    assert not (root / "report.json").exists()
    if kind not in {"existing", "symlink"}:
        assert not profile.exists()


def test_failure_diagnostic_hides_ready_and_secrets_and_stops_child(tmp_path):
    root = qa_root(tmp_path)
    package = installed_package(root)
    runtime = (
        package
        / "Contents/Applications/Green Atlas.app/Contents/Resources/Runtime/GreenAtlasRuntime"
    )
    runtime.write_text(
        "#!"
        + sys.executable
        + "\nprint('private-secret-'+'a'*80)\nprint('useful startup failure')\nraise SystemExit(3)\n"
    )
    with pytest.raises(RuntimeError, match="до готовности"):
        qa.frozen_api(package, root / "FrozenProfile", root / "failed.json")
    diagnostic = (root / "failed.startup.log").read_text()
    assert "useful startup failure" in diagnostic and "a" * 80 not in diagnostic
    assert json.loads((root / "failed.json").read_text())["passed"] is False


def test_live_workspace_snapshot_reads_only_metadata(tmp_path, monkeypatch):
    home = tmp_path / "home"
    db = home / "Library/Application Support/Green Atlas/Workspace/projects.sqlite3"
    db.parent.mkdir(parents=True)
    db.write_bytes(b"user database must never be opened")
    monkeypatch.setattr(qa.pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(home)))
    monkeypatch.setattr(
        qa, "file_hash", lambda _: pytest.fail("Live DB content opened")
    )
    first = qa.snapshot_live()
    db.write_bytes(b"changed user metadata")
    assert qa.snapshot_live() != first
    assert first["workspace_database_content_read"] is False


def test_live_binary_hash_detects_change_but_does_not_follow_parent_alias(tmp_path):
    package = tmp_path / "package"
    package.mkdir()
    binary = package / "binary"
    binary.write_bytes(b"one")
    first = qa.tree_snapshot(package, hashes=True)
    binary.write_bytes(b"two")
    assert (
        qa.tree_snapshot(package, hashes=True)["binary"]["sha256"]
        != first["binary"]["sha256"]
    )
    alias = tmp_path / "alias"
    alias.symlink_to(package)
    assert "ancestor_symlink" in qa.tree_snapshot(alias / "binary", hashes=True)["."]


def test_report_cannot_replace_existing_or_escape_root(tmp_path):
    root = qa_root(tmp_path)
    existing = root / "report.json"
    existing.write_bytes(b"keep")
    for output in (existing, tmp_path / "outside.json"):
        with pytest.raises(ValueError):
            qa.new_output(root, output)
    assert existing.read_bytes() == b"keep"


def test_owned_child_cleanup_terminates_only_its_new_session(tmp_path):
    root = qa_root(tmp_path)
    profile = root / "FrozenProfile"
    profile.mkdir(mode=0o700)
    (profile / "Temp").mkdir(mode=0o700)
    child = qa.OwnedPosixPopen(
        [sys.executable, "-c", "import time;time.sleep(30)"], profile
    )
    assert os.getpgid(child.process.pid) == child.process.pid
    assert os.getpgid(child.process.pid) != os.getpgrp()
    child.close()
    assert child.poll() is not None and child.active_processes() == 0
