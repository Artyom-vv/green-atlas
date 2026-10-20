"""CAD-выпуск: Windows Job cleanup разрешён только проверенному результату."""

import json
from dataclasses import replace
from hashlib import sha256

import pytest
from test_cad_release_process import setup
from test_windows_job_files import _FakeApi
from test_windows_process import FakeNative

from app.exporting.cad_contracts import CadPlant
from app.exporting.cad_process import write_cad_release
from app.native_query.transport import job_files, process, windows_job_files
from app.shared.windows_process import launch_owned


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "receipt",
        "tmp",
        "source",
        "request",
        "xref",
        "result-after-stop",
        "worker-after-stop",
        "exit",
        "stuck",
    ],
)
def test_release_cleanup_requires_verified_result_before_and_after_owned_stop(
    tmp_path, monkeypatch, damage
):
    package, config = setup(tmp_path, "ok")
    worker = tmp_path / "Windows worker"
    worker.mkdir()
    binary = worker / "GreenAtlasQuery.crx"
    binary.write_bytes(b"trusted synthetic Windows worker")
    digest = sha256(binary.read_bytes()).hexdigest()
    (worker / "worker.json").write_text(
        json.dumps(
            {
                "schema": "green-atlas.query-worker/1",
                "target": "windows-x86_64",
                "binary": binary.name,
                "plugin_version": config.plugin_version,
                "sha256": digest,
            }
        )
    )
    config = replace(
        config,
        worker_bundle=worker,
        worker_binary_sha256=digest,
        architecture="x86_64",
        terminate_grace_seconds=0.005,
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
                target = cwd / "result/planting-plan.dwg"
                target.write_bytes(b"verified DWG result")
                receipt = {
                    "schema": "green-atlas.cad-release/1",
                    "request_sha256": sha256(raw).hexdigest(),
                    "source_sha256": sha256(
                        (cwd / "package/host.dwg").read_bytes()
                    ).hexdigest(),
                    "result_sha256": sha256(target.read_bytes()).hexdigest(),
                    "source_instances": 10,
                    "result_instances": 11,
                    "units_code": 6,
                    "reopened": True,
                    "plantings": [
                        {
                            "id": "aa",
                            "kind": "shrub",
                            "x": 1,
                            "y": 2,
                            "radius": 0.5,
                            "handle": "AB",
                        }
                    ],
                }
                if damage == "receipt":
                    receipt["reopened"] = False
                (cwd / "release.json").write_text(json.dumps(receipt))
                if damage == "tmp":
                    (cwd / "release.json.tmp").write_text("{}")
                if damage == "source":
                    (cwd / "package/host.dwg").write_bytes(b"changed")
                if damage == "request":
                    (cwd / "request.txt").write_bytes(b"changed")
                if damage == "xref":
                    (cwd / "result/xref-1.dwg").write_bytes(b"changed")
                self.exit_code = 42 if damage == "exit" else 0
                self.active = 1
                self.output = b"bounded synthetic Core log"

            def terminate_job(self, job):
                if damage == "stuck":
                    self.event("terminate", job)
                    return
                super().terminate_job(job)
                if damage == "result-after-stop":
                    (cwd / "result/planting-plan.dwg").write_bytes(
                        b"mutated after cleanup"
                    )
                if damage == "worker-after-stop":
                    (cwd / "QueryWorker/GreenAtlasQuery.crx").write_bytes(
                        b"changed CRX"
                    )

        native = Core()
        natives.append(native)
        return launch_owned(command, cwd=cwd, native=native)

    monkeypatch.setattr(process, "launch_owned", launch)
    plants = (CadPlant(id="aa", kind="shrub", x=1, y=2, radius=0.5),)
    if damage is None:
        result = write_cad_release(package, plants, 6, config)
        assert result.files["planting-plan.dwg"] == b"verified DWG result"
        jobs = list(config.job_root.glob("ga-cad-release-*"))
        evidence = json.loads((jobs[0] / "process.json").read_bytes())
        assert evidence["signals"] == ["TerminateJobObject"]
        assert evidence["completed_cleanup_verified"] is True
        assert evidence["state"] == "completed"
    else:
        with pytest.raises((OSError, ValueError)):
            write_cad_release(package, plants, 6, config)
        jobs = list(config.job_root.glob("ga-cad-release-*"))
        evidence = json.loads((jobs[0] / "process.json").read_bytes())
        assert evidence["state"] == "failed"
        assert evidence["failure"] is not None
        expected_stops = 2 if damage == "stuck" else 1
        assert evidence["signals"] == ["TerminateJobObject"] * expected_stops
        assert evidence["completed_cleanup_verified"] is False
    assert (package.root / "host.dwg").read_bytes() == b"host.dwg"
    assert natives and any(event[0] == "terminate" for event in natives[0].events)
    assert not files.handles
