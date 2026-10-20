"""Дисковое окно сохраняет контракт отбора без удержания полной GEOS-карты."""

import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import Event, local
from unittest.mock import Mock
from xml.etree import ElementTree as ET

import pytest
from shapely.affinity import translate
from shapely.geometry import mapping, shape

from app.dxf_import.contracts import ImportMode, ImportStatus
from app.dxf_import.layers.contracts import Layer
from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.cancellation import (
    ViewportRequestCancelled,
    viewport_cancellation,
)
from app.geometry.viewport.persistent_index import PersistentViewportTree
from app.geometry.viewport.queries import SpatialQueries
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.projects.adapters import SqliteProjectRepository, project_projection
from app.projects.contracts import Project
from app.projects.ports import ProjectGeometryView


def normalize_overview(snapshot):
    """STRtree и RTree имеют разный обход; сравниваем все пути и стили SVG."""
    document = deepcopy(snapshot.model_dump())
    overview = document["feature_collection"].get("source_overview")
    if overview and overview["svg"]:
        root = ET.fromstring(overview.pop("svg"))
        groups = []
        for group in root:
            paths = [path.attrib["d"] for path in group]
            if group.attrib["data-shape"] == "line":
                paths = [part for path in paths for part in re.findall(r"M[^M]*", path)]
            groups.append((sorted(group.attrib.items()), sorted(paths)))
        overview["normalized_svg"] = (root.attrib, groups)
    return document


class Resource:
    """Port fixture: records lazy coordinate access independently of SQLite tests."""

    def __init__(self, records):
        self.records = deepcopy(records)
        self.identity = "verified-source-v1"
        self.decoded = []
        self.full_features = []
        self.envelopes = []
        self.valid = {}
        for index, feature in enumerate(records):
            try:
                geometry = shape(feature["geometry"])
                if not geometry.is_empty:
                    self.valid[index] = geometry
            except Exception:
                pass
        self.count = len(self.valid)
        self.coordinates = 100

    def query_indexes(self, extent):
        x1, y1, x2, y2 = extent
        return [
            index
            for index, geometry in self.valid.items()
            if geometry.bounds[0] <= x2
            and geometry.bounds[2] >= x1
            and geometry.bounds[1] <= y2
            and geometry.bounds[3] >= y1
        ]

    def metadata(self, index):
        feature = self.records[index]
        result = {
            "properties": {
                name: value
                for name, value in feature.get("properties", {}).items()
                if name in {"kind", "source_layer", "source_handle"}
            }
        }
        if "id" in feature:
            result["id"] = feature["id"]
        return result

    def iter_metadata(self, indexes):
        for index in indexes:
            yield int(index), self.metadata(int(index))

    def feature(self, index):
        self.full_features.append(index)
        return deepcopy(self.records[index])

    def envelope(self, index):
        self.envelopes.append(index)
        result = deepcopy(self.records[index])
        result.pop("geometry", None)
        return result

    def geometry(self, index):
        self.decoded.append(index)
        return shape(self.records[index]["geometry"])

    def iter_geometries(self, indexes):
        for index in indexes:
            yield int(index), self.geometry(int(index))

    def iter_indexes(self):
        yield from self.valid

    def iter_scene_candidates(self, tree_layers, curb_layers):
        for index, geometry in self.valid.items():
            props = self.records[index].get("properties", {})
            if (
                props.get("source_layer") in tree_layers
                or (
                    props.get("source_layer") in curb_layers
                    and props.get("kind") in {"road", "lawn"}
                    and geometry.geom_type in {"LineString", "MultiLineString"}
                )
                or (
                    props.get("kind") in {"building", "road", "lawn", "water"}
                    and geometry.geom_type in {"Polygon", "MultiPolygon"}
                )
            ):
                yield self.feature(index)


