import json
import logging
from io import BytesIO
from math import cos, pi, sin
from pathlib import Path
from random import Random
from weakref import ref

import ezdxf
import pytest
from ezdxf import bbox, transform
from ezdxf.colors import RGB, encode_raw_color
from ezdxf.lldxf.tagwriter import BinaryTagWriter, TagCollector
from ezdxf.lldxf.types import DXFBinaryTag
from ezdxf.math import BoundingBox, Matrix44, Vec2, Vec3
from ezdxf.render.mleader import ConnectionSide
from shapely.geometry import Point, Polygon, box

from app.cad_import.aoi import prepare_aoi
from app.cad_import.aoi_annotations import (
    TOP_ATTACHMENT_OVERRIDE,
    repair_inactive_attachments,
)
from app.cad_import.aoi_binary import AoiBinaryTagWriter
from app.cad_import.aoi_boundary import load_boundary
from app.cad_import.aoi_contracts import (
    AoiEntityLink,
    AoiRequest,
    AoiSource,
    InfluenceScope,
)
from app.cad_import.aoi_export_audit import verify_exported_handles
from app.cad_import.aoi_policy import AoiPolicy
from app.cad_import.aoi_selection import BOUND_ROUNDING_MARGIN, AoiSelection
from app.cad_import.aoi_styles import normalize_dimension_colors
from app.cad_import.aoi_worker import extract, write_manifest
from app.cad_import.cache import file_sha256
from app.cad_import.contracts import CadConversionError
from app.dxf_import.adapters import EzdxfReader, _entity_geometry
from app.dxf_import.preview_marker import read_preview_marker


def artifact(document, path: Path) -> AoiSource:
    path.parent.mkdir(parents=True, exist_ok=True)
    document.saveas(path)
    digest = file_sha256(path)
    return AoiSource(
        original_path=path,
        original_sha256=digest,
        converted_path=path,
        converted_sha256=digest,
    )


def request_for(
    source, tmp_path: Path, radius: float | None = None, boundary_units=6
) -> AoiRequest:
    boundary = ezdxf.new("R2018")
    boundary.units = boundary_units
    factor = 1000 if boundary_units == 4 else 1
    contour = boundary.modelspace().add_lwpolyline(
        [(0, 0), (10 * factor, 0), (10 * factor, 10 * factor), (0, 10 * factor)],
        close=True,
    )
    return AoiRequest(
        source=artifact(source, tmp_path / "sources/main.dxf"),
        boundary_source=artifact(boundary, tmp_path / "sources/boundary.dxf"),
        boundary_handle=contour.dxf.handle,
        influence=InfluenceScope(
            radius_m=radius, provenance=["test fixture influence"]
        ),
    )


