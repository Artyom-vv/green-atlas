"""Native DXF intake must not depend on a DWG executable or change originals."""

from pathlib import Path

import ezdxf
import pytest

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError
from app.cad_import.dxf_inspection import DxfInspector
from app.cad_import.package import PackageInspector
from app.cad_import.package_contracts import SourcePackage
from app.cad_import.policy import ConversionPolicy
from app.cad_intake.adapter import ProcessPackageInspection
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadIntakeRequest
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


def test_process_inspects_full_dxf_graph_without_converter(tmp_path):
    root = tmp_path / "originals"
    entry = write_dxf(root / "main.dxf", {"networks": "refs/networks.dxf"})
    child = write_dxf(root / "refs/networks.dxf")
    originals = {path: path.read_bytes() for path in (entry, child)}
    config = CadIntakeConfig(
        (AllowedCadRoot("source", "DXF", root),), tmp_path / "data", None
    )
    config.require_enabled()
    request = CadIntakeRequest(
        root_id="source", entry="main.dxf", entry_sha256=file_sha256(entry)
    )
    passport = ProcessPackageInspection(config).inspect(
        "test-native-dxf", request, lambda: None, lambda _: None
    )
    assert passport.status == "requires_review"
    assert not passport.calculation_ready
    assert len(passport.drawings) == 2
    assert len(passport.references) == 1
    assert passport.references[0].status == "resolved"
    for drawing in passport.drawings:
        assert drawing.status == "readable"
        assert drawing.source_sha256 == drawing.normalized_sha256
        assert drawing.inspection.units == 6
        assert drawing.inspection.modelspace_entities["LINE"] == 1
    assert {path: path.read_bytes() for path in originals} == originals
    assert set(root.rglob("*.*")) == set(originals)


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
def test_worker_uses_native_dxf_budget_not_dwg_budget(tmp_path, monkeypatch, stale_converter):
    """Different tiny budgets reproduce the former 128/512 MiB mismatch."""
    source = write_dxf(tmp_path / "originals/main.dxf")
    monkeypatch.setattr(
        "app.cad_intake.worker.ConversionPolicy",
        lambda: ConversionPolicy(max_source_bytes=1, max_output_bytes=source.stat().st_size),
    )
    work = InspectionWork(
        root=source.parent,
        cache=tmp_path / "cache",
        output=tmp_path / "package.json",
        converter=tmp_path / "absent-dwg-converter.exe" if stale_converter else None,
        request=CadIntakeRequest(
            root_id="source", entry=source.name, entry_sha256=file_sha256(source)
        ),
    )
    execute(work)
    package = SourcePackage.model_validate_json(work.output.read_bytes())
    assert package.drawings[0].status == "readable"
    assert package.drawings[0].normalized_path == str(source)
    monkeypatch.setattr(
        "app.cad_intake.worker.ConversionPolicy",
        lambda: ConversionPolicy(max_output_bytes=1),
    )
    with pytest.raises(ValueError, match="бюджет"):
        execute(work)
