from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = Path(__file__).with_name("audit_autocad_native_matrix.py")
SPEC = importlib.util.spec_from_file_location("autocad_native_matrix", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def write_manifest(path: Path, drawings: list[dict]) -> Path:
    path.write_text(
        json.dumps({"schema": MODULE.MANIFEST_SCHEMA, "drawings": drawings}),
        encoding="utf-8",
    )
    return path


def test_runs_sources_sequentially_and_preserves_them(tmp_path: Path) -> None:
    first = tmp_path / "first.dwg"
    second = tmp_path / "second.dxf"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    calls: list[str] = []

    def prepare(arguments):
        calls.append(arguments["source_path"])
        source = Path(arguments["source_path"])
        snapshot = source.with_suffix(".snapshot.json")
        snapshot.write_text("{}", encoding="utf-8")
        return {
            "snapshot_path": str(snapshot),
            "snapshot_sha256": MODULE.sha256(snapshot),
            "snapshot_bytes": snapshot.stat().st_size,
            "native_evidence": {"output_path": str(source) + ".geometry.json"},
            "admission": {"native": 1, "unresolved": 0},
        }

    bridge = SimpleNamespace(
        PLUGIN_VERSION="test",
        bridge_status=lambda: {"ready": True, "plugin_version": "test"},
        prepare_dxf=prepare,
    )
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [
            {"id": "first", "source_path": "first.dwg"},
            {"id": "second", "source_path": "second.dxf"},
        ],
    )
    output = tmp_path / "report.json"

    report = MODULE.run_matrix(manifest, output, bridge=bridge)

    assert calls == [str(first), str(second)]
    assert report["summary"] == {"total": 2, "admitted": 2, "rejected": 0}
    assert all(row["source_unchanged"] for row in report["drawings"])
    assert json.loads(output.read_text()) == report


def test_records_failure_and_continues_matrix(tmp_path: Path) -> None:
    rejected = tmp_path / "rejected.dxf"
    admitted = tmp_path / "admitted.dxf"
    rejected.write_bytes(b"rejected")
    admitted.write_bytes(b"admitted")

    def prepare(arguments):
        source = Path(arguments["source_path"])
        if source == rejected:
            raise RuntimeError("native topology incomplete")
        snapshot = source.with_suffix(".snapshot.json")
        snapshot.write_text("{}", encoding="utf-8")
        return {
            "snapshot_path": str(snapshot),
            "snapshot_sha256": MODULE.sha256(snapshot),
            "snapshot_bytes": snapshot.stat().st_size,
            "native_evidence": {},
            "admission": {"native": 1, "unresolved": 0},
        }

    bridge = SimpleNamespace(
        PLUGIN_VERSION="test",
        bridge_status=lambda: {"ready": True, "plugin_version": "test"},
        prepare_dxf=prepare,
    )
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [
            {"id": "rejected", "source_path": "rejected.dxf"},
            {"id": "admitted", "source_path": "admitted.dxf"},
        ],
    )

    report = MODULE.run_matrix(manifest, tmp_path / "report.json", bridge=bridge)

    assert report["summary"] == {"total": 2, "admitted": 1, "rejected": 1}
    assert report["drawings"][0]["reason"] == "native topology incomplete"
    assert report["drawings"][1]["status"] == "admitted"


def test_rejection_preserves_native_partial_geometry_diagnostics(
    tmp_path: Path,
) -> None:
    source = tmp_path / "partial.dwg"
    source.write_bytes(b"partial")
    details = {
        "admission_status": "rejected",
        "native_probe": {
            "summary": {"source_instances": 10, "native": 8, "unresolved_instances": 2}
        },
    }

    def prepare(_arguments):
        error = RuntimeError("two unresolved objects")
        error.details = details
        raise error

    bridge = SimpleNamespace(
        PLUGIN_VERSION="test",
        bridge_status=lambda: {"ready": True, "plugin_version": "test"},
        prepare_dxf=prepare,
    )
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [{"id": "partial", "source_path": "partial.dwg"}],
    )

    report = MODULE.run_matrix(manifest, tmp_path / "report.json", bridge=bridge)

    row = report["drawings"][0]
    assert row["status"] == "rejected"
    assert row["native_probe"]["summary"]["native"] == 8
    assert row["native_probe"]["summary"]["unresolved_instances"] == 2


def test_rejects_stale_installed_bridge_before_any_source(tmp_path: Path) -> None:
    source = tmp_path / "source.dxf"
    source.write_bytes(b"source")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [{"id": "source", "source_path": "source.dxf"}],
    )
    bridge = SimpleNamespace(
        PLUGIN_VERSION="0.1.21",
        bridge_status=lambda: {"ready": True, "plugin_version": "0.1.19"},
    )

    try:
        MODULE.run_matrix(manifest, tmp_path / "report.json", bridge=bridge)
    except RuntimeError as error:
        assert "differs from runner" in str(error)
    else:
        raise AssertionError("stale bridge was accepted")


def test_discovers_every_drawing_under_a_dataset_root_in_stable_order(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    (dataset / "street-b").mkdir(parents=True)
    (dataset / "street-a").mkdir()
    (dataset / "street-b" / "plan.DXF").write_bytes(b"dxf")
    (dataset / "street-a" / "main.dwg").write_bytes(b"dwg")
    (dataset / "street-a" / "notes.txt").write_text("skip")
    manifest = {
        "schema": MODULE.MANIFEST_SCHEMA,
        "roots": [
            {
                "id_prefix": "lct",
                "path": "dataset",
                "extensions": [".dwg", ".dxf"],
                "timeout_seconds": 900,
            }
        ],
    }
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    loaded, _ = MODULE.load_manifest(path)
    drawings = MODULE.expand_drawings(loaded, tmp_path)

    assert [item["id"] for item in drawings] == [
        "lct:street-a/main.dwg",
        "lct:street-b/plan.DXF",
    ]
    assert all(item["package_root"] == str(dataset) for item in drawings)
    assert all(item["timeout_seconds"] == 900 for item in drawings)


def test_resume_reuses_only_hash_verified_admitted_rows(tmp_path: Path) -> None:
    source = tmp_path / "main.dwg"
    source.write_bytes(b"dwg")
    snapshot = tmp_path / "main.snapshot.json"
    snapshot.write_text("{}")
    calls = 0

    def prepare(arguments):
        nonlocal calls
        calls += 1
        return {
            "snapshot_path": str(snapshot),
            "snapshot_sha256": MODULE.sha256(snapshot),
            "snapshot_bytes": snapshot.stat().st_size,
            "native_evidence": {},
            "admission": {"native": 1, "unresolved": 0},
        }

    bridge = SimpleNamespace(
        PLUGIN_VERSION="test",
        bridge_status=lambda: {"ready": True, "plugin_version": "test"},
        prepare_dxf=prepare,
    )
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [{"id": "main", "source_path": "main.dwg"}],
    )
    output = tmp_path / "report.json"
    MODULE.run_matrix(manifest, output, bridge=bridge)
    resumed = MODULE.run_matrix(manifest, output, bridge=bridge, resume=True)

    assert calls == 1
    assert resumed["drawings"][0]["reused_from_previous_run"] is True
    assert resumed["summary"] == {"total": 1, "admitted": 1, "rejected": 0}
