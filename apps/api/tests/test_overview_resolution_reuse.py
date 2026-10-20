"""Exact SVG-пути простых примитивов при смене масштаба."""

from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest
from shapely.geometry import GeometryCollection, LineString, Point, Polygon

import app.geometry.viewport.source_overview as module


@pytest.mark.parametrize(
    "geometry,shared",
    [
        (LineString([(1.2345678, -9.876543), (10, 20)]), True),
        (Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]), True),
        (Polygon([(0, 0), (10, 0), (5, 10)]), True),
        (Point(2, 3), False),
        (LineString([(0, 0), (1, 0.03), (2, 0)]), False),
        (
            Polygon(
                [(0, 0), (10, 0), (10, 10), (0, 10)],
                holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]],
            ),
            False,
        ),
        (Polygon([(0, 0), (5, 0.1), (10, 0), (10, 10), (0, 10)]), False),
        (GeometryCollection([Point(1, 1), LineString([(0, 0), (1, 1)])]), False),
    ],
)
def test_cache_preserves_exact_paths_and_only_reuses_scale_independent_geometry(
    monkeypatch, geometry, shared
):
    original = module._display_paths
    resolutions = [0.25, 0.225, 2.5]
    expected = [original(geometry, resolution) for resolution in resolutions]
    observed = Mock(wraps=original)
    monkeypatch.setattr(module, "_display_paths", observed)
    cache = module.SourcePathCache()
    actual = [cache.paths(geometry, resolution) for resolution in resolutions]
    assert actual == expected
    assert observed.call_count == (1 if shared else 3)
    assert len(cache.entries) == (1 if shared else 3)
    assert cache.bytes <= cache.max_bytes


def test_zoom_reuses_fixed_paths_while_reframing_strokes_points_and_holes(monkeypatch):
    shapes = [
        LineString([(0, 0), (10, 10)]),
        Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
        Point(4, 5),
        Polygon(
            [(0, 0), (10, 0), (10, 10), (0, 10)],
            holes=[[(2, 2), (4, 2), (4, 4), (2, 4)]],
        ),
    ]
    candidates = [
        ({"properties": {"source_layer": f"Слой {i}", "kind": "building"}}, shape)
        for i, shape in enumerate(shapes)
    ]
    extent = (-1, -1, 12, 12)
    expected = [module.source_overview(candidates, extent, r) for r in [0.25, 0.225]]
    observed = Mock(wraps=module._display_paths)
    monkeypatch.setattr(module, "_display_paths", observed)
    cache = module.SourceOverviewCache()
    first = cache.render(candidates, extent, 0.25, b"same-indexes")
    zoom = cache.render(candidates, extent, 0.225, b"same-indexes")
    assert [first, zoom] == expected
    assert observed.call_count == 6
    assert all(
        row["source_features"] == row["rendered_features"] == 4 for row in expected
    )
    svg = ET.fromstring(zoom["svg"])
    assert {group.attrib["stroke-width"] for group in svg} == {str(0.225 * 0.8)}
    holes = next(
        group for group in svg if group.attrib["data-source-layer"] == "Слой 3"
    )
    assert holes[0].attrib["d"].count("Z") == 2


def test_fixed_paths_still_respect_byte_budget_and_geometry_identity():
    first = LineString([(0, 0), (10, 10)])
    changed = LineString([(0, 0), (20, 10)])
    cache = module.SourcePathCache(max_bytes=1)
    assert cache.paths(first, 0.25) == module._display_paths(first, 0.25)
    assert cache.paths(changed, 0.225) == module._display_paths(changed, 0.225)
    assert not cache.entries and cache.bytes == 0
    cache = module.SourcePathCache()
    assert cache.paths(first, 0.25) != cache.paths(changed, 0.225)


@pytest.mark.parametrize(
    "shape,second_resolution",
    [(LineString([(0, 0), (10, 10)]), 0.225), (Point(4, 5), 0.25)],
)
def test_warm_path_hit_never_rechecks_geos_coordinates(
    monkeypatch, shape, second_resolution
):
    predicate = Mock(wraps=module._fixed_display_geometry)
    monkeypatch.setattr(module, "_fixed_display_geometry", predicate)
    cache = module.SourcePathCache()
    expected = cache.paths(shape, 0.25)
    assert predicate.call_count == 1
    assert cache.paths(shape, second_resolution) == expected
    assert predicate.call_count == 1


def test_fixed_and_scaled_path_entries_stay_bounded_and_rebuild_after_eviction():
    shapes = [LineString([(i, 0), (i, 10)]) for i in range(20)]
    cache = module.SourcePathCache(max_bytes=500)
    for index, shape in enumerate(shapes):
        assert cache.paths(shape, 0.25 + index / 100) == module._display_paths(
            shape, 0.25 + index / 100
        )
        assert cache.bytes <= 500
    assert len(cache.entries) < len(shapes)
    assert cache.paths(shapes[0], 0.5) == module._display_paths(shapes[0], 0.5)
    assert cache.bytes <= 500
