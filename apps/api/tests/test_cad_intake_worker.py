"""Retained process cancellation, persisted CAD DTO and asset-path guards."""

import json
import sys
from pathlib import Path

import psutil
import pytest

from app.cad_import.archive.package_contracts import (
    PackageDrawing,
    PackageReference,
    SourcePackage,
)
from app.cad_import.contracts import DrawingInspection
from app.cad_import.legacy.process import run_converter
from app.cad_import.policy import ConversionPolicy
from app.cad_intake.assets.passport import make_passport
from app.cad_intake.contracts import CadDrawingEntry, CadIntakeRequest
from app.cad_intake.inspection.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.inspection.paths import CadDiscovery
from app.operations.progress import OperationCancelled


def test_saved_request_rejects_duplicate_independent_entries() -> None:
    with pytest.raises(ValueError, match="выбирается один раз"):
        CadIntakeRequest(
            root_id="official",
            entry="entry.dxf",
            entry_sha256="a" * 64,
            additional_entries=[CadDrawingEntry(path="entry.dxf", sha256="a" * 64)],
        )


def test_cancelled_local_process_stops_its_process_tree(tmp_path: Path) -> None:
    output = tmp_path / "pids.json"
    program = (
        "import json,os,subprocess,sys,time;from pathlib import Path;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "Path(sys.argv[1]).write_text(json.dumps([os.getpid(),child.pid]));"
        "time.sleep(30)"
    )
    pids: list[int] = []

    def cancel() -> None:
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


def test_public_passport_redacts_absolute_xrefs_and_worker_diagnostics() -> None:
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
    inspection = passport.drawings[0].inspection
    assert inspection is not None and inspection.xrefs["BLOCK"] == "reference.dwg"


@pytest.fixture
def discovery(tmp_path: Path) -> CadDiscovery:
    root = tmp_path / "root"
    root.mkdir()
    return CadDiscovery(
        CadIntakeConfig(
            (AllowedCadRoot("official", "Root", root),),
            tmp_path / "cache",
            None,
        )
    )


@pytest.mark.parametrize(
    "case,message",
    [
        ("outside_symlink", "пределы"),
        ("traversal", "относительный"),
        ("absolute", "относительный"),
        ("wrong_extension", "DWG или DXF"),
        ("missing", "недоступен"),
        ("directory", "DWG или DXF"),
    ],
)
def test_discovery_resolve_rejects_unsafe_or_unavailable_drawings(
    tmp_path: Path, discovery: CadDiscovery, case: str, message: str
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "private.dxf"
    outside.write_bytes(b"private")
    path = "missing.dxf"
    if case == "outside_symlink":
        path = "link.dxf"
        try:
            (root / path).symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("Host has no symlink privilege")
    elif case == "traversal":
        path = "../private.dxf"
    elif case == "absolute":
        path = str(outside)
    elif case == "wrong_extension":
        path = "drawing.txt"
        (root / path).write_bytes(b"not CAD")
    elif case == "directory":
        path = "directory.dwg"
        (root / path).mkdir()
    with pytest.raises(ValueError, match=message):
        discovery.resolve("official", path, drawing=True)


@pytest.mark.parametrize("path", ["nested/plan.DWG", "nested/plan.dxf"])
def test_discovery_resolve_accepts_in_root_drawings(
    tmp_path: Path, discovery: CadDiscovery, path: str
) -> None:
    source = tmp_path / "root" / path
    source.parent.mkdir()
    source.write_bytes(b"synthetic drawing")
    assert discovery.resolve("official", path, drawing=True) == source.resolve()
