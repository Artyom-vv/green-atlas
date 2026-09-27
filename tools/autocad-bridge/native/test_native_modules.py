"""Native module boundaries and executable snapshot tests, not a CAD qualification."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parent
GEOMETRY_MODULES = (
    "curve_sampling",
    "curve_extraction",
    "region_extraction",
    "hatch_extraction",
    "hatch_loop_roles",
    "geometry_math",
    "entity_extraction",
    "direct_query_kernel",
    "native_affine_query",
    "native_area_group",
    "native_area_candidates",
    "native_curve_query",
    "native_query_batch",
    "xref_instance_access",
)


def test_native_files_have_bounded_responsibilities():
    sources = [p for p in NATIVE.iterdir() if p.suffix in {".cpp", ".h", ".mm"}]
    for path in sources:
        text = path.read_text()
        assert len(text.splitlines()) <= 500, path.name
        assert not re.search(r'#include\s+"[^"\n]+\.(?:cpp|inc)"', text), path.name
    entry = (NATIVE / "green_atlas_bridge.cpp").read_text()
    assert "acrxEntryPoint" in entry
    assert "getRegionArea" not in entry
    assert "std::ofstream" not in entry
    assert "collectRegionInstances" not in entry


def test_geometry_does_not_depend_on_transport_or_delivery():
    for module in GEOMETRY_MODULES:
        text = (NATIVE / f"{module}.cpp").read_text()
        for forbidden in (
            "mcp_transport.h",
            "delivery_command.h",
            "delivery_ui.h",
            "mcpDirectory",
            "gLastTopologyExport",
            "std::ofstream",
        ):
            assert forbidden not in text, (module, forbidden)


def test_every_native_translation_unit_is_in_the_build():
    build = (NATIVE.parent / "build-macos.sh").read_text()
    for path in NATIVE.glob("*.cpp"):
        if not path.name.startswith("test_"):
            assert f'"$source_root/{path.name}"' in build, path.name


def test_worker_and_diagnostics_share_the_product_query_kernel():
    worker = (NATIVE.parent / "build-query-worker-macos.sh").read_text()
    diagnostics = (NATIVE.parent / "diagnostics/build-macos.sh").read_text()
    for module in ("direct_query_kernel", "native_affine_query", "native_curve_query",
                   "native_area_group", "native_area_candidates"):
        assert f'"$source_root/{module}.cpp"' in worker
        assert f'/native/{module}.cpp"' in diagnostics
        assert not (NATIVE.parent / "diagnostics" / f"{module}.cpp").exists()
    entry = (NATIVE.parent / "query-worker/entry.cpp").read_text()
    assert "queryObjectsCommand" in entry
    assert "mcp_transport" not in entry
    assert "installMenu" not in entry


@pytest.fixture(scope="module")
def runtime(tmp_path_factory):
    if sys.platform != "darwin":
        pytest.skip("The production file hash helper uses macOS CommonCrypto")
    executable = tmp_path_factory.mktemp("native-module-runtime") / "check"
    sources = [
        "test_snapshot_runtime.cpp",
        "snapshot_writer.cpp",
        "operation_control.cpp",
        "geometry_math.cpp",
        "file_io.cpp",
    ]
    subprocess.run(
        [
            "xcrun",
            "clang++",
            "-std=c++17",
            "-Wno-deprecated-declarations",
            *[str(NATIVE / name) for name in sources],
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return executable


def run_capture(runtime, source, cancel_at=0, mode="file", destination=None):
    result = subprocess.run(
        [str(runtime), str(source), str(cancel_at), mode,
         *([str(destination)] if destination else [])],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_hatch_loop_roles_are_bounded_and_style_aware(tmp_path):
    executable = tmp_path / "hatch-loop-roles"
    subprocess.run(
        [
            "xcrun",
            "clang++",
            "-std=c++17",
            str(NATIVE / "test_hatch_loop_roles.cpp"),
            str(NATIVE / "hatch_loop_roles.cpp"),
            str(NATIVE / "geometry_math.cpp"),
            "-o",
            str(executable),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run([str(executable)], check=True, capture_output=True, text=True)


@pytest.mark.parametrize(
    "mode,modified,capture",
    [
        ("file", 0, "side_database_dxf"),
        ("live", 32, "live_document"),
    ],
)
def test_snapshot_keeps_geometry_provenance_and_partial_coverage(
    runtime, tmp_path, mode, modified, capture
):
    source = tmp_path / 'Чертёж "1".dwg'
    result = run_capture(runtime, source, mode=mode)
    assert result["success"] and result["error"] == ""
    snapshot = json.loads(Path(result["path"]).read_text())
    assert snapshot["capture_mode"] == capture
    assert snapshot["source"]["path"] == str(source)
    assert snapshot["source"]["sha256"] == "a" * 64
    assert snapshot["source"]["database_modified_flags"] == modified
    assert snapshot["source"]["live_database_matches_disk"] is (modified == 0)
    assert snapshot["regions"][0]["layer"] == 'Подоснова|Здание"\\\n'
    assert snapshot["regions"][0]["instance_chain"] == ["B1", "MINSERT:B2:R1:C2"]
    assert [loop["role"] for loop in snapshot["regions"][0]["loops"]] == [
        "outer",
        "hole",
    ]
    assert snapshot["regions"][0]["native_area_units2"] == 11
    assert snapshot["paths"][0]["coordinates"][0][0] == 0.12345678901234567
    assert snapshot["points"][0]["coordinates"] == [2, 3, 4]
    assert snapshot["coverage"][4]["status"] == "unresolved"
    assert snapshot["coverage"][4]["reason"] == "test unresolved"
    assert snapshot["coverage"][5]["xref_dependency_id"] == "xref/X1"
    assert snapshot["xref_dependencies"][0]["sha256"] == "b" * 64
    summary = snapshot["summary"]
    assert (
        summary["source_instances"],
        summary["native"],
        summary["context"],
        summary["unresolved_instances"],
    ) == (6, 3, 2, 1)
    assert (summary["loops"], summary["region_sampled_points"]) == (2, 10)
    assert not Path(result["path"] + ".tmp").exists()


@pytest.mark.parametrize("cancel_at", range(1, 8))
def test_cancel_during_each_payload_section_preserves_previous_snapshot(
    runtime, tmp_path, cancel_at
):
    source = tmp_path / "drawing.dwg"
    destination = Path(str(source) + ".green-atlas.geometry.json")
    destination.write_text("previous snapshot")
    result = run_capture(runtime, source, cancel_at)
    assert result == {
        "success": False,
        "path": "",
        "error": "native topology export cancelled",
    }
    assert destination.read_text() == "previous snapshot"
    assert not Path(str(destination) + ".tmp").exists()


def test_writer_reports_publication_failure(runtime, tmp_path):
    result = run_capture(runtime, tmp_path / "absent" / "drawing.dwg")
    assert result == {
        "success": False,
        "path": "",
        "error": "topology sidecar cannot be created",
    }


def test_live_writer_stages_capture_without_adjacent_sidecar(runtime, tmp_path):
    source = tmp_path / "real-source.dwg"
    source.write_bytes(b"unchanged source")
    destination = tmp_path / "private-transfer" / "Drawing.autocad.json"
    destination.parent.mkdir()
    result = run_capture(runtime, source, mode="live", destination=destination)
    assert result["success"] and result["path"] == str(destination)
    assert json.loads(destination.read_text())["source"]["path"] == str(source)
    assert source.read_bytes() == b"unchanged source"
    assert not Path(str(source) + ".green-atlas.geometry.json").exists()
