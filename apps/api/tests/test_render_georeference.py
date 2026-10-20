import hashlib
import json
import math
from dataclasses import FrozenInstanceError

import pytest

from app.geometry.contracts import CoordinateReference
from app.rendering.georeference import RenderGeoreferenceError, render_frame
from app.scene.contracts import SceneSnapshot


def scene(**values):
    return SceneSnapshot(
        project_id="arbitrary-project",
        source_sha256="a" * 64,
        geometry_version=7,
        plan_version=2,
        horizon_year=0,
        coordinate_origin=[1015, -2010],
        note="test",
        **values,
    )


def config():
    angle = math.radians(13)
    return {
        "schema": "green-atlas.render-georeference/1",
        "project_id": "arbitrary-project",
        "source_sha256": "a" * 64,
        "geometry_version": 7,
        "status": "candidate",
        "reference_lon_lat": [31, 48],
        "earth_radius_m": 6378137,
        "scale": 1.003,
        "rotation": [
            [math.cos(angle), math.sin(angle)],
            [-math.sin(angle), math.cos(angle)],
        ],
        "translation": [1000, -2000],
        "valid_extent_cad_xy": [800, -2200, 1200, -1800],
        "evidence_refs": ["control-manifest-sha256:example"],
        "rmse_m": 0.9,
        "max_residual_m": 1.4,
    }


def write_config(tmp_path, values):
    path = tmp_path / "georeferences/arbitrary-project.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values))
    return path


