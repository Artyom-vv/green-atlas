"""Проекция native-примитивов сохраняет точность и топологию GeoJSON библиотеки."""

import json

import pytest
from shapely.geometry import LineString, Polygon, mapping

from app.cad_bridge.contracts import PathGeometry, PointGeometry, RegionGeometry
from app.cad_bridge.provider import _native_geojson, native_geometry_shape


@pytest.mark.parametrize("factor", [0.001, 1.0, 1000.0])
@pytest.mark.parametrize(
    "closed,coordinates",
    [
        (False, [(-0.0, 1e-100, 7), (1.2345678901234567, 2.345678901234567, -3)]),
        (False, [(1e6, 2e6, 0), (1e6 + 0.000001, 2e6, 0), (1e6 + 1, 2e6, 0)]),
        (True, [(0, 0, 0), (0, 10, 0), (10, 10, 0), (10, 0, 0), (0, 0, 0)]),
        # Самопересечение остаётся линией, без выдуманного полигона.
        (True, [(0, 0, 0), (10, 10, 0), (0, 10, 0), (10, 0, 0), (0, 0, 0)]),
    ],
)
def test_path_projection_keeps_exact_mapping_bytes(factor, closed, coordinates):
    geometry = PathGeometry(
        id="path/A",
        identity={"handle": "A"},
        kind="path",
        closed=closed,
        coordinates=coordinates,
        achieved_tolerance_m=0.001,
        content_sha256="a" * 64,
    )
    assert_native_mapping(geometry, factor)


@pytest.mark.parametrize("factor", [0.001, 1.0, 1000.0])
def test_point_projection_preserves_negative_zero_and_precision(factor):
    geometry = PointGeometry(
        id="point/A",
        identity={"handle": "A"},
        kind="point",
        coordinates=(-0.0, 1.2345678901234567, 90),
        content_sha256="a" * 64,
    )
    assert_native_mapping(geometry, factor)


@pytest.mark.parametrize("multiple", [False, True])
def test_region_projection_preserves_hole_and_separate_components(multiple):
    rings = [
        ("outer", [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]),
        ("hole", [(2, 2), (2, 4), (4, 4), (4, 2), (2, 2)]),
    ]
    if multiple:
        rings.append(("outer", [(30, 0), (40, 0), (40, 10), (30, 10), (30, 0)]))
    geometry = RegionGeometry(
        id="region/A",
        identity={"handle": "A"},
        kind="region",
        loops=[
            {"role": role, "closed": True, "coordinates": [(x, y, 0) for x, y in ring]}
            for role, ring in rings
        ],
        native_area_units2=496 if multiple else 396,
        native_perimeter_units=128 if multiple else 88,
        achieved_tolerance_m=0.001,
        content_sha256="a" * 64,
    )
    assert_native_mapping(geometry, 1)


def assert_native_mapping(geometry, factor):
    shape = native_geometry_shape(geometry, factor)
    if isinstance(geometry, PathGeometry):
        constructor = Polygon if shape.geom_type == "Polygon" else LineString
        previous_shape = constructor(
            [(point[0] * factor, point[1] * factor) for point in geometry.coordinates]
        )
        assert shape.wkb == previous_shape.wkb
    expected = mapping(shape)
    actual = _native_geojson(geometry, shape)
    assert actual == expected
    # Равенство чисел само по себе не различает -0.0 и 0.0.
    assert json.dumps(actual, ensure_ascii=False) == json.dumps(
        expected, ensure_ascii=False
    )