def test_aoi_keeps_whole_crossing_and_influencing_entities_with_source_hashes(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    crossing = source.modelspace().add_line((-20, 5), (20, 5))
    nearby = source.modelspace().add_line((11, 2), (11, 8))
    far = source.modelspace().add_line((100, 100), (110, 110))
    request = request_for(source, tmp_path, radius=2)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    result = ezdxf.readfile(output)
    selected = {
        link.source_handle
        for link in manifest.source_links
        if link.source_sha256 == request.source.original_sha256
    }
    assert selected == {crossing.dxf.handle, nearby.dxf.handle}
    assert far.dxf.handle not in selected
    assert tuple(result.modelspace().query("LINE")[0].dxf.start) == (-20, 5, 0)
    assert tuple(result.modelspace().query("LINE")[0].dxf.end) == (20, 5, 0)
    assert manifest.boundary_area_m2 == 100
    assert not manifest.calculation_ready and manifest.status == "preview_only"
    assert file_sha256(request.source.original_path) == request.source.original_sha256


def test_block_basepoint_transform_and_mpolygon_hole_survive_leaf_copy(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    block = source.blocks.new("plant", base_point=(2, 3))
    block.add_circle((2, 3), 1)
    insert = source.modelspace().add_blockref(
        "plant", (5, 5), dxfattribs={"xscale": 2, "yscale": 3, "rotation": 30}
    )
    insert.add_attrib("LABEL", "Original value", (5, 5))
    polygon = source.modelspace().add_mpolygon()
    polygon.paths.add_polyline_path([(1, 1), (9, 1), (9, 9), (1, 9)], is_closed=True)
    polygon.paths.add_polyline_path([(3, 3), (7, 3), (7, 7), (3, 7)], is_closed=True)
    request = request_for(source, tmp_path)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    result = ezdxf.readfile(output)
    copied = result.modelspace().query("ELLIPSE")[0]
    expected = list(insert.virtual_entities())[0]
    assert copied.dxf.center.isclose(expected.dxf.center)
    assert copied.dxf.major_axis.isclose(expected.dxf.major_axis)
    assert copied.dxf.ratio == expected.dxf.ratio
    assert result.modelspace().query("TEXT")[0].dxf.text == "Original value"
    assert bbox.extents([copied]).extmin.isclose(bbox.extents([insert]).extmin)
    assert _entity_geometry(
        result.modelspace().query("MPOLYGON")[0], 1
    ) == _entity_geometry(polygon, 1)
    link = next(link for link in manifest.source_links if link.entity_type == "ELLIPSE")
    assert link.insert_chain == [f"{insert.dxf.handle}:0"]
    assert manifest.derivation == "world_space_leaves"


def test_unknown_xref_included_once_without_embedding_and_unit_conversion(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    source.add_xref_def("missing.dwg", "external")
    source.modelspace().add_blockref("external", (1000, 1000))
    source.modelspace().add_line((1, 1), (9, 9))
    request = request_for(source, tmp_path, boundary_units=4)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    result = ezdxf.readfile(output)
    assert manifest.unknown_bounds == 1
    assert len(result.modelspace().query("INSERT")) == 1
    assert result.blocks.get("external").block.dxf.xref_path == "missing.dwg"
    assert result.units == 6 and manifest.boundary_area_m2 == 100
    contour = result.modelspace().query("LWPOLYLINE")[0]
    assert list(contour.get_points("xy")) == [(0, 0), (10, 0), (10, 10), (0, 10)]


def test_invalid_boundary_and_unproven_influence_are_not_silently_accepted() -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    line = source.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10)])
    with pytest.raises(CadConversionError, match="замкнутая"):
        load_boundary(source, line.dxf.handle, 6)
    with pytest.raises(ValueError, match="происхождения"):
        InfluenceScope(radius_m=50, verified=True)


def test_unknown_bounds_warnings_are_aggregated_by_entity_type() -> None:
    document = ezdxf.new("R2018")
    first = document.modelspace().add_text("first")
    second = document.modelspace().add_text("second")
    selection = AoiSelection(box(0, 0, 10, 10))

    assert selection.unknown_extent(first)
    assert selection.unknown_extent(second)

    assert selection.unknown == 2
    assert len(selection.warnings) == 1
    assert "2 объектов TEXT" in selection.warnings[0]
    assert f"#{first.dxf.handle}" in selection.warnings[0]
    assert f"#{second.dxf.handle}" in selection.warnings[0]


