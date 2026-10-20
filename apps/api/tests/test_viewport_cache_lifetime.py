"""Удержание полной карты и единственный холодный читатель без потери объектов."""

import gc
import weakref
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from unittest.mock import Mock

import pytest

from app.cad_bridge.contracts import CadSnapshotProvenance
from app.dxf_import.contracts import ImportMode, ImportStatus, SourceFile
from app.dxf_import.source.review_contracts import SourceReview
from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.queries import SpatialQueries
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.geometry.viewport.viewport_budget import feature_size
from app.native_query.live.live_client import LiveSession
from app.projects.adapters import project_projection
from app.projects.contracts import Project
from app.projects.ports import ProjectGeometryView


def drawing(identity: str, count: int, vertices: int = 2) -> Project:
    return Project(
        id=identity,
        name=identity,
        geometry_version=1,
        source_geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": f"{identity}-{index}",
                        "properties": {"kind": "road"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [float(x), float(index)] for x in range(vertices)
                            ],
                        },
                    }
                    for index in range(count)
                ],
            }
        ),
    )


@pytest.mark.parametrize("budget", ["features", "coordinates"])
def test_large_index_evicts_prior_drawing_without_truncating_geometry(budget):
    query = IndexedGeometryQuery(
        max_cached_projects=32,
        max_cached_features=3 if budget == "features" else 100,
        max_cached_coordinates=6 if budget == "coordinates" else 100,
    )
    previous = drawing("previous", 2)
    query.query(previous, (-1, -1, 5, 5), 0.1)
    previous_index = weakref.ref(query._indexes[previous.id])
    previous_geometry = weakref.ref(query._indexes[previous.id].geometries[0])
    del previous
    active = drawing("active", 4)
    before = active.source_geometry.model_dump_json()
    response = query.query(active, (-1, -1, 5, 5), 0.1)
    gc.collect()

    assert previous_index() is None and previous_geometry() is None
    assert [feature["id"] for feature in response.feature_collection["features"]] == [
        f"active-{index}" for index in range(4)
    ]
    assert not response.feature_collection["metadata"]["truncated"]
    assert active.source_geometry.model_dump_json() == before
    assert (
        query.query_cached(project_projection(active), (-1, -1, 5, 5), 0.1) == response
    )
    active_index = weakref.ref(query._indexes[active.id])
    del response
    query.discard(active.id)
    gc.collect()
    assert active_index() is None


def test_cold_viewport_queries_share_one_full_source_load():
    project = drawing("concurrent", 3)
    entered, release, second_miss = Event(), Event(), Event()
    counter_lock = Lock()

    class Repository:
        full_reads = 0
        lightweight_reads = 0

        def get(self, project_id, *, lightweight=False):
            assert project_id == project.id
            if lightweight:
                with counter_lock:
                    self.lightweight_reads += 1
                    if self.lightweight_reads >= 3:
                        second_miss.set()
                return project_projection(project)
            self.full_reads += 1
            entered.set()
            assert release.wait(5)
            return project.model_copy(deep=True)

        def get_geometry_view(self, project_id):
            current = self.get(project_id)
            return ProjectGeometryView(
                id=current.id,
                geometry_version=current.geometry_version,
                import_status=current.import_status,
                geometry=current.geometry,
                source_geometry=current.source_geometry
                if current.geometry is None
                else None,
                site_area_m2=current.site_area_m2,
                planning_area_m2=current.planning_area_m2,
                allowed_area_m2=current.allowed_area_m2,
            )

    repository = Repository()
    queries = SpatialQueries(repository, IndexedGeometryQuery(), Mock())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(queries.query_geometry, project.id, (-1, -1, 5, 5), 0.1)
        assert entered.wait(5)
        second = executor.submit(
            queries.query_geometry, project.id, (-1, -1, 5, 5), 0.1
        )
        assert second_miss.wait(5)
        release.set()
        assert first.result(5) == second.result(5)
    assert repository.full_reads == 1


