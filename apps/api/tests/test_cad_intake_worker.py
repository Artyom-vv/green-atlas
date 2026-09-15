import json
import sys
from pathlib import Path

import psutil
import pytest

from app.cad_import.contracts import DrawingInspection
from app.cad_import.package_contracts import (
    PackageDrawing,
    PackageReference,
    SourcePackage,
)
from app.cad_import.policy import ConversionPolicy
from app.cad_import.process import run_converter
from app.cad_intake.contracts import CadIntakeRequest
from app.cad_intake.passport import make_passport
from app.cad_intake.worker import InspectionWork, execute
from app.operations.progress import OperationCancelled


def test_changed_fingerprint_fails_before_converter(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "entry.dxf").write_bytes(b"changed")
    request = CadIntakeRequest(
        root_id="official", entry="entry.dxf", entry_sha256="a" * 64
    )
    with pytest.raises(ValueError, match="изменился"):
        execute(
            InspectionWork(
                root=root,
                cache=tmp_path / "cache",
                converter=tmp_path / "missing.exe",
                output=tmp_path / "manifest.json",
                request=request,
            )
        )
    assert not (tmp_path / "manifest.json").exists()


def test_cancelled_worker_stops_its_process_tree(tmp_path):
    output = tmp_path / "pids.json"
    program = (
        "import json,os,subprocess,sys,time;from pathlib import Path;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]));"
        "time.sleep(30)"
    )
    pids: list[int] = []

    def cancel():
        if output.exists():
            try:
                pids.extend(json.loads(output.read_text()))
            except json.JSONDecodeError:
                return
            raise OperationCancelled("cancel fixture")

    with pytest.raises(OperationCancelled):
        run_converter(
            [sys.executable, "-c", program, str(output)],
            output,
            tmp_path / "worker.log",
            ConversionPolicy(timeout_seconds=8),
            check_cancelled=cancel,
        )
    assert len(pids) == 2
    assert all(not psutil.pid_exists(pid) for pid in pids)


def test_public_passport_redacts_absolute_xrefs_and_worker_diagnostics():
    package = SourcePackage(
        root="D:/private/originals",
        entry="main.dwg",
        drawings=[
            PackageDrawing(
                path="main.dwg",
                source_sha256="a" * 64,
                source_bytes=10,
                normalized_path="D:/private/cache/normalized.dxf",
                evidence_path="D:/private/cache/evidence.json",
                status="readable",
                inspection=DrawingInspection(
                    dxf_version="AC1032",
                    units=6,
                    modelspace_entities={"LINE": 2},
                    layer_names=["0"],
                    xrefs={"BLOCK": "C:\\private\\reference.dwg"},
                ),
            ),
            PackageDrawing(
                path="failed.dwg",
                source_bytes=4,
                status="rejected",
                message="Private failure at D:/private/secret.log",
            ),
        ],
        references=[
            PackageReference(
                owner="main.dwg",
                block="BLOCK",
                requested_path="C:/private/reference.dwg",
                status="missing",
            )
        ],
        status="blocked",
        blockers=["Не прочитан failed.dwg"],
    )
    passport = make_passport(package, "official", "b" * 64, {"main.dwg": "c" * 64})
    assert "private" not in passport.model_dump_json()
    assert passport.drawings[0].normalized_sha256 == "c" * 64
    assert passport.references[0].requested_path == "reference.dwg"
    assert passport.drawings[0].inspection.xrefs["BLOCK"] == "reference.dwg"


def test_discovery_rejects_symlink_leaving_root(tmp_path):
    from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
    from app.cad_intake.paths import CadDiscovery

    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "private.dxf"
    outside.write_bytes(b"private")
    link = root / "link.dxf"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Host has no symlink privilege")
    discovery = CadDiscovery(
        CadIntakeConfig(
            (AllowedCadRoot("official", "Root", root),),
            tmp_path / "cache",
            Path("converter"),
        )
    )
    with pytest.raises(ValueError, match="пределы"):
        discovery.fingerprint("official", "link.dxf")
    assert discovery.directory("official").entries == []
