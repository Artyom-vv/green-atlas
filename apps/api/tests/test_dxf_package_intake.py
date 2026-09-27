"""Native DXF intake must not depend on a DWG executable or change originals."""

from hashlib import sha256
from pathlib import Path

import ezdxf
import pytest

from app.cad_bridge import CadSnapshot
from app.cad_bridge.compiler import _canonical_sha256
from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError
from app.cad_import.dxf_inspection import DxfInspector
from app.cad_import.package import PackageInspector
from app.cad_import.package_contracts import SourcePackage
from app.cad_import.policy import ConversionPolicy
from app.cad_intake.adapter import ProcessPackageInspection
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadDrawingEntry, CadIntakeRequest
from app.cad_intake.worker import InspectionWork, execute


def write_dxf(path: Path, references: dict[str, str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = ezdxf.new("R2018")
    document.units = 6
    document.modelspace().add_line((10, 20), (30, 40))
    for name, target in (references or {}).items():
        document.add_xref_def(filename=target, name=name)
        document.modelspace().add_blockref(name, (100, 200))
    document.saveas(path)
    return path


def write_autocad_snapshot(
    source: Path,
    *,
    dependencies: list[tuple[str, str, Path]] | None = None,
) -> Path:
    coverage = [
        {
            "identity": {"handle": "10", "instance_chain": []},
            "entity_type": "AcDbLine",
            "layer": "0",
            "status": "native",
            "method": "autodesk-acdbline-endpoints",
            "geometry_ids": ["path/10"],
        }
    ]
    native_dependencies = []
    for index, (record_handle, block_name, target) in enumerate(
        dependencies or [], start=1
    ):
        dependency_id = f"xref/{record_handle}"
        relative = target.relative_to(source.parent).as_posix()
        content = target.read_bytes()
        native_dependencies.append(
            {
                "id": dependency_id,
                "kind": "xref",
                "path": relative,
                "sha256": sha256(content).hexdigest(),
                "bytes": len(content),
                "record_handle": record_handle,
                "block_name": block_name,
                "stored_path": relative,
            }
        )
        coverage.append(
            {
                "identity": {
                    "handle": f"B{index}",
                    "instance_chain": [],
                },
                "entity_type": "AcDbBlockReference",
                "layer": "XREF",
                "status": "context",
                "method": "traverse-xref-reference",
                "reason": "resolved XREF traversed by AutoCAD",
                "geometry_ids": [],
                "dependency_ids": [dependency_id],
            }
        )
    geometry = {
        "id": "path/10",
        "identity": {"handle": "10", "instance_chain": []},
        "kind": "path",
        "closed": False,
        "coordinates": [[10, 20, 0], [30, 40, 0]],
        "achieved_tolerance_m": 0,
        "content_sha256": "0" * 64,
    }
    payload = {
        "schema": "green-atlas.autocad-snapshot/1",
        "source": {
            "sha256": file_sha256(source),
            "saved": True,
            "units_code": 6,
            "document_revision": "test-native-intake",
        },
        "extraction": {
            "autocad_version": "2027.0.1",
            "plugin_version": "0.1.16",
            "target": "macos-arm64",
            "projection": "wcs-xy-planar",
            "requested_tolerance_m": 0.001,
        },
        "dependencies": native_dependencies or None,
        "coverage": coverage,
        "geometry": [geometry],
        "summary": {
            "source_instances": len(coverage),
            "native": 1,
            "converted": 0,
            "context": len(coverage) - 1,
            "unresolved": 0,
            "payload_sha256": "0" * 64,
            "complete": True,
        },
    }
    normalized = CadSnapshot.model_validate(payload).model_dump(
        by_alias=True, mode="json", exclude_none=True
    )
    normalized["geometry"][0]["content_sha256"] = _canonical_sha256(
        {
            key: value
            for key, value in normalized["geometry"][0].items()
            if key != "content_sha256"
        }
    )
    normalized["summary"]["payload_sha256"] = _canonical_sha256(
        {key: value for key, value in normalized.items() if key != "summary"}
    )
    snapshot = CadSnapshot.model_validate(normalized)
    path = source.with_name(f"{source.name}.green-atlas.snapshot.json")
    path.write_text(snapshot.model_dump_json(by_alias=True, exclude_none=True))
    return path


def test_process_inspects_full_dxf_graph_without_converter(tmp_path):
    root = tmp_path / "originals"
    entry = write_dxf(root / "main.dxf", {"networks": "refs/networks.dxf"})
    child = write_dxf(root / "refs/networks.dxf")
    write_autocad_snapshot(child)
    write_autocad_snapshot(
        entry,
        dependencies=[("2F", "networks", child)],
    )
    originals = {path: path.read_bytes() for path in (entry, child)}
    config = CadIntakeConfig(
        (AllowedCadRoot("source", "DXF", root),), tmp_path / "data", None
    )
    config.require_enabled()
    request = CadIntakeRequest(
        root_id="source",
        entry="main.dxf",
        entry_sha256=file_sha256(entry),
        additional_entries=[
            CadDrawingEntry(
                path="refs/networks.dxf",
                sha256=file_sha256(child),
            )
        ],
    )
    passport = ProcessPackageInspection(config).inspect(
        "test-native-dxf", request, lambda: None, lambda _: None
    )
    assert passport.status == "requires_review"
    assert not passport.calculation_ready
    assert len(passport.drawings) == 2
    assert len(passport.references) == 1
    assert passport.references[0].status == "resolved"
    assert passport.entries == ["main.dxf"]
    for drawing in passport.drawings:
        assert drawing.status == "readable"
        assert drawing.source_sha256 == drawing.normalized_sha256
        assert drawing.inspection.units == 6
        assert drawing.inspection.modelspace_entities["LINE"] == 1
    assert {path: path.read_bytes() for path in originals} == originals
    assert len(list(root.rglob("*.snapshot.json"))) == 2


def test_mixed_graph_reports_dwg_without_dropping_other_references(tmp_path):
    root = tmp_path / "originals"
    write_dxf(root / "main.dxf", {"dwg": "external.dwg", "dxf": "networks.dxf"})
    (root / "external.dwg").write_bytes(b"AC1032fixture")
    write_dxf(root / "networks.dxf")
    result = PackageInspector(DxfInspector(tmp_path / "cache")).inspect(
        root, Path("main.dxf")
    )
    drawings = {drawing.path: drawing for drawing in result.drawings}
    assert result.status == "blocked"
    assert len(drawings) == 3
    assert drawings["main.dxf"].status == drawings["networks.dxf"].status == "readable"
    assert drawings["external.dwg"].status == "rejected"
    assert "Подготовьте этот чертёж в DXF" in drawings["external.dwg"].message
    assert not result.calculation_ready


def test_native_cache_reused_without_spawning_another_parser(tmp_path, monkeypatch):
    source = write_dxf(tmp_path / "originals/main.dxf")
    inspector = DxfInspector(tmp_path / "cache")
    first = inspector.inspect_dxf(source)

    def unexpected_parser(*args, **kwargs):
        pytest.fail("Unchanged DXF should use its verified inspection cache")

    monkeypatch.setattr(inspector, "_inspect", unexpected_parser)
    assert inspector.inspect_dxf(source) == first


def test_native_reader_preserves_budget_and_original_root_protection(tmp_path):
    source = write_dxf(tmp_path / "originals/main.dxf")
    inspector = DxfInspector(tmp_path / "cache", ConversionPolicy(max_output_bytes=1))
    with pytest.raises(CadConversionError, match="бюджета"):
        inspector.inspect_dxf(source)
    with pytest.raises(CadConversionError, match="вне исходного"):
        PackageInspector(DxfInspector(source.parent / "cache")).inspect(
            source.parent, Path(source.name)
        )


def test_no_roots_remains_disabled(tmp_path):
    with pytest.raises(ValueError, match="не настроен"):
        CadIntakeConfig((), tmp_path / "cache", None).require_enabled()


@pytest.mark.parametrize("stale_converter", [False, True])
def test_worker_uses_native_dxf_budget_not_dwg_budget(tmp_path, stale_converter):
    """Different tiny budgets reproduce the former 128/512 MiB mismatch."""
    source = write_dxf(tmp_path / "originals/main.dxf")
    write_autocad_snapshot(source)
    work = InspectionWork(
        root=source.parent,
        cache=tmp_path / "cache",
        output=tmp_path / "package.json",
        converter=tmp_path / "absent-dwg-converter.exe" if stale_converter else None,
        policy=ConversionPolicy(
            max_source_bytes=1, max_output_bytes=source.stat().st_size
        ),
        request=CadIntakeRequest(
            root_id="source", entry=source.name, entry_sha256=file_sha256(source)
        ),
    )
    execute(work)
    package = SourcePackage.model_validate_json(work.output.read_bytes())
    assert package.drawings[0].status == "readable"
    assert package.drawings[0].normalized_path == str(source)
    work = work.model_copy(
        update={"policy": ConversionPolicy(max_output_bytes=1)}
    )
    with pytest.raises(ValueError, match="бюджет"):
        execute(work)
