"""Общий query pipeline через Windows files/job facade на синтетических границах."""

import ast
import json
from dataclasses import replace
from hashlib import sha256

import pytest
from test_native_query_process import setup_job
from test_windows_job_files import _FakeApi
from test_windows_process import FakeNative

from app.native_query.transport import job_files, process, windows_job_files
from app.shared.windows_process import launch_owned


@pytest.mark.parametrize("damage", [None, "lifecycle", "source", "staged_worker", "membership", "resume"])
def test_windows_query_uses_same_sha_and_lifecycle_admission(tmp_path, monkeypatch, damage):
    package, query, config = setup_job(tmp_path)
    parsed = ast.parse(config.core_executable.read_text())
    options = next(
        ast.literal_eval(item.value)
        for item in parsed.body
        if isinstance(item, ast.Assign)
        and isinstance(item.targets[0], ast.Name)
        and item.targets[0].id == "OPTIONS"
    )
    worker = tmp_path / "Работник CRX"
    worker.mkdir()
    binary = worker / "GreenAtlasQuery.crx"
    binary.write_bytes(b"synthetic Windows worker")
    digest = sha256(binary.read_bytes()).hexdigest()
    (worker / "worker.json").write_text(json.dumps({
        "schema": "green-atlas.query-worker/1",
        "target": "windows-x86_64",
        "binary": "GreenAtlasQuery.crx",
        "plugin_version": config.plugin_version,
        "sha256": digest,
    }))
    config = replace(
        config, worker_bundle=worker, worker_binary_sha256=digest, architecture="x86_64"
    )
    files = _FakeApi()
    monkeypatch.setattr(windows_job_files, "_file_api", lambda: files)
    monkeypatch.setattr(job_files, "_windows_platform", lambda: True)
    monkeypatch.setattr(process, "_windows_platform", lambda: True)
    natives = []

    def launch(command, *, cwd):
        class Core(FakeNative):
            def resume(self, thread):
                super().resume(thread)
                raw = (cwd / "request.txt").read_bytes()
                reply = dict(options["reply"], request_sha256=sha256(raw).hexdigest())
                output = json.dumps(reply).encode()
                (cwd / "native.json").write_bytes(output)
                lifecycle = {
                    "schema": "green-atlas.native-query-lifecycle/1",
                    "request_sha256": sha256(raw).hexdigest(),
                    "source_sha256": query.source_sha256,
                    "reply_sha256": sha256(output).hexdigest(),
                    "database_destroyed": True,
                    "working_database_restored": True,
                    "field_evaluation_restored": damage != "lifecycle",
                }
                (cwd / "lifecycle.json").write_text(json.dumps(lifecycle))
                if damage == "source":
                    (package.root / package.entry).write_bytes(b"mutated source fixture")
                elif damage == "staged_worker":
                    (cwd / "QueryWorker/GreenAtlasQuery.crx").write_bytes(b"changed CRX")
                self.exit_code = 0
                self.active = 0

        native = Core("not-member" if damage == "membership" else "resume" if damage == "resume" else "")
        natives.append(native)
        return launch_owned(command, cwd=cwd, native=native)

    monkeypatch.setattr(process, "launch_owned", launch)
    result = process.run_native_query(package, query, config)
    assert not files.handles
    assert len(natives) == 1 and sorted(natives[0].closed) == [11, 12, 13, 14, 15, 16]
    receipt = result.receipt
    assert receipt.command[0] == str(config.core_executable)
    assert "/usr/bin/arch" not in receipt.command
    assert receipt.source_verified_before and receipt.staged_verified_before
    job = receipt.job_directory
    assert job is not None
    script = (job / "query.scr").read_text()
    assert "QueryWorker/GreenAtlasQuery.crx" in script and "SECURELOAD" not in script
    if damage is None:
        assert result.failure is None and result.reply is not None
        assert receipt.lifecycle_verified and receipt.exit_code == 0
        assert receipt.source_verified_after and receipt.staged_verified_after
        assert receipt.signals == ()
    else:
        assert result.reply is None and result.failure is not None
        assert result.failure.code == (
            "launch_failed" if damage in {"membership", "resume"}
            else "integrity_failure" if damage == "source" else "invalid_reply"
        )
