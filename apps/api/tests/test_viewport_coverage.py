from collections.abc import Sequence
from copy import deepcopy

import pytest
import shapely
from shapely.errors import GEOSException
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry

from app.geometry import viewport_simplification as module
from app.geometry.contracts import GeometrySnapshot
from app.geometry.query_adapters import IndexedGeometryQuery
from app.projects.contracts import Project


def project_with(geometry: BaseGeometry, *, calculated: bool = True) -> Project:
    snapshot = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "id": "allowed-area",
                    "type": "Feature",
                    "properties": {"kind": "allowed", "label": "Allowed"},
                    "geometry": mapping(geometry),
                }
            ],
        }
    )
    return Project(
        name="Coverage display",
        geometry=snapshot if calculated else None,
        source_geometry=deepcopy(snapshot),
        geometry_version=1,
        allowed_area_m2=geometry.area,
    )


def coverage() -> MultiPolygon:
    polygon = Polygon(
        [(0, 0), (5, 0), (10, 0), (10, 10), (0, 10)],
        holes=[[(2, 2), (2, 4), (4, 4), (4, 2)]],
    )
    return MultiPolygon([polygon, box(100, 0, 110, 10)])


def test_coverage_keeps_complete_feature_identity_and_reuses_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = project_with(coverage())
    before = project.model_dump_json()
    validity_calls = []
    simplify_calls = []
    native_valid = shapely.coverage_is_valid
    native_simplify = shapely.coverage_simplify

    def valid(parts: Sequence[BaseGeometry]) -> bool:
        validity_calls.append(len(parts))
        return bool(native_valid(parts))

    def simplify(parts: Sequence[BaseGeometry], tolerance: float) -> object:
        simplify_calls.append(tolerance)
        return native_simplify(parts, tolerance)

    monkeypatch.setattr(module.shapely, "coverage_is_valid", valid)
    monkeypatch.setattr(module.shapely, "coverage_simplify", simplify)

    def global_simplify(*args: object, **kwargs: object) -> BaseGeometry:
        raise AssertionError("Calculated coverage must not use global DP simplify")

    monkeypatch.setattr(BaseGeometry, "simplify", global_simplify)
    query = IndexedGeometryQuery()
    first = query.query(project, (-1, -1, 12, 12), 1)
    feature = first.feature_collection["features"][0]
    actual = shape(feature["geometry"])
    assert isinstance(actual, MultiPolygon)
    assert actual.is_valid and len(actual.geoms) == 2
    assert len(actual.geoms[0].interiors) == 1
    assert actual.bounds[2] == 110  # no viewport-dependent partial geometry per ID
    assert feature["id"] == "allowed-area"
    assert feature["properties"] == {"kind": "allowed", "label": "Allowed"}
    assert query.query(project, (-1, -1, 12, 12), 1) == first
    query.query(project, (-1, -1, 12, 12), 2)
    assert validity_calls == [2]
    assert simplify_calls == [0.35, 0.7]
    metadata = first.feature_collection["metadata"]
    assert metadata["representation_id"].startswith("viewport-v2:")
    assert metadata["simplification_policy"]["coverage_tolerance_unit"] == (
        "sqrt_triangle_area_m"
    )
    assert first.allowed_area_m2 == project.allowed_area_m2
    assert project.model_dump_json() == before


@pytest.mark.parametrize("byte_budget", [100, 8 * 1024 * 1024])
def test_invalid_coverage_preserves_exact_geometry_and_reports_fallback(
    byte_budget: int,
) -> None:
    project = project_with(MultiPolygon([box(0, 0, 10, 10), box(5, 0, 15, 10)]))
    assert project.geometry is not None
    original = project.geometry.feature_collection["features"][0]
    query = IndexedGeometryQuery(max_feature_bytes=byte_budget)
    snapshot = query.query(project, (-1, -1, 20, 20), 1).feature_collection
    metadata = snapshot["metadata"]
    assert metadata["simplification_fallbacks"] == {"invalid_polygon_coverage": 1}
    if byte_budget == 100:
        assert snapshot["features"] == []
        assert metadata["truncation_reasons"] == ["bytes"]
        assert metadata["omitted_by_kind"] == {"allowed": 1}
    else:
        assert snapshot["features"] == [original]
        assert metadata["truncated"] is False
    assert query.query(project, (-1, -1, 20, 20), 1).feature_collection == snapshot


def test_coverage_failure_is_an_explicit_exact_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise GEOSException("Native simplifier failed")

    monkeypatch.setattr(module.shapely, "coverage_simplify", fail)
    project = project_with(coverage())
    assert project.geometry is not None
    result = IndexedGeometryQuery().query(project, (-1, -1, 120, 20), 1)
    assert (
        result.feature_collection["features"]
        == (project.geometry.feature_collection["features"])
    )
    assert result.feature_collection["metadata"]["simplification_fallbacks"] == {
        "coverage_simplification_failed": 1
    }


def test_new_geometry_revision_does_not_reuse_old_coverage_validation() -> None:
    project = project_with(coverage())
    assert project.geometry is not None
    query = IndexedGeometryQuery()
    assert not query.query(project, (-1, -1, 120, 20), 1).feature_collection[
        "metadata"
    ]["simplification_fallbacks"]
    project.geometry_version += 1
    project.geometry.feature_collection["features"][0]["geometry"] = mapping(
        MultiPolygon([box(0, 0, 10, 10), box(5, 0, 15, 10)])
    )
    assert query.query(project, (-1, -1, 120, 20), 1).feature_collection["metadata"][
        "simplification_fallbacks"
    ] == {"invalid_polygon_coverage": 1}


def test_source_multipolygon_keeps_existing_douglas_peucker_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*args: object, **kwargs: object) -> None:
        raise AssertionError("Source geometry must not enter calculated coverage path")

    monkeypatch.setattr(module.shapely, "coverage_is_valid", unexpected)
    project = project_with(coverage(), calculated=False)
    result = IndexedGeometryQuery().query(project, (-1, -1, 120, 20), 1)
    expected = coverage().simplify(0.35, preserve_topology=True)
    assert shape(result.feature_collection["features"][0]["geometry"]).equals_exact(
        expected, 0
    )
