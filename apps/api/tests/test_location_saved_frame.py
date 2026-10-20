"""Saved frames are reused only for a matching, supported CAD/map area."""

import hashlib
import json

import pytest
from shapely.affinity import translate
from shapely.geometry import box, mapping, shape
from test_location_application import (
    apply_request,
    auto_alignment,
    geographic,
    polygon_geo,
    resolved,
)
from test_location_application import fixture as fixture

from app.geometry.contracts import CoordinateReference
from app.location.contracts import LocationAlignmentRequest, LocationControlPair
from app.rendering.georeference import render_frame


def save_frame(fixture, **changes):
    service, project, _, _ = fixture
    for row in project.geometry.feature_collection["features"]:
        row["properties"].pop("address", None)
    values = {
        "schema": "green-atlas.render-georeference/1",
        "project_id": project.id,
        "source_sha256": project.source_file.content_sha256,
        "geometry_version": project.geometry_version,
        "status": "candidate",
        "reference_lon_lat": [37, 55],
        "earth_radius_m": 6378137,
        "scale": 1.0,
        "rotation": [[1, 0], [0, 1]],
        "translation": [1000, 2000],
        "valid_extent_cad_xy": [800, 1800, 1300, 2300],
        "evidence_refs": ["previous-reviewed-control-receipt"],
        "rmse_m": 0.75,
        "max_residual_m": 1.2,
        **changes,
    }
    path = service.directory / "georeferences" / f"{project.id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(values))
    return path


def test_valid_saved_frame_needs_no_new_addresses_or_control_pairs(fixture):
    service, project, _, _ = fixture
    path = save_frame(fixture)
    before = path.read_bytes()
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    assert alignment.method == "saved_frame" and alignment.status == "candidate"
    assert alignment.control_pairs == []
    assert alignment.inlier_count == 0 and alignment.holdout_rmse_m is None
    assert alignment.rmse_m == 0.75 and alignment.max_residual_m == 1.2
    assert (
        alignment.provenance["saved_frame"]["config_sha256"]
        == hashlib.sha256(before).hexdigest()
    )
    assert alignment.provenance["saved_frame"]["evidence_refs"] == [
        "previous-reviewed-control-receipt"
    ]
    overlap = alignment.provenance["overlap_check"]
    assert overlap["matched_building_count"] == 5
    assert overlap["new_fit_or_holdout"] is False
    assert len({row["osm_id"] for row in overlap["matches"]}) == 5
    assert "Используется сохранённая привязка" in alignment.message
    assert "проверено отдельными точками" not in alignment.message
    assert path.read_bytes() == before
    assert (
        service.apply(project.id, apply_request(draft, alignment)).applied.status
        == "candidate"
    )


@pytest.mark.parametrize(
    "frame_change",
    [
        {"source_sha256": "b" * 64},
        {"geometry_version": 0},
        {"status": "invalid"},
        {"valid_extent_cad_xy": [995, 1995, 1006, 2006]},
    ],
)
def test_stale_invalid_or_insufficient_saved_support_falls_back_to_controls(
    fixture, frame_change
):
    service, project, _, _ = fixture
    save_frame(fixture, **frame_change)
    alignment = auto_alignment(service, project, resolved(fixture))
    assert alignment.status == "needs_controls"
    assert alignment.method == "automatic_addresses"


def test_wrong_map_area_cannot_reuse_an_otherwise_valid_frame(fixture):
    service, project, providers, _ = fixture
    save_frame(fixture)
    for index, row in enumerate(providers.building_rows):
        row["geometry"] = mapping(
            polygon_geo(box(150 + index * 20, 150, 160 + index * 20, 160))
        )
    alignment = auto_alignment(service, project, resolved(fixture))
    assert alignment.status == "needs_controls"
    assert alignment.method != "saved_frame"


def test_duplicate_cad_footprints_do_not_multiply_overlap_witnesses(fixture):
    service, project, _, _ = fixture
    save_frame(fixture)
    rows = project.geometry.feature_collection["features"]
    first = rows[0]["geometry"]
    for row in rows:
        if row["properties"]["kind"] == "building":
            row["geometry"] = first
    alignment = auto_alignment(service, project, resolved(fixture))
    assert alignment.status == "needs_controls"


def test_competing_osm_parents_do_not_qualify_as_saved_frame_overlap(fixture):
    service, project, providers, _ = fixture
    save_frame(fixture)
    providers.building_rows.extend(
        [
            {**row, "id": "duplicate:" + row["id"]}
            for row in list(providers.building_rows)
        ]
    )
    alignment = auto_alignment(service, project, resolved(fixture))
    assert alignment.status == "needs_controls"


def test_saved_frame_radius_and_supported_preview_survive_explicit_apply(fixture):
    service, project, providers, _ = fixture
    path = save_frame(
        fixture, earth_radius_m=6371000, valid_extent_cad_xy=[980, 1980, 1150, 2150]
    )
    providers.building_rows.append(
        {
            "type": "Feature",
            "id": "outside",
            "geometry": mapping(polygon_geo(box(190, 0, 210, 20))),
            "properties": {"tags": {"building": "yes"}},
        }
    )
    frame_before = render_frame(service._snapshot(project), service.directory)
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    assert alignment.method == "saved_frame"
    assert alignment.transform.earth_radius_m == 6371000
    assert alignment.preview_buildings["omitted_outside_extent_count"] == 1
    assert all(
        row["id"] != "outside" for row in alignment.preview_buildings["features"]
    )
    source_point = tuple(
        draft.map_buildings["features"][0]["geometry"]["coordinates"][0][0]
    )
    preview_point = alignment.preview_buildings["features"][0]["geometry"][
        "coordinates"
    ][0][0]
    assert preview_point == pytest.approx(frame_before.wgs84_to_local(*source_point))
    service.apply(project.id, apply_request(draft, alignment))
    assert json.loads(path.read_bytes())["earth_radius_m"] == 6371000
    frame_after = render_frame(service._snapshot(project), service.directory)
    assert frame_after.wgs84_to_local(*source_point) == pytest.approx(preview_point)


def test_explicit_manual_pairs_override_saved_frame_reuse(fixture):
    service, project, _, _ = fixture
    save_frame(fixture)
    draft = resolved(fixture)
    pairs = [
        LocationControlPair(
            cad_xy=(1000 + x, 2000 + y),
            lon_lat=geographic(x, y),
            usage="holdout" if index == 3 else "fit",
        )
        for index, (x, y) in enumerate([(0, 0), (100, 0), (0, 100), (100, 100)])
    ]
    alignment = service.align(
        project.id, LocationAlignmentRequest(draft_id=draft.id, control_pairs=pairs)
    )
    assert alignment.status == "candidate" and alignment.method == "manual"


def test_declared_project_tangent_frame_can_be_reused_without_sidecar(fixture):
    service, project, _, _ = fixture
    for row in project.geometry.feature_collection["features"]:
        row["properties"].pop("address", None)
        row["geometry"] = mapping(translate(shape(row["geometry"]), -1000, -2000))
    project.coordinate_reference = CoordinateReference(
        status="declared",
        source="user_declared",
        axis_order="xy",
        origin_wgs84=[55, 37],
        local_projection="local_equirectangular_wgs84",
        earth_radius_m=6378137,
        evidence="Previously declared frame",
    )
    alignment = auto_alignment(service, project, resolved(fixture))
    assert alignment.status == "candidate" and alignment.method == "saved_frame"
    assert alignment.provenance["saved_frame"]["basis"] == "scene_coordinate_reference"
    assert not (service.directory / "georeferences" / f"{project.id}.json").exists()