@pytest.mark.parametrize("scenario", ["large_source", "new_revision", "small_source"])
@pytest.mark.parametrize("editor", [True, False], ids=["editor", "calculated"])
def test_native_drawing_releases_obsolete_index_before_full_source_load(
    scenario, editor
):
    query = IndexedGeometryQuery(max_cached_projects=32, max_cached_features=3)
    previous = drawing("previous", 2)
    query.query(previous, (-1, -1, 5, 5), 0.1)
    previous_index = weakref.ref(query._indexes[previous.id])
    del previous
    identity = "previous" if scenario == "new_revision" else "active"
    count = 4 if scenario == "large_source" else 1
    active = drawing(identity, count)
    active.geometry_version = 2
    active.geometry = active.source_geometry
    active.source_geometry = None
    active.source_review = SourceReview() if editor else None
    active.import_status = ImportStatus(mode=ImportMode.AUTOCAD_LIVE)
    active.source_file = SourceFile(
        name="source.dwg",
        content_sha256="c" * 64,
        size=1,
        imported_at="2026-10-12",
        dxf_version="AutoCAD live snapshot",
        units="м",
        entity_count=count,
        native_session=LiveSession(
            session_id="a" * 32,
            pid=1,
            plugin_version="0.1.40",
            source_path="source.dwg",
            source_sha256="b" * 64,
            snapshot_path="snapshot.json",
            snapshot_sha256="c" * 64,
            units_code=6,
            watched_databases=1,
            unavailable_xrefs=0,
        ),
        cad_snapshot_provenance=CadSnapshotProvenance(
            schema_="green-atlas.autocad-snapshot/1",
            source_sha256="c" * 64,
            payload_sha256="d" * 64,
            autocad_version="2027",
            plugin_version="0.1.48",
            target="macos-arm64",
            source_instances=count,
            native_geometry=count,
            unresolved_instances=0,
        ),
    )
    before = active.model_dump_json()
    repository = Mock()
    repository.get.return_value = project_projection(active)

    def load(project_id):
        assert project_id == active.id
        gc.collect()
        assert (previous_index() is None) == (scenario != "small_source")
        return ProjectGeometryView(
            id=active.id,
            geometry_version=active.geometry_version,
            import_status=active.import_status,
            geometry=active.geometry,
            source_geometry=None,
            site_area_m2=None,
            planning_area_m2=None,
            allowed_area_m2=None,
        )

    repository.get_geometry_view.side_effect = load
    expected = IndexedGeometryQuery().query(active, (-1, -1, 5, 5), 0.1)
    actual = SpatialQueries(repository, query, Mock()).query_geometry(
        active.id, (-1, -1, 5, 5), 0.1
    )
    assert actual == expected
    assert len(actual.feature_collection["features"]) == count
    assert not actual.feature_collection["metadata"]["truncated"]
    assert active.model_dump_json() == before
    repository.get_geometry_view.assert_called_once_with(active.id)


def test_navigation_bounds_simplified_bytes_without_changing_returned_features():
    project = drawing("navigation", 3, vertices=30)
    exact = IndexedGeometryQuery().query(project, (-1, -1, 40, 5), 1)
    size = feature_size(exact.feature_collection["features"][0])[1]
    bounded = IndexedGeometryQuery(max_cached_simplified_bytes=size)
    for resolution in (1.0, 1.1, 2.0, 3.0, 1.0):
        response = bounded.query(project, (-1, -1, 40, 5), resolution)
        expected = IndexedGeometryQuery().query(project, (-1, -1, 40, 5), resolution)
        assert response == expected
        index = bounded._indexes[project.id]
        assert index.simplified_bytes <= size
        assert index.simplified_bytes == sum(index.simplified_sizes.values())
    assert len(index.simplified_geometries) < 3

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(
                lambda _: bounded.query(project, (-1, -1, 40, 5), 1), range(16)
            )
        )
    assert all(response == exact for response in results)
    assert index.simplified_bytes <= size
    assert index.simplified_bytes == sum(index.simplified_sizes.values())


@pytest.mark.parametrize("budget", ["bytes", "coordinates"])
def test_repeated_budgeted_viewport_reuses_sizes_of_the_entire_candidate_window(
    monkeypatch, budget
):
    from app.geometry.viewport import query_adapters
    from app.geometry.viewport.viewport_selection import (
        VIEWPORT_CANDIDATE_WINDOW_FACTOR,
    )

    project = drawing("dense", 12, vertices=6)
    limits = {"max_feature_bytes": 400} if budget == "bytes" else {"max_coordinates": 8}
    query = IndexedGeometryQuery(max_features=4, **limits)
    measure = Mock(wraps=feature_size)
    monkeypatch.setattr(query_adapters, "feature_size", measure)
    extent = (-1, -1, 10, 20)
    first = query.query(project, extent, 0.1)
    window = query.max_features * VIEWPORT_CANDIDATE_WINDOW_FACTOR
    assert measure.call_count == window
    assert first.feature_collection["features"]
    assert budget in first.feature_collection["metadata"]["truncation_reasons"]
    measure.reset_mock()
    assert query.query(project, extent, 0.1) == first
    measure.assert_not_called()

    # Новые масштабы и окна имеют собственные размеры и ограниченный кеш.
    for resolution, bounds in [(1.0, extent), (3.0, extent), (0.1, (-1, 8, 10, 20))]:
        actual = query.query(project, bounds, resolution)
        expected = IndexedGeometryQuery(max_features=4, **limits).query(
            project, bounds, resolution
        )
        assert actual == expected
        assert len(query._indexes[project.id].display_sizes) <= window


@pytest.mark.parametrize(
    "field",
    ["max_cached_features", "max_cached_coordinates", "max_cached_simplified_bytes"],
)
def test_invalid_cache_budget_is_rejected(field):
    with pytest.raises(ValueError):
        IndexedGeometryQuery(**{field: 0})