def test_rotated_sidecar_round_trip_preserves_wcs_and_scene_origin(tmp_path):
    snapshot = scene()
    before = snapshot.model_dump_json()
    path = write_config(tmp_path, config())
    frame = render_frame(snapshot, tmp_path)
    assert frame is not None
    for xy in [(1000, -2000), (1027, -1910), (917, -2104)]:
        lon_lat = frame.cad_to_wgs84(*xy)
        local = frame.wgs84_to_local(*lon_lat)
        assert local == pytest.approx((xy[0] - 1015, xy[1] + 2010), abs=1e-7)
    assert frame.cad_to_wgs84(1000, -2000) == (31, 48)
    assert (
        frame.metadata["config_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    )
    assert frame.metadata["status"] == "candidate"
    assert snapshot.model_dump_json() == before


@pytest.mark.parametrize("status", ["declared", "verified"])
def test_reconstructed_tangent_frame_does_not_subtract_scene_origin_twice(
    tmp_path, status
):
    snapshot = scene(
        coordinate_reference=CoordinateReference(
            status=status,
            source="dxf_custom_georeference",
            axis_order="xy",
            local_projection="local_equirectangular_wgs84",
            origin_wgs84=[48, 31],
            earth_radius_m=6378137,
        )
    )
    frame = render_frame(snapshot, tmp_path)
    assert frame is not None
    assert frame.cad_to_wgs84(0, 0) == (31, 48)
    assert frame.wgs84_to_local(31, 48) == (-1015, 2010)
    world = frame.cad_to_wgs84(1015, -2010)
    assert frame.wgs84_to_local(*world) == pytest.approx((0, 0), abs=1e-7)
    assert frame.metadata["basis"] == "scene_coordinate_reference"
    assert frame.metadata["status"] == status
    assert frame.metadata["source_origin_axis_order"] == "lat_lon"
    assert frame.metadata["reference_lon_lat"] == [31, 48]


@pytest.mark.parametrize(
    "reference",
    [
        CoordinateReference(),
        CoordinateReference(status="local", origin_wgs84=[48, 31]),
        CoordinateReference(status="declared", crs_id="EPSG:4326"),
        CoordinateReference(
            status="unknown",
            axis_order="xy",
            local_projection="local_equirectangular_wgs84",
            origin_wgs84=[48, 31],
            earth_radius_m=6378137,
        ),
        CoordinateReference(
            status="declared",
            axis_order="yx",
            local_projection="local_equirectangular_wgs84",
            origin_wgs84=[48, 31],
            earth_radius_m=6378137,
        ),
    ],
)
def test_missing_or_unreconstructible_reference_never_infers_a_frame(
    tmp_path, reference
):
    assert render_frame(scene(coordinate_reference=reference), tmp_path) is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize(
    "key,value",
    [
        ("source_sha256", "b" * 64),
        ("source_sha256", None),
        ("project_id", "another-project"),
        ("geometry_version", 8),
        ("geometry_version", True),
        ("schema", "unrecognized/1"),
        ("status", "unknown"),
        ("reference_lon_lat", [181, 48]),
        ("reference_lon_lat", [31, 91]),
        ("reference_lon_lat", [31, 90]),
        ("earth_radius_m", 0),
        ("earth_radius_m", True),
        ("earth_radius_m", float("inf")),
        ("scale", 1000),
        ("scale", 0),
        ("scale", float("nan")),
        ("rotation", [[1, 1], [0, 1]]),
        ("rotation", [[1, 0], [0, 0]]),
        ("rotation", [[1, 0], [0, -1]]),
        ("rotation", [[1, 0]]),
        ("translation", [1000, float("nan")]),
        ("valid_extent_cad_xy", [1200, -2200, 800, -1800]),
        ("valid_extent_cad_xy", None),
        ("evidence_refs", [None]),
        ("rmse_m", -1),
        ("max_residual_m", float("inf")),
    ],
)
def test_invalid_or_stale_sidecar_rejected_without_fallback(tmp_path, key, value):
    values = config()
    values[key] = value
    write_config(tmp_path, values)
    # Even a complete declared scene frame cannot hide an invalid sidecar.
    snapshot = scene(
        coordinate_reference=CoordinateReference(
            status="declared",
            axis_order="xy",
            local_projection="local_equirectangular_wgs84",
            origin_wgs84=[48, 31],
            earth_radius_m=6378137,
        )
    )
    with pytest.raises(RenderGeoreferenceError):
        render_frame(snapshot, tmp_path)


def test_both_transform_directions_reject_points_outside_valid_extent(tmp_path):
    values = config()
    values.update(rotation=[[1, 0], [0, 1]], scale=1)
    write_config(tmp_path, values)
    frame = render_frame(scene(), tmp_path)
    assert frame is not None
    with pytest.raises(RenderGeoreferenceError, match="outside"):
        frame.cad_to_wgs84(1201, -2000)
    outside_lon = 31 + math.degrees(201 / (6378137 * math.cos(math.radians(48))))
    with pytest.raises(RenderGeoreferenceError, match="outside"):
        frame.wgs84_to_local(outside_lon, 48)
    with pytest.raises(RenderGeoreferenceError, match="finite"):
        frame.cad_to_wgs84(float("nan"), -2000)
    with pytest.raises(RenderGeoreferenceError, match="out of range"):
        frame.wgs84_to_local(31, 100)


def test_frozen_frame_and_detached_metadata_preserve_recorded_evidence(tmp_path):
    write_config(tmp_path, config())
    frame = render_frame(scene(), tmp_path)
    assert frame is not None
    metadata = frame.metadata
    metadata["evidence_refs"].append("untrusted-change")
    metadata["rotation"][0][0] = 100
    metadata["status"] = "verified"
    assert frame.metadata["evidence_refs"] == ["control-manifest-sha256:example"]
    assert frame.metadata["status"] == "candidate"
    assert frame.metadata["rotation"][0][0] == math.cos(math.radians(13))
    with pytest.raises(FrozenInstanceError):
        frame.status = "verified"


def test_malformed_json_is_an_addressed_georeference_error(tmp_path):
    path = write_config(tmp_path, config())
    path.write_text("not json")
    with pytest.raises(RenderGeoreferenceError, match="Cannot read"):
        render_frame(scene(), tmp_path)
