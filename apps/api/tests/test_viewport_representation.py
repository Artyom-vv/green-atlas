from math import nextafter

import pytest
from shapely.geometry.base import BaseGeometry

from app.geometry.contracts import GeometrySnapshot
from app.geometry.query_adapters import IndexedGeometryQuery
from app.geometry.viewport_representation import viewport_representation
from app.projects.contracts import Project


def feature(id_: str, x: float = 0, kind: str = "road") -> dict:
    return {
        "type": "Feature",
        "id": id_,
        "properties": {"kind": kind},
        "geometry": {
            "type": "LineString",
            "coordinates": [[x, 0], [x + 1, 0.35005], [x + 2, 0]],
        },
    }


def project_with(*features: dict) -> Project:
    return Project(
        name="Viewport representation",
        geometry_version=1,
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
    )


def test_exact_range_and_visibility_boundary_have_distinct_contracts() -> None:
    exact = viewport_representation(0.2)
    assert exact.id == viewport_representation(0.75).id
    assert exact.id != viewport_representation(0.7501).id
    assert exact.metadata()["resolution_range"] == {
        "min": 0,
        "max": 0.75,
        "min_inclusive": False,
        "max_inclusive": True,
    }
    below = viewport_representation(2.2)
    above = viewport_representation(2.2001)
    assert below.simplify_tolerance == above.simplify_tolerance == 0.77
    assert below.id != above.id
    assert below.resolution_range.max == 2.2
    assert below.resolution_range.max_inclusive is True
    assert above.resolution_range.min == 2.2
    assert above.resolution_range.min_inclusive is False


@pytest.mark.parametrize(
    "resolution", [0.1, 0.75, 0.7501, 1, 1.0001, 2.2, 2.2001, 2.5, 10, 999]
)
def test_every_advertised_interval_preserves_the_actual_representation(
    resolution: float,
) -> None:
    representation = viewport_representation(resolution)
    interval = representation.resolution_range
    assert interval is not None
    first = (
        interval.min
        if interval.min_inclusive
        else nextafter(interval.min, interval.max)
    )
    last = (
        interval.max
        if interval.max_inclusive
        else nextafter(interval.max, interval.min)
    )
    probes = [
        first,
        last,
        *[first + (last - first) * fraction / 10 for fraction in range(1, 10)],
    ]
    assert all(
        viewport_representation(probe).id == representation.id for probe in probes
    )


def test_quantized_geometry_is_independent_of_cache_history(monkeypatch) -> None:
    project = project_with(feature("curved"))
    extent = (-1, -1, 3, 3)
    resolutions = [1.0001, 1.0002]
    # Before this change both resolutions had cache key .350 but computed
    # tolerances .350035 / .350070, on opposite sides of this line's bend.
    cold = [IndexedGeometryQuery().query(project, extent, r) for r in resolutions]
    assert (
        cold[0].feature_collection["features"] == cold[1].feature_collection["features"]
    )
    assert (
        len(cold[0].feature_collection["features"][0]["geometry"]["coordinates"]) == 3
    )

    original = BaseGeometry.simplify
    calls: list[float] = []

    def simplify(self, tolerance, preserve_topology=True):
        calls.append(tolerance)
        return original(self, tolerance, preserve_topology=preserve_topology)

    monkeypatch.setattr(BaseGeometry, "simplify", simplify)
    for order in [resolutions, list(reversed(resolutions))]:
        query = IndexedGeometryQuery()
        calls.clear()
        for resolution in order:
            warm = query.query(project, extent, resolution)
            assert (
                warm.feature_collection["features"]
                == cold[0].feature_collection["features"]
            )
            assert warm.feature_collection["metadata"]["simplify_tolerance"] == 0.35
        assert calls == [0.35]


def test_visibility_changes_even_when_the_quantized_tolerance_does_not() -> None:
    project = project_with(feature("road"), feature("setback", kind="forbidden"))
    query = IndexedGeometryQuery()
    below = query.query(project, (-1, -1, 3, 3), 2.2).feature_collection
    above = query.query(project, (-1, -1, 3, 3), 2.2001).feature_collection
    assert {item["id"] for item in below["features"]} == {"road", "setback"}
    assert {item["id"] for item in above["features"]} == {"road"}
    assert above["metadata"]["total_matches"] == 1
    assert (
        below["metadata"]["representation_id"] != above["metadata"]["representation_id"]
    )


def test_response_completeness_does_not_change_representation_or_source() -> None:
    project = project_with(feature("near"), feature("far", 10))
    original = project.source_geometry.model_dump_json()
    query = IndexedGeometryQuery(max_features=1)
    small = query.query(project, (-1, -1, 3, 3), 0.1).feature_collection
    large = query.query(project, (-1, -1, 20, 3), 0.7).feature_collection
    assert small["metadata"]["lod"] == "detail"
    assert large["metadata"]["lod"] == "budgeted"
    assert large["metadata"]["truncated"] is True
    assert (
        small["metadata"]["representation_id"] == large["metadata"]["representation_id"]
    )
    assert project.source_geometry.model_dump_json() == original
