"""Точное представление большого viewport, метаданные отбора и срок жизни исходника."""

import gc
import math
import weakref
from decimal import Decimal
from enum import Enum
from unittest.mock import Mock

import pytest

from app.dxf_import.contracts import ImportMode, ImportStatus
from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.feature_storage import ViewportFeatures
from app.geometry.viewport.queries import SpatialQueries
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.projects.adapters import project_projection
from app.projects.contracts import Project
from app.projects.ports import ProjectGeometryView


def feature(index, properties=None):
    return {
        "type": "Feature",
        "id": f"объект-{index}",
        "bbox": [0, 0, 10, 10],
        "author_data": {"unknown": [True, None, 2**100, -0.0, 1.2345678901234567]},
        "properties": {
            "kind": "road",
            "source_layer": "Дороги",
            "source_handle": f"A{index}",
            "source_instance_chain": ["BLOCK", f"PART-{index}"],
            "source_geometry_content_sha256": "a" * 64,
            "source_sampling_tolerance_m": 0.001,
            "custom": {"title": "Схема", "attributes": [["X", 9], ["Y", False]]},
            **(properties or {}),
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[0.0, -0.0, 3], [10, 0, 3], [10, 10, 3], [0, 10, 3], [0, -0.0, 3]],
                [[2, 2, 3], [3, 2, 3], [3, 3, 3], [2, 3, 3], [2, 2, 3]],
            ],
        },
    }


def project(features, *, calculated=False):
    graph = GeometrySnapshot(
        feature_collection={"type": "FeatureCollection", "features": features}
    )
    return Project(
        id="compact",
        name="Полный чертёж",
        geometry_version=1,
        import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
        geometry=graph if calculated else None,
        source_geometry=None if calculated else graph,
    )


@pytest.mark.parametrize("calculated", [False, True])
@pytest.mark.parametrize("resolution", [0.1, 2.4, 3.0])
@pytest.mark.parametrize("budget", [2, 20])
def test_compact_viewport_matches_full_dictionary_response(
    calculated, resolution, budget
):
    features = [
        feature(index, {"kind": kind, "source_layer": f"Слой {index % 2}"})
        for index, kind in enumerate(
            ["road", "utility", "existing_green", "allowed", "forbidden", "ignore"]
        )
    ]
    current = project(features, calculated=calculated)
    before = current.model_dump_json()
    ordinary = IndexedGeometryQuery(max_features=budget, max_cached_features=100)
    compact = IndexedGeometryQuery(max_features=budget, max_cached_features=1)
    expected = ordinary.query(current, (-1, -1, 12, 12), resolution)
    actual = compact.query(current, (-1, -1, 12, 12), resolution)
    assert compact._indexes[current.id].features.encoded_count == len(features)
    assert actual == expected
    assert actual.model_dump_json() == expected.model_dump_json()
    assert (
        compact.query_cached(project_projection(current), (-1, -1, 12, 12), resolution)
        == expected
    )
    assert current.model_dump_json() == before


def test_compact_selection_preserves_absent_none_and_false_metadata():
    features = [feature(index) for index in range(6)]
    del features[0]["id"]
    features[0]["properties"] = {"source_handle": None, "source_layer": "Без вида"}
    features[1]["id"] = None
    features[1]["properties"] = {"kind": None, "source_layer": 0}
    del features[2]["properties"]["kind"]
    features[2]["properties"]["source_layer"] = None
    del features[3]["properties"]["source_layer"]
    features[4]["properties"]["kind"] = 0
    features[5]["properties"]["source_layer"] = ""
    current = project(features)
    compact = IndexedGeometryQuery(max_features=3, max_cached_features=1)
    ordinary = IndexedGeometryQuery(max_features=3, max_cached_features=100)
    assert compact.query(current, (-1, -1, 12, 12), 3) == ordinary.query(
        current, (-1, -1, 12, 12), 3
    )


@pytest.mark.parametrize("byte_budget,decoded_count", [(8 * 1024 * 1024, 2), (2, 4)])
def test_selection_and_overview_decode_only_bounded_admission_candidates(
    monkeypatch, byte_budget, decoded_count
):
    current = project([feature(index) for index in range(8)])
    compact = IndexedGeometryQuery(
        max_features=2, max_cached_features=1, max_feature_bytes=byte_budget
    )
    ordinary = IndexedGeometryQuery(
        max_features=2, max_cached_features=100, max_feature_bytes=byte_budget
    )
    original = ViewportFeatures.feature
    decoded = []

    def observe(storage, index):
        if storage.compact:
            decoded.append(index)
        return original(storage, index)

    monkeypatch.setattr(ViewportFeatures, "feature", observe)
    expected = ordinary.query(current, (-1, -1, 12, 12), 3)
    actual = compact.query(current, (-1, -1, 12, 12), 3)
    assert actual == expected
    assert len(decoded) == decoded_count
    assert actual.feature_collection["source_overview"]["source_features"] == 8
    decoded.clear()
    assert (
        compact.query_cached(project_projection(current), (-1, -1, 12, 12), 3)
        == expected
    )
    assert len(decoded) == len(expected.feature_collection["features"])