def test_changed_source_is_rejected_before_workpackage_publication(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    source.modelspace().add_line((0, 0), (10, 10))
    request = request_for(source, tmp_path)
    request.source.original_path.write_bytes(b"changed")
    with pytest.raises(CadConversionError, match="изменился"):
        extract(request, tmp_path / "working.dxf")
    assert not (tmp_path / "working.dxf").exists()


def test_bounded_worker_publishes_only_complete_artifact_and_keeps_original(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    source.modelspace().add_line((-10, 5), (15, 5))
    request = request_for(source, tmp_path)
    result = prepare_aoi(
        request, tmp_path / "workpackages", AoiPolicy(timeout_seconds=15)
    )
    assert file_sha256(result.drawing_path) == result.manifest.output_sha256
    assert result.manifest_path.is_file()
    assert result.peak_memory_bytes > 0
    assert file_sha256(request.source.original_path) == request.source.original_sha256


def test_large_block_is_filtered_before_dependency_copy(tmp_path: Path) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    source.layers.new("existing", dxfattribs={"color": 3})
    block = source.blocks.new("large-survey", base_point=(100, 100))
    crossing = block.add_line((80, 105), (120, 105))
    for index in range(2000):
        block.add_line((1000 + index, 1000), (1000 + index, 1010))
    insert = source.modelspace().add_blockref(
        block.name, (0, 0), dxfattribs={"layer": "existing"}
    )
    request = request_for(source, tmp_path)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    result = ezdxf.readfile(output)
    assert manifest.excluded_by_bounds == 2000
    assert manifest.selection_metrics["rejected_before_copy"] == 2000
    assert manifest.selected_modelspace_entities == 1
    assert len(result.modelspace().query("INSERT")) == 0
    assert "large-survey" not in result.blocks
    line = result.modelspace().query("LINE")[0]
    assert tuple(line.dxf.start) == (-20, 5, 0)
    assert tuple(line.dxf.end) == (20, 5, 0)
    assert line.dxf.layer == "existing"
    link = next(link for link in manifest.source_links if link.entity_type == "LINE")
    assert link.source_handle == crossing.dxf.handle
    assert link.insert_chain == [f"{insert.dxf.handle}:0"]
    assert output.stat().st_size < request.source.original_path.stat().st_size / 5
    assert file_sha256(request.source.original_path) == request.source.original_sha256


def test_nested_minsert_keeps_instance_transforms_and_original_attribute_handles(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    inner = source.blocks.new("inner", base_point=(3, 4))
    original_line = inner.add_line((3, 4), (4, 4))
    outer = source.blocks.new("outer", base_point=(1, 2))
    nested = outer.add_blockref("inner", (1, 2), dxfattribs={"rotation": 90})
    insert = source.modelspace().add_blockref(
        "outer", (4, 4), dxfattribs={"rotation": 90, "xscale": 2, "yscale": 3}
    )
    insert.grid(size=(1, 2), spacing=(0, 3))
    attribute = insert.add_attrib("ID", "Instance", (4, 4))
    request = request_for(source, tmp_path)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    result = ezdxf.readfile(output)
    lines = list(result.modelspace().query("LINE"))
    assert len(lines) == 2
    for line, instance in zip(lines, insert.multi_insert(), strict=True):
        expected_start = instance.matrix44().transform(
            nested.matrix44().transform(original_line.dxf.start)
        )
        expected_end = instance.matrix44().transform(
            nested.matrix44().transform(original_line.dxf.end)
        )
        assert line.dxf.start.isclose(expected_start)
        assert line.dxf.end.isclose(expected_end)
    text_links = [link for link in manifest.source_links if link.entity_type == "TEXT"]
    assert len(text_links) == 2
    assert {link.source_handle for link in text_links} == {attribute.dxf.handle}
    assert {tuple(link.insert_chain) for link in text_links} == {
        (f"{insert.dxf.handle}:0",),
        (f"{insert.dxf.handle}:1",),
    }
    assert manifest.selection_metrics["bounds_cache_hits"] == 1


def test_missing_nested_block_is_diagnosed_and_never_claims_full_geometry(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    block = source.blocks.new("parent")
    missing = block.add_blockref("absent", (0, 0))
    block.add_line((1, 1), (9, 9))
    source.modelspace().add_blockref("parent", (0, 0))
    request = request_for(source, tmp_path)
    manifest = extract(request, tmp_path / "working.dxf")
    assert any(f"#{missing.dxf.handle}" in item for item in manifest.diagnostics)
    assert not manifest.calculation_ready


def test_leaf_budget_refusal_retains_diagnostics_without_partial_artifact(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    source.modelspace().add_line((1, 1), (2, 2))
    source.modelspace().add_line((3, 3), (4, 4))
    request = request_for(source, tmp_path)
    output_root = tmp_path / "workpackages"
    with pytest.raises(CadConversionError, match="бюджет объектов"):
        prepare_aoi(request, output_root, AoiPolicy(max_selected_entities=1))
    assert not list(output_root.rglob("*.dxf"))
    failures = list((output_root / "failures").glob("*.json"))
    assert len(failures) == 1
    evidence = json.loads(failures[0].read_text(encoding="utf-8"))
    assert "stage=source_loaded" in evidence["log_tail"]
    assert file_sha256(request.source.original_path) == request.source.original_sha256


def test_outside_leaf_is_not_copied_and_nonuniform_polyline_stays_whole(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    block = source.blocks.new("curved")
    polyline = block.add_lwpolyline([(0, 5, 1), (10, 5, 0), (100, 5, 0)], format="xyb")
    outside = block.add_line((1000, 1000), (1001, 1001))
    source.modelspace().add_blockref(
        block.name, (0, 0), dxfattribs={"xscale": 2, "yscale": 1}
    )
    request = request_for(source, tmp_path)
    copied_handles = []
    original_copies = transform.copies

    def record_copies(entities, matrix):
        copied_handles.extend(entity.dxf.handle for entity in entities)
        return original_copies(entities, matrix)

    monkeypatch.setattr(transform, "copies", record_copies)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    assert outside.dxf.handle not in copied_handles
    links = [
        link
        for link in manifest.source_links
        if link.source_handle == polyline.dxf.handle
    ]
    assert {link.entity_type for link in links} == {"LINE", "ELLIPSE"}
    result = ezdxf.readfile(output)
    # The line is outside the site but belongs to the same crossing polyline.
    line = result.modelspace().query("LINE")[0]
    assert tuple(line.dxf.start) == (20, 5, 0)
    assert tuple(line.dxf.end) == (200, 5, 0)


def test_unknown_labels_do_not_replace_missing_physical_geometry(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    source.modelspace().add_line((1000, 1000), (1100, 1100))
    source.modelspace().add_text("Unknown extent", dxfattribs={"insert": (0, 0)})
    request = request_for(source, tmp_path)
    output_root = tmp_path / "workpackages"
    with pytest.raises(CadConversionError, match="не получена геометрия"):
        prepare_aoi(request, output_root)
    assert not list(output_root.rglob("*.dxf"))
    evidence = json.loads(
        next((output_root / "failures").glob("*.json")).read_text(encoding="utf-8")
    )
    assert '"selected_known_primitives": 0' in evidence["log_tail"]
    assert '"selected_primitives": 1' in evidence["log_tail"]


@pytest.mark.parametrize("bulge", [1, -1])
def test_curved_boundary_has_metric_error_bound_and_preserves_authored_arcs(
    tmp_path: Path,
    bulge: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    contour = source.modelspace().add_lwpolyline(
        [(0, 0, 0), (10, 0, bulge), (10, 10, 0), (0, 10, 0)],
        format="xyb",
        close=True,
    )
    source.modelspace().add_line((1, 1), (3, 3))
    boundary = load_boundary(source, contour.dxf.handle, 6)
    assert boundary.approximation.method == "bulge_arc_sagitta"
    assert boundary.approximation.sagitta_tolerance_m == 0.01
    for step in range(1001):
        angle = -pi / 2 + bulge * pi * step / 1000
        point = Point(10 + 5 * cos(angle), 5 + 5 * sin(angle))
        assert (
            point.distance(boundary.polygon) <= boundary.approximation.outward_margin_m
        )
    expected_area = 100 + bulge * pi * 25 / 2
    assert abs(boundary.area_m2 - expected_area) < 0.2
    source_ref = artifact(source, tmp_path / "sources/main.dxf")
    request = AoiRequest(
        source=source_ref,
        boundary_source=source_ref,
        boundary_handle=contour.dxf.handle,
    )
    reads = []
    original_read = ezdxf.readfile

    def record_read(path):
        reads.append(path)
        return original_read(path)

    monkeypatch.setattr(ezdxf, "readfile", record_read)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    assert len(reads) == 1
    result = original_read(output)
    copied = result.modelspace().query("LWPOLYLINE")
    assert len(copied) == 1
    assert list(copied[0].get_points("xyb")) == list(contour.get_points("xyb"))
    assert manifest.boundary_approximation == boundary.approximation
    assert (
        len(
            [
                link
                for link in manifest.source_links
                if link.source_handle == contour.dxf.handle
            ]
        )
        == 1
    )


def test_curved_boundary_vertex_budget_and_block_definition_guard() -> None:
    source = ezdxf.new("R2018")
    source.units = 4
    points = [(0, 0, 0), (10000, 0, 1), (10000, 10000, 0), (0, 10000, 0)]
    contour = source.modelspace().add_lwpolyline(points, format="xyb", close=True)
    boundary = load_boundary(source, contour.dxf.handle, 6)
    assert boundary.polygon.bounds[2] == pytest.approx(15, abs=0.01)
    with pytest.raises(CadConversionError, match="бюджет вершин"):
        load_boundary(
            source, contour.dxf.handle, 6, AoiPolicy(max_boundary_vertices=10)
        )
    definition = source.blocks.new("unplaced").add_lwpolyline(
        points, format="xyb", close=True
    )
    with pytest.raises(CadConversionError, match="пространстве модели"):
        load_boundary(source, definition.dxf.handle, 6)


def test_fast_line_prefilter_matches_disassembler_and_polygon_distance() -> None:
    source = ezdxf.new("R2018")
    mask = Polygon([(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)])
    random = Random(160915)
    cases = [
        ((-20, 5, 0), (20, 5, 0)),
        ((10, 0, 0), (10, 4, 0)),
        ((8, 8, 0), (9, 9, 0)),
    ]
    cases.extend(
        (
            tuple(random.uniform(-20, 30) for _ in range(3)),
            tuple(random.uniform(-20, 30) for _ in range(3)),
        )
        for _ in range(150)
    )
    transforms = [
        Matrix44(),
        Matrix44.z_rotate(0.8) @ Matrix44.translate(3, -2, 0),
        Matrix44.scale(2, 0.5, 3) @ Matrix44.x_rotate(0.7) @ Matrix44.y_rotate(-0.3),
    ]
    for distance in (0, 2):
        selection = AoiSelection(mask, distance)
        for start, end in cases:
            line = source.modelspace().add_line(start, end)
            original_bounds = bbox.extents([line], fast=True)
            margin = Vec3(
                BOUND_ROUNDING_MARGIN, BOUND_ROUNDING_MARGIN, BOUND_ROUNDING_MARGIN
            )
            expanded = BoundingBox(
                [original_bounds.extmin - margin, original_bounds.extmax + margin]
            )
            for matrix in transforms:
                world = BoundingBox(matrix.transform_vertices(expanded.cube_vertices()))
                # Previous path: disassemble even LINE, construct a Shapely box,
                # then run the polygon predicate for every candidate.
                expected = (
                    mask.distance(
                        box(
                            world.extmin.x,
                            world.extmin.y,
                            world.extmax.x,
                            world.extmax.y,
                        )
                    )
                    <= distance
                )
                assert selection.accepts(line, matrix) is expected
        assert selection.rectangle_rejects > 0


def test_provenance_replacement_keeps_upstream_xdata_without_duplicate_log(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    upstream_appid = "GREEN_ATLAS_AOI_SOURCE"
    source.appids.new(upstream_appid)
    line = source.modelspace().add_line((1, 1), (9, 9))
    line.set_xdata(upstream_appid, [(1000, "upstream-original-metadata")])
    request = request_for(source, tmp_path)
    caplog.set_level(logging.INFO)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    copied = ezdxf.readfile(output).modelspace().query("LINE")[0]
    link = next(link for link in manifest.source_links if link.entity_type == "LINE")
    assert link.provenance_appid != upstream_appid
    assert copied.get_xdata(upstream_appid)[0].value == "upstream-original-metadata"
    assert copied.get_xdata(link.provenance_appid)[1].value == line.dxf.handle
    assert "Duplicate XDATA appid" not in caplog.text
    assert file_sha256(request.source.original_path) == request.source.original_sha256


def test_binary_workpackage_roundtrip_has_marker_and_same_geometry_as_ascii(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    line = source.modelspace().add_line((-10, 5), (20, 5))
    annotation = "Водопровод Ø150, отметка 152.40"
    source.modelspace().add_text(annotation, dxfattribs={"insert": (1, 1)})
    polygon = source.modelspace().add_mpolygon()
    polygon.paths.add_polyline_path([(1, 1), (9, 1), (9, 9), (1, 9)], is_closed=True)
    polygon.paths.add_polyline_path([(3, 3), (7, 3), (7, 7), (3, 7)], is_closed=True)
    request = request_for(source, tmp_path, radius=2)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    content = output.read_bytes()
    assert content.startswith(b"AutoCAD Binary DXF")
    assert manifest.output_format == "binary_dxf"
    document = ezdxf.readfile(output)
    assert document.modelspace().query("TEXT")[0].dxf.text == annotation
    marker = read_preview_marker(document)
    assert marker is not None and not marker.calculation_ready
    assert marker.original_sha256 == request.source.original_sha256
    assert marker.converted_sha256 == request.source.converted_sha256
    assert marker.boundary_handle == request.boundary_handle
    assert marker.influence_radius_m == 2
    assert marker.boundary_bounds_m == (0, 0, 10, 10)
    link = next(link for link in manifest.source_links if link.entity_type == "LINE")
    assert (
        document.entitydb[link.output_handle].get_xdata(link.provenance_appid)[1].value
        == line.dxf.handle
    )
    assert _entity_geometry(
        document.modelspace().query("MPOLYGON")[0], 1
    ) == _entity_geometry(polygon, 1)
    ascii_path = tmp_path / "comparison.dxf"
    document.saveas(ascii_path, fmt="asc")
    binary_import = EzdxfReader().read("working.dxf", content)
    ascii_import = EzdxfReader().read("comparison.dxf", ascii_path.read_bytes())
    assert binary_import.preview_provenance == marker
    assert binary_import.geometry == ascii_import.geometry
    assert len(content) < ascii_path.stat().st_size


def test_used_dimstyle_raw_colors_are_losslessly_repaired_only_in_derivative(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    style = source.dimstyles.new("Original dimension colors")
    dimension = source.modelspace().add_linear_dim(
        base=(0, 5), p1=(1, 1), p2=(9, 1), dimstyle=style.dxf.name
    )
    dimension.render()
    # Real TABLES pathology: raw BYBLOCK, BYLAYER and ACI red in ACI fields.
    raw_colors = {
        "dimclrd": -1056964608,
        "dimclre": -1073741824,
        "dimclrt": -1023410175,
    }
    for attribute, raw in raw_colors.items():
        style.dxf.set(attribute, raw)
    line = source.modelspace().add_line((1, 1), (9, 9))
    payload = bytes(range(256)) + b"\x00\x00\xfe\xff"
    image_def = source.add_image_def("original-raster.png", size_in_pixel=(8, 8))
    image = source.modelspace().add_image(
        image_def, insert=(1, 1), size_in_units=(2, 2)
    )
    wipeout = source.modelspace().add_wipeout([(2, 2), (3, 2), (3, 3)])
    image.proxy_graphic = payload
    wipeout.proxy_graphic = payload
    source.modelspace().add_mtext(
        "Сеть Ø150\\Pотметка 152,40", dxfattribs={"insert": (1, 1)}
    )
    source.appids.new("ORIGINAL_BINARY")
    line.set_xdata("ORIGINAL_BINARY", [(1004, payload[:100])])
    line.new_extension_dict().add_xrecord("original-binary").reset(
        [DXFBinaryTag(310, payload[:100])]
    )
    request = request_for(source, tmp_path)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    result = ezdxf.readfile(output)
    copied_dimension = result.modelspace().query("DIMENSION")[0]
    copied_style = result.dimstyles.get(copied_dimension.dxf.dimstyle)
    assert result.modelspace().query("IMAGE")[0].proxy_graphic == payload
    assert result.modelspace().query("WIPEOUT")[0].proxy_graphic == payload
    assert (
        result.modelspace().query("MTEXT")[0].dxf.text == "Сеть Ø150\\Pотметка 152,40"
    )
    line_link = next(
        link for link in manifest.source_links if link.source_handle == line.dxf.handle
    )
    copied_line = result.entitydb[line_link.output_handle]
    assert copied_line.get_xdata("ORIGINAL_BINARY")[0].value == payload[:100]
    assert (
        copied_line.get_extension_dict()["original-binary"].tags[0].value
        == payload[:100]
    )
    assert any("hex-фрагментов" in item for item in manifest.diagnostics)
    assert [copied_style.dxf.get(field) for field in raw_colors] == [0, 256, 1]
    assert len(manifest.style_repairs) == 3
    assert {repair.source_handle for repair in manifest.style_repairs} == {
        style.dxf.handle
    }
    assert {repair.source_name for repair in manifest.style_repairs} == {style.dxf.name}
    assert [repair.original_value for repair in manifest.style_repairs] == list(
        raw_colors.values()
    )
    assert [repair.group for repair in manifest.style_repairs] == [176, 177, 178]
    assert all(
        repair.policy == "canonical_raw_dimstyle_aci_v1"
        for repair in manifest.style_repairs
    )
    reloaded_source = ezdxf.readfile(request.source.converted_path)
    assert [
        reloaded_source.dimstyles.get(style.dxf.name).dxf.get(field)
        for field in raw_colors
    ] == list(raw_colors.values())
    assert file_sha256(request.source.original_path) == request.source.original_sha256


@pytest.mark.parametrize(
    "invalid", [encode_raw_color(RGB(10, 20, 30)), 32768, -1056964607, -1023410176]
)
def test_dimstyle_rgb_unknown_and_noncanonical_values_are_rejected(
    invalid: int,
) -> None:
    source = ezdxf.new("R2018")
    source_style = source.dimstyles.new("bad color")
    target = ezdxf.new("R2018")
    style = target.dimstyles.new("bad color", dxfattribs={"dimclrd": invalid})
    link = AoiEntityLink(
        source_sha256="a" * 64,
        source_handle=source_style.dxf.handle,
        output_handle=style.dxf.handle,
        entity_type="DIMSTYLE",
    )
    with pytest.raises(CadConversionError, match="преобразования в ACI"):
        normalize_dimension_colors(target, {"a" * 64: source}, [link])
    assert style.dxf.dimclrd == invalid


def test_binary_hex_adapter_matches_bytes_writer_and_rejects_invalid_hex() -> None:
    payload = bytes(range(256)) + b"\x00\xff"
    expected, actual = BytesIO(), BytesIO()
    native = BinaryTagWriter(expected)
    adapter = AoiBinaryTagWriter(actual)
    for code in (310, 1004):
        native.write_tag2(code, payload)
        adapter.write_tag2(code, payload.hex().upper())
    assert actual.getvalue() == expected.getvalue()
    assert adapter.decoded_hex_chunks == 2
    for invalid in ("0", "GG", "00 01"):
        with pytest.raises(CadConversionError, match="Некорректный hex"):
            adapter.write_tag2(310, invalid)


def mleader_with_overflow(document):
    builder = document.modelspace().add_multileader_mtext("Standard")
    builder.set_content("Крышка колодца\\PПК-15", char_height=0.25)
    builder.add_leader_line(ConnectionSide.left, [Vec2(1, 1), Vec2(2, 2)])
    builder.build(insert=Vec2(4, 4))
    entity = builder.multileader
    entity.dxf.text_top_attachment_type = 41088
    entity.dxf.property_override_flags &= ~TOP_ATTACHMENT_OVERRIDE
    entity.proxy_graphic = bytes(range(256))
    return entity


def test_inactive_mleader_repair_retains_annotation_and_records_applicability(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    entity = mleader_with_overflow(source)
    source.modelspace().add_line((1, 1), (9, 9))
    request = request_for(source, tmp_path)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    copied = ezdxf.readfile(output).modelspace().query("MULTILEADER")[0]
    assert copied.dxf.text_top_attachment_type == entity.context.top_attachment
    assert copied.get_mtext_content() == entity.get_mtext_content()
    assert copied.proxy_graphic == entity.proxy_graphic
    assert copied.context.base_point.isclose(entity.context.base_point)
    assert copied.context.mtext.insert.isclose(entity.context.mtext.insert)
    assert [
        line.vertices for leader in copied.context.leaders for line in leader.lines
    ] == [line.vertices for leader in entity.context.leaders for line in leader.lines]
    assert len(manifest.annotation_repairs) == 1
    repair = manifest.annotation_repairs[0]
    assert repair.source_handle == entity.dxf.handle and repair.insert_chain == []
    assert repair.original_value == 41088 and repair.normalized_value == 9
    assert repair.global_attachment_direction == 0
    assert repair.leader_attachment_directions == [0]
    assert not repair.property_override_flags & TOP_ATTACHMENT_OVERRIDE
    assert file_sha256(request.source.original_path) == request.source.original_sha256


@pytest.mark.parametrize(
    "unsafe",
    ["global_vertical", "leader_vertical", "override", "unknown_context", "no_leaders"],
)
def test_mleader_active_or_ambiguous_attachment_is_never_repaired(unsafe: str) -> None:
    target = ezdxf.new("R2018")
    entity = mleader_with_overflow(target)
    if unsafe == "global_vertical":
        entity.dxf.text_attachment_direction = 1
    elif unsafe == "leader_vertical":
        entity.context.leaders[0].attachment_direction = 1
    elif unsafe == "override":
        entity.dxf.property_override_flags |= TOP_ATTACHMENT_OVERRIDE
    elif unsafe == "unknown_context":
        entity.context.top_attachment = 32768
    else:
        entity.context.leaders.clear()
    before = TagCollector.dxftags(entity)
    link = AoiEntityLink(
        source_sha256="a" * 64,
        source_handle="original",
        output_handle=entity.dxf.handle,
        entity_type="MULTILEADER",
    )
    with pytest.raises(CadConversionError, match="неактивная привязка"):
        repair_inactive_attachments(target, [link])
    assert TagCollector.dxftags(entity) == before


def test_transformed_mleader_does_not_keep_stale_proxy_and_reports_its_origin(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    entity = mleader_with_overflow(source)
    source.modelspace().unlink_entity(entity)
    source.blocks.new("annotation").add_entity(entity)
    insert = source.modelspace().add_blockref(
        "annotation", (2, 3), dxfattribs={"rotation": 30}
    )
    source.modelspace().add_line((1, 1), (9, 9))
    request = request_for(source, tmp_path)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    copied = ezdxf.readfile(output).modelspace().query("MULTILEADER")[0]
    assert copied.proxy_graphic is None
    assert copied.context.base_point.isclose(
        insert.matrix44().transform(entity.context.base_point)
    )
    assert copied.get_mtext_content() == entity.get_mtext_content()
    assert manifest.selection_metrics["proxy_graphics_invalidated"] == 1
    messages = [item for item in manifest.diagnostics if "proxy_graphic" in item]
    assert len(messages) == 1
    assert f"#{entity.dxf.handle}" in messages[0]
    assert f"{insert.dxf.handle}:0" in messages[0]
    assert manifest.annotation_repairs[0].insert_chain == [f"{insert.dxf.handle}:0"]


def test_missing_source_acis_is_explicit_in_sidecar_and_portable_marker(
    tmp_path: Path,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    source.modelspace().add_line((1, 1), (9, 9))
    request = request_for(source, tmp_path)
    # A source record as supplied by a converter: REGION exists but no ACDSDATA.
    path = request.source.converted_path
    text = path.read_text(encoding="utf-8")
    start = text.index("  2\nENTITIES\n")
    end = text.index("  0\nENDSEC\n", start)
    record = (
        f"  0\nREGION\n  5\nF001\n330\n{source.modelspace().block_record_handle}\n"
        "100\nAcDbEntity\n  8\n0\n100\nAcDbModelerGeometry\n 70\n1\n"
    )
    path.write_text(text[:end] + record + text[end:], encoding="utf-8")
    request.source.original_sha256 = request.source.converted_sha256 = file_sha256(path)
    output = tmp_path / "working.dxf"
    manifest = extract(request, output)
    assert len(manifest.export_omissions) == 1
    omission = manifest.export_omissions[0]
    assert omission.reason == "source_acis_payload_missing"
    assert omission.source.source_handle == "F001"
    assert not omission.source.exported_in_dxf
    result = ezdxf.readfile(output)
    assert not result.modelspace().query("REGION")
    assert len(result.modelspace().query("LINE")) == 1
    assert read_preview_marker(result).omitted_entity_count == 1
    assert file_sha256(path) == request.source.original_sha256


def test_unexpected_export_omission_is_rejected() -> None:
    link = AoiEntityLink(
        source_sha256="a" * 64,
        source_handle="original-line",
        output_handle="F001",
        entity_type="LINE",
    )
    with pytest.raises(CadConversionError, match="не совпала"):
        verify_exported_handles([link], [], set())


def test_manifest_serialization_releases_loaded_drawing_cycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = ezdxf.new("R2018")
    source.units = 6
    source.modelspace().add_line((1, 1), (9, 9))
    request = request_for(source, tmp_path)
    readfile = ezdxf.readfile
    documents = []

    def track_document(path):
        document = readfile(path)
        documents.append(ref(document))
        return document

    monkeypatch.setattr(ezdxf, "readfile", track_document)
    manifest = extract(request, tmp_path / "working.dxf")
    write_manifest(manifest, tmp_path / "manifest.json")
    assert all(document() is None for document in documents)
    assert "documents_released" in manifest.stage_rss_bytes
    assert (
        json.loads((tmp_path / "manifest.json").read_text("utf-8"))["output_sha256"]
        == manifest.output_sha256
    )
