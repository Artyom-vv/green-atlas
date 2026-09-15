from collections import Counter

import pytest

import app.geometry.query_adapters as query_module
from app.geometry.contracts import GeometrySnapshot
from app.geometry.query_adapters import IndexedGeometryQuery
from app.geometry.viewport_budget import feature_size
from app.projects.contracts import Project

EXTENT = (-1.0, -1.0, 100.0, 100.0)


def feature(identity: str, kind: str, layer: str, *, vertices: int = 2) -> dict:
    return {
        "type": "Feature",
        "id": identity,
        "properties": {"kind": kind, "source_layer": layer},
        "geometry": {
            "type": "LineString",
            "coordinates": [[index, 0] for index in range(vertices)],
        },
    }


def project_with(features: list[dict]) -> Project:
    return Project(
        name="Display fairness",
        geometry_version=1,
        source_geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
    )


def test_dense_utilities_share_feature_budget_with_context_and_other_layers() -> None:
    features = [
        feature(f"{layer}-{index:03}", "utility", layer)
        for layer, count in (("A cables", 200), ("B water", 20), ("C heat", 5))
        for index in range(count)
    ]
    features.extend(
        feature(f"{kind}-{index:03}", kind, kind)
        for kind, count in (
            ("building", 20),
            ("site_border", 12),
            ("existing_green", 30),
        )
        for index in range(count)
    )
    project = project_with(features)
    original = project.source_geometry.model_dump_json()
    query = IndexedGeometryQuery(max_features=36)
    result = query.query(project, EXTENT, 0.1).feature_collection
    returned = result["features"]

    assert Counter(item["properties"]["kind"] for item in returned) == {
        "building": 9,
        "site_border": 9,
        "existing_green": 9,
        "utility": 9,
    }
    assert Counter(
        item["properties"]["source_layer"]
        for item in returned
        if item["properties"]["kind"] == "utility"
    ) == {"A cables": 3, "B water": 3, "C heat": 3}
    metadata = result["metadata"]
    assert metadata["returned_features"] == 36
    assert metadata["total_matches"] == len(features)
    assert metadata["truncated"] is True
    assert metadata["truncation_reasons"] == ["features"]
    assert sum(metadata["omitted_by_kind"].values()) == len(features) - 36
    assert project.source_geometry.model_dump_json() == original
    assert query.query_cached(project, EXTENT, 0.1).feature_collection == result

    # Stable CAD identities determine the budgeted subset, not INSERT/source order.
    reordered = query.query(project_with(list(reversed(features))), EXTENT, 0.1)
    assert [item["id"] for item in reordered.feature_collection["features"]] == [
        item["id"] for item in returned
    ]


def test_large_first_geometry_cannot_consume_other_kinds_coordinate_shares() -> None:
    features = [feature("large", "utility", "A cables", vertices=12)]
    features.extend(
        feature(f"building-{index}", "building", "Buildings") for index in range(3)
    )
    features.extend(feature(f"road-{index}", "road", "Roads") for index in range(2))
    features.extend(
        feature(f"water-{index}", "utility", "B water") for index in range(3)
    )
    result = (
        IndexedGeometryQuery(max_coordinates=12)
        .query(project_with(features), EXTENT, 0.1)
        .feature_collection
    )

    assert set(result["metadata"]["returned_by_kind"]) == {
        "building",
        "road",
        "utility",
    }
    assert "large" not in {item["id"] for item in result["features"]}
    assert result["metadata"]["returned_coordinates"] == 12
    assert result["metadata"]["truncation_reasons"] == ["coordinates"]
    assert sum(result["metadata"]["omitted_by_kind"].values()) == len(features) - len(
        result["features"]
    )


def test_byte_heavy_layer_cannot_starve_a_smaller_layer_of_the_same_kind() -> None:
    heavy = feature("a-heavy", "utility", "A cables")
    heavy["properties"]["source_text"] = "x" * 4000
    small = [feature(f"water-{index}", "utility", "B water") for index in range(3)]
    byte_budget = feature_size(heavy)[1] + 2
    result = (
        IndexedGeometryQuery(max_feature_bytes=byte_budget)
        .query(project_with([heavy, *small]), EXTENT, 0.1)
        .feature_collection
    )

    assert {item["id"] for item in result["features"]} == {item["id"] for item in small}
    assert result["metadata"]["returned_bytes"] <= byte_budget
    assert result["metadata"]["omitted_by_kind"] == {"utility": 1}
    assert result["metadata"]["truncation_reasons"] == ["bytes"]