class Repository:
    def __init__(self, project, resource):
        self.project = project
        self.resource = resource
        self.active = 0
        self.opens = 0
        self.fail_after_read = False

    def get(self, project_id, *, lightweight=False):
        assert project_id == self.project.id and lightweight
        return project_projection(self.project)

    def consume_geometry_view(self, *_):
        raise AssertionError("The persistent viewport must not consume a full snapshot")

    def get_geometry_view(self, *_):
        raise AssertionError("The persistent viewport must not hydrate GeoJSON")

    @contextmanager
    def open_display_geometry(self, project_id, *, wait_check=None):
        assert project_id == self.project.id
        self.active += 1
        self.opens += 1
        project = project_projection(self.project)
        view = ProjectGeometryView(
            id=project.id,
            geometry_version=project.geometry_version,
            import_status=project.import_status,
            geometry=project.geometry,
            source_geometry=project.source_geometry,
            site_area_m2=project.site_area_m2,
            planning_area_m2=project.planning_area_m2,
            allowed_area_m2=project.allowed_area_m2,
        )
        try:
            yield view, self.resource
            if self.fail_after_read:
                raise ValueError("resource changed after viewport preparation")
        finally:
            self.active -= 1


def feature(index, geometry, kind="road", layer="Дороги"):
    return {
        "type": "Feature",
        "id": f"source-{index}",
        "geometry": geometry,
        "properties": {
            "kind": kind,
            "source_layer": layer,
            "source_handle": str(index),
            "source_instance_chain": ["BLOCK", str(index)],
            "unknown": [False, None, 2**100],
        },
        "foreign": {"data": "точно"},
    }


def project(records, *, calculated=True):
    graph = GeometrySnapshot(feature_collection={"features": records})
    return Project(
        name="Persistent viewport",
        geometry_version=1,
        import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
        geometry=graph if calculated else None,
        source_geometry=None if calculated else graph,
    )


@pytest.fixture
def records():
    result = [None, {}, feature(2, None)]
    for index, kind in enumerate(
        ["road", "building", "utility", "allowed", "forbidden", "ignore"], start=3
    ):
        result.append(
            feature(
                index,
                {
                    "type": "Polygon",
                    "coordinates": [
                        [[0, 0, 3], [10, 0, 3], [10, 10, 3], [0, 10, 3], [0, 0, 3]],
                        [[2, 2, 3], [3, 2, 3], [3, 3, 3], [2, 3, 3], [2, 2, 3]],
                    ],
                },
                kind=kind,
                layer=f"Слой {index % 2}",
            )
        )
    result.append(feature(9, {"type": "GeometryCollection", "geometries": []}))
    result.append(feature(10, {"type": "Point", "coordinates": [100, 100]}))
    result.append(feature(11, {"type": "LineString", "coordinates": [[0, 0], [4, 4]]}))
    del result[3]["id"]
    result[4]["id"] = None
    return result


@pytest.mark.parametrize("resolution", [0.1, 2.4, 3.0])
@pytest.mark.parametrize("max_features", [2, 30])
@pytest.mark.parametrize(
    "limits", [{}, {"max_coordinates": 12}, {"max_feature_bytes": 2}]
)
@pytest.mark.parametrize("calculated", [False, True])
def test_persistent_window_preserves_exact_selection_budgets_and_overview(
    records, resolution, max_features, limits, calculated
):
    current = project(records, calculated=calculated)
    resource = Resource(records)
    repository = Repository(current, resource)
    ordinary = IndexedGeometryQuery(max_features=max_features, **limits)
    persistent = IndexedGeometryQuery(max_features=max_features, **limits)
    spatial = SpatialQueries(repository, persistent, Mock())
    extent = (-1, -1, 12, 12)
    expected = ordinary.query(current, extent, resolution)
    actual = spatial.query_geometry(current.id, extent, resolution)
    assert normalize_overview(actual) == normalize_overview(expected)
    assert normalize_overview(
        spatial.query_geometry(current.id, extent, resolution)
    ) == normalize_overview(expected)
    assert repository.active == 0 and repository.opens == 2
    index = persistent._indexes[current.id]
    assert isinstance(index.tree, PersistentViewportTree)
    assert not isinstance(index.geometries, list)
    assert index.source_overviews._paths is None
    with pytest.raises(RuntimeError, match="не открыт"):
        index.features.feature(3)
    assert 10 not in resource.decoded and 10 not in resource.full_features