@pytest.mark.parametrize("resolution", [0.1, 3.0])
@pytest.mark.parametrize("byte_budget", [2, 4000])
def test_warm_admission_decodes_only_returned_features_without_changing_response(
    monkeypatch, resolution, byte_budget
):
    current = project(
        [feature(index, {"source_layer": f"Слой {index % 2}"}) for index in range(24)]
    )
    original = current.model_dump_json()
    query = IndexedGeometryQuery(
        max_features=24, max_cached_features=1, max_feature_bytes=byte_budget
    )
    expected = query.query(current, (-1, -1, 12, 12), resolution)
    assert len(expected.feature_collection["features"]) < 12
    storage = query._indexes[current.id].features
    assert storage.encoded_count == 24
    decode = Mock(wraps=storage.feature)
    monkeypatch.setattr(storage, "feature", decode)

    actual = query.query_cached(
        project_projection(current), (-1, -1, 12, 12), resolution
    )

    assert actual.model_dump_json() == expected.model_dump_json()
    returned = actual.feature_collection["features"]
    assert decode.call_count == len(returned)
    assert actual.feature_collection["source_overview"]["rendered_features"] == 24
    assert current.model_dump_json() == original
    for item in returned:
        assert item["author_data"]["unknown"][2] == 2**100
        assert len(item["geometry"]["coordinates"]) == 2
        assert item["properties"]["source_instance_chain"]


def test_warm_simplification_fallback_keeps_diagnostics_for_rejected_candidates(
    monkeypatch,
):
    from app.geometry.viewport.viewport_simplification import ViewportSimplifier

    monkeypatch.setattr(
        ViewportSimplifier,
        "simplify",
        lambda self, index, geometry, tolerance, **options: (geometry, "test_fallback"),
    )
    current = project([feature(index) for index in range(24)])
    query = IndexedGeometryQuery(
        max_features=12, max_cached_features=1, max_feature_bytes=4000
    )
    expected = query.query(current, (-1, -1, 12, 12), 3)
    assert expected.feature_collection["metadata"]["simplification_fallbacks"] == {
        "test_fallback": 24
    }
    actual = query.query_cached(project_projection(current), (-1, -1, 12, 12), 3)
    assert actual.model_dump_json() == expected.model_dump_json()


def test_scalar_size_cache_does_not_substitute_missing_simplified_geometry():
    current = project([feature(index) for index in range(24)])
    query = IndexedGeometryQuery(
        max_features=12, max_cached_features=1, max_feature_bytes=4000
    )
    expected = query.query(current, (-1, -1, 12, 12), 3)
    indexed = query._indexes[current.id]
    assert indexed.display_sizes
    indexed.simplified_geometries.clear()
    indexed.simplified_sizes.clear()
    indexed.simplified_bytes = 0
    actual = query.query_cached(project_projection(current), (-1, -1, 12, 12), 3)
    assert actual.model_dump_json() == expected.model_dump_json()


def test_feature_storage_keeps_precision_unknown_fields_and_signed_zero():
    original = feature(1)
    storage = ViewportFeatures(compact=True)
    storage.append(original)
    assert storage.encoded_count == 1
    restored = storage.feature(0)
    assert restored == original
    assert restored["author_data"]["unknown"][2] == 2**100
    assert math.copysign(1, restored["geometry"]["coordinates"][0][0][1]) == -1
    assert math.copysign(1, restored["author_data"]["unknown"][3]) == -1
    restored["properties"]["custom"]["title"] = "Изменено"
    assert storage.feature(0)["properties"]["custom"]["title"] == "Схема"
    assert original["properties"]["custom"]["title"] == "Схема"


@pytest.mark.parametrize(
    "value", [(1, 2), Decimal("1.2"), float("nan"), float("inf"), {1: "ключ"}]
)
def test_non_json_compatible_values_keep_original_feature(value):
    original = feature(0)
    original["properties"]["legacy"] = value
    storage = ViewportFeatures(compact=True)
    storage.append(original)
    assert storage.encoded_count == 0
    assert storage.feature(0) is original
    assert storage.metadata(0) is original