def test_whole_feature_can_borrow_unused_shares_without_false_truncation() -> None:
    heavy = feature("heavy", "utility", "Cables")
    heavy["properties"]["source_text"] = "x" * 1000
    road = feature("road", "road", "Roads")
    byte_budget = sum(feature_size(item)[1] for item in (heavy, road)) + 3
    result = (
        IndexedGeometryQuery(max_feature_bytes=byte_budget)
        .query(project_with([heavy, road]), EXTENT, 0.1)
        .feature_collection
    )

    assert [item["id"] for item in result["features"]] == ["heavy", "road"]
    assert result["metadata"]["returned_bytes"] == byte_budget
    assert result["metadata"]["truncated"] is False
    assert result["metadata"]["omitted_by_kind"] == {}


def test_unknown_detail_cannot_displace_available_physical_context() -> None:
    features = [feature(f"note-{index}", "ignore", "A labels") for index in range(30)]
    features.extend(
        feature(f"building-{index}", "building", "Z buildings") for index in range(4)
    )
    result = (
        IndexedGeometryQuery(max_features=4)
        .query(project_with(features), EXTENT, 0.1)
        .feature_collection
    )
    assert result["metadata"]["returned_by_kind"] == {"building": 4}
    assert result["metadata"]["omitted_by_kind"] == {"ignore": 30}


@pytest.mark.parametrize("resource", ["bytes", "coordinates"])
def test_rejected_primary_candidate_is_replaced_by_same_layer(resource: str) -> None:
    heavy = feature("a-heavy", "building", "Buildings", vertices=20)
    heavy["properties"]["source_text"] = "x" * 4000
    small = feature("z-small", "building", "Buildings")
    utility = feature("u-small", "utility", "Utilities")
    options = (
        {"max_feature_bytes": feature_size(small)[1] + feature_size(utility)[1] + 3}
        if resource == "bytes"
        else {"max_coordinates": 4}
    )
    result = (
        IndexedGeometryQuery(max_features=2, **options)
        .query(project_with([heavy, small, utility]), EXTENT, 0.1)
        .feature_collection
    )
    assert {item["id"] for item in result["features"]} == {"z-small", "u-small"}
    assert result["metadata"]["returned_features"] == 2
    assert result["metadata"]["omitted_by_kind"] == {"building": 1}
    assert set(result["metadata"]["truncation_reasons"]) == {"features", resource}


def test_all_fit_primary_window_preserves_warm_size_and_lod_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original_size = query_module.feature_size

    def counted_size(item: dict) -> tuple[int, int]:
        calls.append(item["id"])
        return original_size(item)

    monkeypatch.setattr(query_module, "feature_size", counted_size)
    project = project_with(
        [
            feature(f"{kind}-{index:03}", kind, kind)
            for kind in ("utility", "building")
            for index in range(20)
        ]
    )
    query = IndexedGeometryQuery(max_features=4)
    first = query.query(project, EXTENT, 1)
    cached_geometries = dict(query._indexes[project.id].simplified_geometries)
    assert len(calls) == 4
    assert query.query(project, EXTENT, 1) == first
    assert len(calls) == 4
    assert query._indexes[project.id].simplified_geometries == cached_geometries


def test_refill_preparation_stops_at_one_extra_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    original_size = query_module.feature_size

    def counted_size(item: dict) -> tuple[int, int]:
        calls.append(item["id"])
        return original_size(item)

    monkeypatch.setattr(query_module, "feature_size", counted_size)
    features = [
        feature(f"a-heavy-{index:03}", "building", "Buildings", vertices=10)
        for index in range(20)
    ]
    features.append(feature("z-small", "building", "Buildings"))
    result = (
        IndexedGeometryQuery(max_features=2, max_coordinates=4)
        .query(project_with(features), EXTENT, 0.1)
        .feature_collection
    )
    assert len(calls) == 4
    assert result["features"] == []
    assert result["metadata"]["truncated"] is True
    assert result["metadata"]["omitted_by_kind"] == {"building": 21}