def test_narrow_window_does_not_decode_or_materialize_unselected_source():
    records = [
        feature(i, {"type": "Point", "coordinates": [i * 10, 0, 2.5]})
        for i in range(500)
    ]
    current = project(records)
    resource = Resource(records)
    query = IndexedGeometryQuery(max_features=20)
    spatial = SpatialQueries(Repository(current, resource), query, Mock())
    actual = spatial.query_geometry(current.id, (-1, -1, 1, 1), 0.1)
    assert actual.feature_collection["features"] == records[:1]
    assert resource.full_features == [0]
    assert resource.decoded == []


def test_direct_query_after_persistent_viewport_opens_verified_resource(records):
    current = project(records)
    repository = Repository(current, Resource(records))
    query = IndexedGeometryQuery(max_features=2)
    expected = query.query_streamed(repository, current.id, (-1, -1, 12, 12), 3)
    assert query.query(current, (-1, -1, 12, 12), 3) == expected
    assert repository.active == 0 and repository.opens == 2


def test_simplification_uses_envelope_and_warm_cache_skips_geometry_reads(
    records, monkeypatch
):
    current = project(records)
    expected = IndexedGeometryQuery(max_features=30).query(current, (-1, -1, 12, 12), 3)
    resource = Resource(records)
    repository = Repository(current, resource)
    query = IndexedGeometryQuery(max_features=30)
    spatial = SpatialQueries(repository, query, Mock())
    monkeypatch.setattr(
        resource,
        "feature",
        Mock(side_effect=AssertionError("original coordinates are not needed")),
    )
    actual = spatial.query_geometry(current.id, (-1, -1, 12, 12), 3)
    assert actual == expected
    assert resource.envelopes and resource.decoded
    resource.envelopes.clear()
    monkeypatch.setattr(
        resource,
        "geometry",
        Mock(side_effect=AssertionError("simplified geometry is already cached")),
    )
    assert spatial.query_geometry(current.id, (-1, -1, 12, 12), 3) == expected
    assert len(resource.envelopes) == len(actual.feature_collection["features"])
    assert actual.feature_collection["features"][0]["foreign"] == {"data": "точно"}


def test_admitted_cached_geometry_survives_eviction_during_other_materialization():
    from app.geometry.viewport.viewport_simplification import DisplayGeometry

    records = [
        feature(
            i, {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 0]]]}
        )
        for i in range(24)
    ]
    current = project(records)
    resource = Resource(records)
    query = IndexedGeometryQuery(
        max_features=12, max_feature_bytes=2000, max_cached_simplified_bytes=2000
    )
    spatial = SpatialQueries(Repository(current, resource), query, Mock())
    expected = spatial.query_geometry(current.id, (-1, -1, 12, 12), 3)
    selected_ids = {item["id"] for item in expected.feature_collection["features"]}
    selected = [i for i, record in enumerate(records) if record["id"] in selected_ids]
    rejected = [i for i in range(len(records)) if i not in selected]
    assert len(selected) > 2 and len(rejected) > 2
    index = query._indexes[current.id]
    index.simplified_geometries.clear()
    index.simplified_sizes.clear()
    index.simplified_bytes = 0
    tolerance = next(iter(index.display_sizes))[0]
    # Часть выбранных контуров пережила прошлое окно вместе с отклонёнными.
    # materialize обязан вернуть им приоритет после окончательного допуска.
    for ordinal in [selected[0], selected[-1], *rejected]:
        key = (tolerance, ordinal)
        size = index.display_sizes[key].feature_bytes
        if index.simplified_bytes + size > query.max_cached_simplified_bytes:
            break
        index.simplified_geometries[key] = DisplayGeometry(
            mapping(resource.valid[ordinal].simplify(tolerance, preserve_topology=True))
        )
        index.simplified_sizes[key] = size
        index.simplified_bytes += size
    assert spatial.query_geometry(current.id, (-1, -1, 12, 12), 3) == expected
    resource.decoded.clear()
    assert spatial.query_geometry(current.id, (-1, -1, 12, 12), 3) == expected
    assert resource.decoded == []
    assert index.simplified_bytes == sum(index.simplified_sizes.values()) <= 2000
    assert len(index.simplified_geometries) <= query.max_features


