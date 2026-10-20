"""Meaningful refusal controls for experimental file intake."""

import json
from pathlib import Path

import pytest
from capture_session_run import build
from core_intake_policy import assess_input
from core_intake_run import run_stage, stage_package


def source_graph(status: int, present: bool) -> dict:
    return {
        "database": {"units": 6},
        "graph": {
            "nodes": [
                {"name": "host"},
                {
                    "name": "NETWORK",
                    "status": status,
                    "database_present": present,
                    "authored_path": "../network.dwg",
                    "read_substatus": 0,
                },
            ]
        },
        "entities": [{"class": "AcDbLine"}],
    }


def test_missing_xref_never_means_complete() -> None:
    result = assess_input(source_graph(4, False), {"objects": []})
    assert result["state"] == "needs_source_review"
    assert result["issues"][0]["kind"] == "missing_xref"
    assert result["issues"][0]["path"] == "../network.dwg"


def test_unloaded_is_not_missing_or_silently_complete() -> None:
    result = assess_input(source_graph(2, False), {"objects": []})
    assert result["state"] == "needs_source_review"
    assert not result["issues"]
    assert result["unloaded_xrefs"] == ["NETWORK"]


def test_resolved_status_without_database_is_refused() -> None:
    assert assess_input(source_graph(1, False), {"objects": []})["issues"]


def test_proxy_graphics_do_not_certify_geometry() -> None:
    before = source_graph(1, True)
    before["entities"] = [{"class": "AcDbProxyEntity"}]
    assert assess_input(before, {"objects": []})["state"] == "needs_source_review"


def test_no_invented_unit_conversion() -> None:
    before = source_graph(1, True)
    before["database"]["units"] = 0
    assert {"kind": "unknown_units"} in assess_input(before, {"objects": []})["issues"]


def test_unreadable_calculation_object_keeps_route() -> None:
    inventory = {
        "objects": [{"route": "A/B", "error": "native failure", "context": False}]
    }
    assert assess_input(source_graph(1, True), inventory)["issues"][0]["route"] == "A/B"


def test_preserves_nested_duplicate_basenames(tmp_path: Path) -> None:
    package = tmp_path / "original"
    for folder, content in (("a", b"first"), ("b", b"second")):
        directory = package / folder
        directory.mkdir(parents=True)
        (directory / "network.dwg").write_bytes(content)
    source = package / "host.dwg"
    source.write_bytes(b"host")
    destination = tmp_path / "private"
    records = stage_package(source, package, destination)
    assert len(records) == 3
    assert (destination / "a/network.dwg").read_bytes() == b"first"
    assert (destination / "b/network.dwg").read_bytes() == b"second"
    assert source.read_bytes() == b"host"
    json.dumps(records)


def test_output_inside_package_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "host.dwg"
    source.write_bytes(b"host")
    with pytest.raises(ValueError, match="outside"):
        stage_package(source, tmp_path, tmp_path / "output")


def test_symlink_outside_package_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "host.dwg"
    source.write_bytes(b"host")
    (tmp_path / "external.dwg").symlink_to("/does/not/exist")
    with pytest.raises(ValueError, match="symlink"):
        stage_package(source, tmp_path, tmp_path.parent / "private")


def test_unsupported_architecture_refused_before_build(tmp_path: Path) -> None:
    root = tmp_path / "probe"
    with pytest.raises(ValueError, match="architecture"):
        build(root, architecture="ppc")
    assert not root.exists()


def test_unsupported_architecture_refused_before_core(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="architecture"):
        run_stage(
            tmp_path,
            tmp_path / "probe",
            tmp_path / "library",
            "capture",
            None,
            1,
            "complete",
            architecture="ppc",
        )
    assert not (tmp_path / "capture").exists()
