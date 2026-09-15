import sys
from pathlib import Path

import ezdxf

from app.cad_import.boundary_catalog import MAX_BOUNDARY_CANDIDATES, boundary_catalog
from app.cad_import.cache import file_sha256
from app.cad_import.contracts import ConverterIdentity
from app.cad_import.conversion import LibreDwgConverter
from app.cad_import.inspection import inspect_drawing


def contour(layout, *, closed=True):
    return layout.add_lwpolyline(
        [(0, 0), (10000, 0), (10000, 10000), (0, 10000)], close=closed
    )


def test_only_closed_authored_modelspace_contours_have_metric_bounds():
    document = ezdxf.new("R2018")
    document.units = 4
    chosen = contour(document.modelspace())
    contour(document.modelspace(), closed=False)
    contour(document.blocks.new("symbol"))
    contour(document.paperspace())
    result = boundary_catalog(document)
    assert result.total_candidates == 1 and not result.truncated
    candidate = result.candidates[0]
    assert candidate.handle == chosen.dxf.handle
    assert candidate.bounds_m == (0, 0, 10, 10)
    assert candidate.vertex_count == 4 and candidate.available_for_preview


def test_missing_units_and_degenerate_bounds_are_explicitly_unavailable():
    document = ezdxf.new("R2018")
    document.units = 0
    contour(document.modelspace())
    result = boundary_catalog(document).candidates[0]
    assert not result.available_for_preview and result.bounds_m is None
    document.units = 6
    document.modelspace().delete_all_entities()
    document.modelspace().add_lwpolyline([(0, 0), (1, 0), (2, 0)], close=True)
    assert not boundary_catalog(document).candidates[0].available_for_preview


def test_boundary_list_is_bounded_without_claiming_completeness():
    document = ezdxf.new("R2018")
    document.units = 6
    for _ in range(MAX_BOUNDARY_CANDIDATES + 1):
        contour(document.modelspace())
    result = boundary_catalog(document)
    assert result.total_candidates == MAX_BOUNDARY_CANDIDATES + 1
    assert len(result.candidates) == MAX_BOUNDARY_CANDIDATES and result.truncated


def test_inspection_cache_tracks_content_and_refreshes_legacy_catalog(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        "app.cad_import.conversion.converter_identity",
        lambda _: ConverterIdentity(
            name="libredwg", version="fixture", executable_sha256="a" * 64
        ),
    )
    converter = LibreDwgConverter(Path(sys.executable), tmp_path / "cache")
    calls = []

    def inspect(source, work):
        calls.append(source)
        result = inspect_drawing(source)
        (work / "inspection.json").write_text(
            result.model_dump_json(), encoding="utf-8"
        )
        return result

    monkeypatch.setattr(converter, "_inspect", inspect)
    source = tmp_path / "source.dxf"
    document = ezdxf.new("R2018")
    document.units = 6
    contour(document.modelspace())
    document.saveas(source)
    first = converter.inspect_dxf(source)
    assert converter.inspect_dxf(source) == first and len(calls) == 1
    cache = next((converter.cache / "inspections" / file_sha256(source)).glob("*.json"))
    cache.write_text(
        first.model_copy(update={"boundary_catalog": None}).model_dump_json(),
        encoding="utf-8",
    )
    assert (
        converter.inspect_dxf(source).boundary_catalog is not None and len(calls) == 2
    )
    contour(document.modelspace())
    document.saveas(source)
    assert converter.inspect_dxf(source).boundary_catalog.total_candidates == 2
    assert len(calls) == 3