@pytest.mark.parametrize("outcome", ["fallback", "exception"])
def test_envelope_optimization_keeps_exact_original_geometry_on_refusal(
    records, monkeypatch, outcome
):
    from app.geometry.viewport.viewport_simplification import ViewportSimplifier

    def simplify(self, index, geometry, tolerance, **kwargs):
        if outcome == "exception":
            raise ValueError("simplification failed")
        return geometry, "coverage_simplification_failed"

    records[3]["geometry"]["foreign"] = {"preserve": [False, None, 2**100]}
    current = project(records)
    monkeypatch.setattr(ViewportSimplifier, "simplify", simplify)
    expected = IndexedGeometryQuery(max_features=30).query(current, (-1, -1, 12, 12), 3)
    resource = Resource(records)
    query = IndexedGeometryQuery(max_features=30)
    spatial = SpatialQueries(Repository(current, resource), query, Mock())
    actual = spatial.query_geometry(current.id, (-1, -1, 12, 12), 3)
    assert actual == expected
    assert resource.full_features
    assert (
        actual.feature_collection["features"][0]["geometry"] == records[3]["geometry"]
    )


@pytest.mark.parametrize("resolution,max_features", [(0.1, 30), (2.4, 30), (3.0, 2)])
@pytest.mark.parametrize("calculated", [False, True])
def test_real_sqlite_resource_matches_legacy_response_without_full_read(
    tmp_path, monkeypatch, records, resolution, max_features, calculated
):
    repository = SqliteProjectRepository(tmp_path / "actual.sqlite3")
    current = repository.create(project(records, calculated=calculated))
    ordinary = IndexedGeometryQuery(max_features=max_features)
    expected = ordinary.query(current, (-1, -1, 12, 12), resolution)
    monkeypatch.setattr(
        repository, "get_geometry_view", Mock(side_effect=AssertionError("full view"))
    )
    monkeypatch.setattr(
        repository._geometry_store,
        "read",
        Mock(side_effect=AssertionError("full graph")),
    )
    persistent = IndexedGeometryQuery(max_features=max_features)
    spatial = SpatialQueries(repository, persistent, Mock())
    try:
        actual = spatial.query_geometry(current.id, (-1, -1, 12, 12), resolution)
        assert normalize_overview(actual) == normalize_overview(expected)
        persistent.discard(current.id)
        monkeypatch.setattr(
            repository._geometry_store,
            "consume_features",
            Mock(side_effect=AssertionError("resource must survive index discard")),
        )
        assert normalize_overview(
            spatial.query_geometry(current.id, (-1, -1, 12, 12), resolution)
        ) == normalize_overview(expected)
    finally:
        repository.close()


def test_resource_identity_change_without_version_change_invalidates_caches(records):
    current = project(records)
    resource = Resource(records)
    repository = Repository(current, resource)
    query = IndexedGeometryQuery(max_features=20)
    spatial = SpatialQueries(repository, query, Mock())
    spatial.query_geometry(current.id, (-1, -1, 12, 12), 0.1)
    original = query._indexes[current.id]
    resource.identity = "verified-source-v2"
    resource.records[3]["foreign"]["data"] = "изменено"
    response = spatial.query_geometry(current.id, (-1, -1, 12, 12), 0.1)
    assert query._indexes[current.id] is not original
    assert response.feature_collection["features"][0]["foreign"]["data"] == "изменено"