def test_enum_metadata_with_different_string_semantics_keeps_dictionary_path():
    class LegacyKind(str, Enum):  # noqa: UP042 - проверяем прежнее толкование Enum
        ROAD = "road"

    current = project([feature(0, {"kind": LegacyKind.ROAD}), feature(1)])
    ordinary = IndexedGeometryQuery(max_features=1, max_cached_features=100)
    compact = IndexedGeometryQuery(max_features=1, max_cached_features=1)
    assert compact.query(current, (-1, -1, 12, 12), 3) == ordinary.query(
        current, (-1, -1, 12, 12), 3
    )
    assert compact._indexes[current.id].features.encoded_count == 1


def test_compact_index_releases_original_containers_after_cold_reader_returns():
    class Properties(dict):
        pass

    class Geometry(dict):
        pass

    class Coordinates(list):
        pass

    features = [feature(index) for index in range(4)]
    features[0]["properties"] = Properties(features[0]["properties"])
    features[0]["geometry"] = Geometry(features[0]["geometry"])
    features[0]["geometry"]["coordinates"] = Coordinates(
        features[0]["geometry"]["coordinates"]
    )
    properties = weakref.ref(features[0]["properties"])
    geometry = weakref.ref(features[0]["geometry"])
    coordinates = weakref.ref(features[0]["geometry"]["coordinates"])
    current = project(features)
    metadata = project_projection(current)
    graph = current.source_geometry
    repository = Mock()
    repository.get.return_value = metadata
    repository.get_geometry_view.return_value = ProjectGeometryView(
        id=current.id,
        geometry_version=current.geometry_version,
        import_status=current.import_status,
        geometry=None,
        source_geometry=graph,
        site_area_m2=None,
        planning_area_m2=None,
        allowed_area_m2=None,
    )
    query = IndexedGeometryQuery(max_features=2, max_cached_features=1)
    response = SpatialQueries(repository, query, Mock()).query_geometry(
        current.id, (-1, -1, 12, 12), 0.1
    )
    repository.get_geometry_view.return_value = None
    del features, current, graph
    gc.collect()
    assert properties() is None and geometry() is None and coordinates() is None
    assert query.cached_project_count == 1
    assert len(response.feature_collection["features"]) == 2
    assert query.query_cached(metadata, (-1, -1, 12, 12), 0.1) == response


def test_compact_metadata_releases_original_strings_and_nested_values():
    class SourceText(str):
        pass

    class SourceIdentity(dict):
        pass

    original = feature(0)
    original["id"] = SourceIdentity({"address": SourceText("native-instance")})
    for name in ("kind", "source_layer", "source_handle"):
        original["properties"][name] = SourceText(original["properties"][name])
    references = [weakref.ref(original["id"]), weakref.ref(original["id"]["address"])]
    references.extend(
        weakref.ref(original["properties"][name])
        for name in ("kind", "source_layer", "source_handle")
    )
    expected_metadata = {
        "id": {"address": "native-instance"},
        "properties": {"kind": "road", "source_layer": "Дороги", "source_handle": "A0"},
    }
    storage = ViewportFeatures(compact=True)
    storage.append(original)
    assert storage.encoded_count == 1
    del original
    gc.collect()
    assert all(reference() is None for reference in references)
    assert storage.metadata(0) == expected_metadata
    assert storage.feature(0)["id"] == expected_metadata["id"]


def test_small_index_keeps_dictionary_path_and_revision_invalidates_compact_index():
    current = project([feature(0), feature(1)])
    small = IndexedGeometryQuery(max_cached_features=2)
    small.query(current, (-1, -1, 12, 12), 0.1)
    assert small._indexes[current.id].features.encoded_count == 0
    compact = IndexedGeometryQuery(max_cached_features=1)
    compact.query(current, (-1, -1, 12, 12), 0.1)
    old = weakref.ref(compact._indexes[current.id])
    changed = current.model_copy(deep=True)
    changed.geometry_version += 1
    changed.source_geometry.feature_collection["features"][0]["author_data"] = {
        "new": 1
    }
    assert (
        compact.query_cached(project_projection(changed), (-1, -1, 12, 12), 0.1) is None
    )
    gc.collect()
    assert old() is None
    expected = IndexedGeometryQuery().query(changed, (-1, -1, 12, 12), 0.1)
    assert compact.query(changed, (-1, -1, 12, 12), 0.1) == expected
    assert current.source_geometry.feature_collection["features"][0] == feature(0)
