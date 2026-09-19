from pathlib import Path

import ezdxf
import pytest

from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError
from app.cad_import.conversion import LibreDwgConverter
from app.cad_import.inspection import inspect_drawing
from app.cad_import.package import PackageInspector
from app.cad_import.package_contracts import ReferenceOverride
from app.cad_import.references import PackagePaths, path_key


def dxf(path: Path, references: dict[str, str] | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    document = ezdxf.new("R2018")
    document.modelspace().add_line((0, 0), (1, 1))
    for block, reference in (references or {}).items():
        document.add_xref_def(filename=reference, name=block)
        document.modelspace().add_blockref(block, (10, 20))
    document.saveas(path)
    return path


def test_reference_resolution_keeps_owner_context_and_rejects_escape(
    tmp_path: Path,
) -> None:
    first = dxf(tmp_path / "street-1" / "source.dxf")
    expected = dxf(tmp_path / "street-1" / "network.dxf")
    dxf(tmp_path / "street-2" / "network.dxf")
    paths = PackagePaths(tmp_path)
    edge, target = paths.resolve(first, "network", ".\\NETWORK.dxf")
    assert edge.status == "resolved"
    assert target == expected
    for reference in (
        "../../network.dxf",
        "C:\\author\\network.dxf",
        "\\\\server\\network.dxf",
    ):
        assert (
            paths.resolve(first, "external", reference)[0].status == "outside_package"
        )
    assert paths.resolve(first, "missing", "wrong/network.dxf")[1] is None


def test_archive_name_normalization_does_not_remove_parent_traversal() -> None:
    assert path_key("foo./../NETWORK.dxf ") == "network.dxf"
    assert path_key("../../network.dxf") == "../../network.dxf"
    assert path_key(".. /.. /network.dxf") == "../../network.dxf"


def test_package_cycles_missing_refs_and_original_preservation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "originals"
    entry = dxf(
        root / "main.dxf",
        {"network": "references/network.dxf", "missing": "missing.dwg"},
    )
    child = dxf(root / "references/network.dxf", {"main": "../main.dxf"})
    originals = {path: path.read_bytes() for path in (entry, child)}
    converter = object.__new__(LibreDwgConverter)
    converter.cache = tmp_path / "cache"
    monkeypatch.setattr(converter, "inspect_dxf", inspect_drawing)
    manifest = PackageInspector(converter).inspect(root, Path("main.dxf"))
    assert manifest.status == "blocked"
    assert not manifest.calculation_ready
    assert len(manifest.drawings) == 2
    assert {edge.status for edge in manifest.references} == {
        "resolved",
        "missing",
        "cycle",
    }
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert set(root.rglob("*.dxf")) == set(originals)


def test_complete_graph_does_not_claim_conversion_fidelity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "originals"
    dxf(root / "main.dxf")
    converter = object.__new__(LibreDwgConverter)
    converter.cache = tmp_path / "cache"
    monkeypatch.setattr(converter, "inspect_dxf", inspect_drawing)
    manifest = PackageInspector(converter).inspect(root, Path("main.dxf"))
    assert manifest.status == "requires_review"
    assert not manifest.calculation_ready


def test_multiple_independent_dxf_entries_share_one_explicit_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "originals"
    first = dxf(root / "base/site.dxf")
    second = dxf(root / "networks/site.dxf")
    originals = {path: path.read_bytes() for path in (first, second)}
    converter = object.__new__(LibreDwgConverter)
    converter.cache = tmp_path / "cache"
    monkeypatch.setattr(converter, "inspect_dxf", inspect_drawing)

    manifest = PackageInspector(converter).inspect_entries(
        root,
        [Path("base/site.dxf"), Path("networks/site.dxf")],
    )

    assert manifest.entry == "base/site.dxf"
    assert manifest.entries == ["base/site.dxf", "networks/site.dxf"]
    assert [item.path for item in manifest.drawings] == manifest.entries
    assert manifest.references == []
    assert manifest.status == "requires_review"
    assert all(path.read_bytes() == content for path, content in originals.items())


def test_package_cache_cannot_write_into_originals(tmp_path: Path) -> None:
    dxf(tmp_path / "main.dxf")
    converter = object.__new__(LibreDwgConverter)
    converter.cache = tmp_path / "cache"
    with pytest.raises(CadConversionError, match="вне исходного"):
        PackageInspector(converter).inspect(tmp_path, Path("main.dxf"))


def test_explicit_reference_override_keeps_original_path_and_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "originals"
    dxf(root / "main.dxf", {"network": "missing/network.dxf"})
    target = dxf(root / "provided/network.dxf")
    override = ReferenceOverride(
        owner="main.dxf",
        block="network",
        target="provided/network.dxf",
        expected_sha256=file_sha256(target),
        reason="Provided in the same street package; explicit review",
    )
    converter = object.__new__(LibreDwgConverter)
    converter.cache = tmp_path / "cache"
    monkeypatch.setattr(converter, "inspect_dxf", inspect_drawing)
    manifest = PackageInspector(converter).inspect(root, Path("main.dxf"), [override])
    edge = manifest.references[0]
    assert edge.requested_path == "missing/network.dxf"
    assert edge.target == override.target
    assert edge.expected_sha256 == override.expected_sha256
    assert edge.resolution == "explicit_override"
    assert manifest.status == "requires_review" and not manifest.calculation_ready
    target.write_bytes(b"changed")
    with pytest.raises(CadConversionError, match="изменилось"):
        PackageInspector(converter).inspect(root, Path("main.dxf"), [override])