@pytest.mark.parametrize("warm", [False, True])
def test_failed_final_integrity_check_discards_all_derived_window_caches(records, warm):
    current = project(records)
    repository = Repository(current, Resource(records))
    query = IndexedGeometryQuery(max_features=2)
    spatial = SpatialQueries(repository, query, Mock())
    if warm:
        spatial.query_geometry(current.id, (-1, -1, 12, 12), 3)
    repository.fail_after_read = True
    with pytest.raises(ValueError, match="changed after"):
        spatial.query_geometry(current.id, (-1, -1, 12, 12), 3)
    assert query.cached_project_count == 0 and repository.active == 0


def test_repeated_disk_window_skips_metadata_and_overlapping_svg_reuses_paths():
    class CountingResource(Resource):
        metadata_reads = 0

        def metadata(self, index):
            self.metadata_reads += 1
            return super().metadata(index)

    records = [
        feature(
            i,
            {"type": "LineString", "coordinates": [[i, 0], [i, 10]]},
        )
        for i in range(110)
    ]
    current = project(records)
    resource = CountingResource(records)
    repository = Repository(current, resource)
    query = IndexedGeometryQuery(max_features=2)
    spatial = SpatialQueries(repository, query, Mock())
    initial, panned = (-1, -1, 100.1, 11), (1.1, -1, 101.1, 11)
    first = spatial.query_geometry(current.id, initial, 1)
    indexed = query._indexes[current.id]
    # Один проход отбора и один отдельный полный проход обзора.
    assert resource.metadata_reads == 2 * len(resource.query_indexes(initial))
    assert indexed.selection_cache.metadata_passes == 1
    resource.metadata_reads = 0
    repeat = spatial.query_geometry(current.id, initial, 1)
    assert repeat == first
    assert resource.metadata_reads == 0
    assert indexed.selection_cache.hits == 1

    resource.decoded.clear()
    actual = spatial.query_geometry(current.id, panned, 1)
    expected = IndexedGeometryQuery(max_features=2).query(current, panned, 1)
    assert normalize_overview(actual) == normalize_overview(expected)
    # Не более четырёх picking-кандидатов и двух новых SVG-путей.
    assert len(resource.decoded) <= 6
    stats = indexed.source_overviews.stable_path_stats
    assert stats is not None and stats["hits"] >= 99
    assert stats["accounted_bytes"] <= stats["max_bytes"]


def test_failed_final_sha_clears_compact_selection_and_stable_svg_cache():
    records = [
        feature(i, {"type": "LineString", "coordinates": [[i, 0], [i, 10]]})
        for i in range(20)
    ]
    current = project(records)
    repository = Repository(current, Resource(records))
    query = IndexedGeometryQuery(max_features=2)
    spatial = SpatialQueries(repository, query, Mock())
    spatial.query_geometry(current.id, (-1, -1, 21, 11), 1)
    original = query._indexes[current.id]
    assert original.selection_cache.entry_count == 1
    assert original.source_overviews.stable_path_stats["entries"] > 0
    repository.fail_after_read = True
    with pytest.raises(ValueError, match="changed after"):
        spatial.query_geometry(current.id, (-1, -1, 22, 11), 1)
    assert original.selection_cache.entry_count == 0
    assert original.selection_cache.retained_bytes == 0
    assert original.source_overviews.stable_path_stats["entries"] == 0
    assert not original.source_overviews._drawings
    repository.fail_after_read = False
    expected = IndexedGeometryQuery(max_features=2).query(current, (-1, -1, 12, 12), 3)
    actual = spatial.query_geometry(current.id, (-1, -1, 12, 12), 3)
    assert normalize_overview(actual) == normalize_overview(expected)


