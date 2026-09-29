"""A site is an area even when its source layer also retains open lines."""

from shapely.geometry import LineString, box, mapping

from app.contracts import GeometrySnapshot, Project
from app.geometry.domain import PositionChecker


def source_feature(kind, geometry):
    return {
        "type": "Feature",
        "properties": {"kind": kind},
        "geometry": mapping(geometry),
    }


def test_site_lines_do_not_turn_the_area_into_a_geometry_collection():
    project = Project(
        name="Mixed boundary",
        geometry=GeometrySnapshot(feature_collection={
            "type": "FeatureCollection",
            "features": [
                source_feature("site_surface", box(0, 0, 20, 20)),
                source_feature("site_surface", LineString([(20, 0), (25, 0)])),
            ],
        }),
    )
    checker = PositionChecker(project)
    assert checker.site.geom_type == "Polygon"
    assert checker.check(10, 10, 0.5, "shrub") is None
    assert checker.check(24, 1, 0.5, "shrub").code == "SITE_BOUNDARY"


def test_unclosed_surface_lines_do_not_hide_a_valid_border_area():
    project = Project(
        name="Line and polygon border",
        geometry=GeometrySnapshot(feature_collection={
            "type": "FeatureCollection",
            "features": [
                source_feature("site_surface", LineString([(0, 0), (5, 0)])),
                source_feature("site_border", box(0, 0, 20, 20)),
            ],
        }),
    )
    checker = PositionChecker(project)
    assert checker.site.geom_type == "Polygon"
    assert checker.check(10, 10, 0.5, "tree") is None
    assert checker.check(24, 1, 0.5, "tree").code == "SITE_BOUNDARY"