def test_unverified_cache_is_never_visible_to_concurrent_verified_window(
    records, monkeypatch
):
    current = project(records)
    resource = Resource(records)
    mode = local()
    finalizing, release, captured_old_index = Event(), Event(), Event()

    class ConcurrentRepository(Repository):
        @contextmanager
        def open_display_geometry(self, *args, **kwargs):
            with super().open_display_geometry(*args, **kwargs) as opened:
                yield opened
                if getattr(mode, "tainted", False):
                    finalizing.set()
                    assert release.wait(2)
                    raise ValueError("failed final resource SHA")

    repository = ConcurrentRepository(current, resource)
    query = IndexedGeometryQuery(max_features=2)
    spatial = SpatialQueries(repository, query, Mock())
    spatial.query_geometry(current.id, (-1, -1, 12, 12), 3)
    old_index = query._indexes[current.id]
    original_geometry, original_index = resource.geometry, query._persistent_index

    def geometry(index):
        result = original_geometry(index)
        return (
            translate(result, xoff=100) if getattr(mode, "tainted", False) else result
        )

    def index(*args, **kwargs):
        result = original_index(*args, **kwargs)
        if getattr(mode, "verified", False):
            assert result is old_index
            captured_old_index.set()
        return result

    monkeypatch.setattr(resource, "geometry", geometry)
    monkeypatch.setattr(query, "_persistent_index", index)

    def request(*, tainted=False, verified=False):
        mode.tainted, mode.verified = tainted, verified
        return spatial.query_geometry(current.id, (-1, -1, 12, 12), 4)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(request, tainted=True)
        try:
            assert finalizing.wait(2)
            second = executor.submit(request, verified=True)
            assert captured_old_index.wait(2)
            assert not second.done()
        finally:
            release.set()
        with pytest.raises(ValueError, match="final resource SHA"):
            first.result(2)
        actual = second.result(2)
    expected = IndexedGeometryQuery(max_features=2).query(current, (-1, -1, 12, 12), 4)
    assert normalize_overview(actual) == normalize_overview(expected)
    assert repository.active == 0


def test_cancelled_window_closes_reader_and_never_reuses_context(records):
    current = project(records)
    repository = Repository(current, Resource(records))
    query = IndexedGeometryQuery(max_features=2)

    def cancel():
        raise ViewportRequestCancelled()

    with viewport_cancellation(cancel), pytest.raises(ViewportRequestCancelled):
        query.query_streamed(repository, current.id, (-1, -1, 12, 12), 3)
    assert repository.active == 0
    response = query.query_streamed(repository, current.id, (-1, -1, 12, 12), 3)
    assert response.feature_collection["metadata"]["total_matches"] > 0
    assert repository.active == 0


def test_scene_can_read_selected_shapes_before_first_map_without_full_graph():
    records = [
        feature(0, {"type": "Point", "coordinates": [0, 0]}, layer="Деревья"),
        feature(
            1, {"type": "LineString", "coordinates": [[0, 0], [2, 2]]}, layer="Бордюр"
        ),
        feature(
            2, {"type": "LineString", "coordinates": [[0, 0], [2, 2]]}, layer="Прочее"
        ),
        feature(
            3,
            {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [2, 2], [0, 0]]]},
            kind="building",
        ),
    ]
    current = project(records)
    current.layers = [
        Layer(
            id="tree",
            source_name="Деревья",
            suggested_kind="existing_green",
            mapped_kind="existing_green",
            category="tree",
            mapping_confirmed=True,
            object_count=1,
            color="#000000",
        ),
        Layer(
            id="curb",
            source_name="Бордюр",
            suggested_kind="road",
            mapped_kind="road",
            category="curb",
            mapping_confirmed=True,
            object_count=1,
            color="#000000",
        ),
    ]
    resource = Resource(records)
    repository = Repository(current, resource)
    query = IndexedGeometryQuery(display_repository=repository)
    actual = query.cached_scene_features(project_projection(current))
    assert actual == [records[i] for i in [0, 1, 3]]
    assert Counter(resource.full_features) == Counter([0, 1, 3])
    assert query.cached_project_count == 0 and repository.active == 0
