"""Потоковая карта сохраняет объекты, ревизию и целостность без полного графа."""

import gc
import gzip
import json
import weakref
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from hashlib import sha256
from threading import Event
from unittest.mock import Mock

import ijson
import pytest

from app.dxf_import.contracts import ImportMode, ImportStatus
from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.queries import SpatialQueries
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.operations.progress import OperationCancelled, operation_cancellation
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project
from app.projects.geometry_storage import GeometryReference, geometry_references


def feature(index):
    return {
        "type": "Feature",
        "id": str(index),
        "geometry": {"type": "Point", "coordinates": [index, -0.0, 2.5]},
        "properties": {
            "kind": "road",
            "source_layer": "Дорога",
            "unknown": [False, None],
        },
        "foreign": {"nested": {"exact": 10**40 if index == 7 else 1}},
    }


def create(repository, calculated=True, count=12):
    graph = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "extra": {"number": 10**40},
            "features": [feature(i) for i in range(count)],
        }
    )
    return repository.create(
        Project(
            name="Потоковая карта",
            import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
            geometry_version=1,
            geometry=graph if calculated else None,
            source_geometry=graph,
        )
    )


def references(repository, project):
    payload = json.loads(
        repository._connection.execute(
            "SELECT payload FROM projects WHERE id=?", (project.id,)
        ).fetchone()[0]
    )
    return payload, geometry_references(payload)


def replace_collection(repository, project, raw, count):
    """Создать проверяемый файл в отдельной тестовой БД, включая некорректный JSON."""
    payload, refs = references(repository, project)
    data = gzip.compress(raw, mtime=0)
    ref = GeometryReference(
        schema_version="green-atlas.project-geometry/1",
        bytes=len(raw),
        sha256=sha256(raw).hexdigest(),
        stored_bytes=len(data),
        stored_sha256=sha256(data).hexdigest(),
        features=count,
    )
    path = repository._geometry_store.path(project.id, ref)
    path.write_bytes(data)
    field = "geometry" if project.geometry else "source_geometry"
    payload["_geometry_storage"][field] = ref.model_dump(mode="json", by_alias=True)
    repository._connection.execute(
        "UPDATE projects SET payload=? WHERE id=?",
        (
            json.dumps(payload),
            project.id,
        ),
    )
    repository._connection.commit()
    return path, ref


@pytest.mark.parametrize("calculated", [False, True])
@pytest.mark.parametrize("inline", [False, True])
@pytest.mark.parametrize("resolution,budget", [(0.1, 30), (3.0, 3)])
def test_streamed_viewport_matches_exact_query_and_never_hydrates_full_graph(
    tmp_path, monkeypatch, calculated, inline, resolution, budget
):
    repository = SqliteProjectRepository(
        ":memory:" if inline else tmp_path / "map.sqlite3"
    )
    project = create(repository, calculated)
    expected = IndexedGeometryQuery(max_features=budget, max_cached_features=2).query(
        repository.get_geometry_view(project.id), (-1, -1, 20, 20), resolution
    )
    get = repository.get

    def lightweight_only(project_id, *, lightweight=False):
        assert lightweight
        return get(project_id, lightweight=True)

    monkeypatch.setattr(repository, "get", lightweight_only)
    monkeypatch.setattr(
        repository, "get_geometry_view", Mock(side_effect=AssertionError("full view"))
    )
    if not inline:
        monkeypatch.setattr(
            repository._geometry_store,
            "read",
            Mock(side_effect=AssertionError("full graph")),
        )
    query = IndexedGeometryQuery(max_features=budget, max_cached_features=2)
    spatial = SpatialQueries(repository, query, Mock())
    assert spatial.query_geometry(project.id, (-1, -1, 20, 20), resolution) == expected
    assert spatial.query_geometry(project.id, (-1, -1, 20, 20), resolution) == expected
    assert len(query._indexes[project.id].features) == 12
    if budget == 3:
        assert expected.feature_collection.get("source_overview") is not None
    repository.close()


@pytest.mark.parametrize(
    "failure",
    ["stored_sha", "raw_sha", "raw_size", "count", "trailing", "crc", "shape"],
)
def test_partial_stream_is_never_published(tmp_path, monkeypatch, failure):
    repository = SqliteProjectRepository(tmp_path / "failure.sqlite3")
    project = create(repository, count=1)
    raw = json.dumps({"features": [feature(i) for i in range(200)]}).encode()
    if failure == "trailing":
        raw += b" {}"
    if failure == "shape":
        raw = b'{"features":null}'
    path, ref = replace_collection(repository, project, raw, 200)
    payload, _ = references(repository, project)
    values = payload["_geometry_storage"]["geometry"]
    if failure in ("stored_sha", "crc"):
        data = bytearray(path.read_bytes())
        data[-5] ^= 1
        path.write_bytes(data)
        if failure == "crc":
            changed = sha256(data).hexdigest()
            values["stored_sha256"] = changed
            path.rename(path.with_name(changed + ".json.gz"))
    elif failure == "raw_sha":
        values["sha256"] = "0" * 64
    elif failure == "raw_size":
        values["bytes"] += 1
    elif failure == "count":
        values["features"] += 1
    repository._connection.execute(
        "UPDATE projects SET payload=? WHERE id=?", (json.dumps(payload), project.id)
    )
    repository._connection.commit()
    query = IndexedGeometryQuery(max_cached_features=2)
    with pytest.raises((ValueError, gzip.BadGzipFile, ijson.JSONError)):
        SpatialQueries(repository, query, Mock()).query_geometry(
            project.id, (-1, -1, 300, 20), 0.1
        )
    assert query.cached_project_count == 0
    repository.close()


@pytest.mark.parametrize("persistent", [False, True])
def test_overflow_discards_first_consumer_before_retry(
    tmp_path, monkeypatch, persistent
):
    from app.geometry.viewport import query_adapters
    from app.projects import display_storage

    repository = SqliteProjectRepository(tmp_path / "overflow.sqlite3")
    if not persistent:
        monkeypatch.setattr(
            repository, "open_display_geometry", lambda _, **kwargs: nullcontext(None)
        )
    project = create(repository, count=1)
    records = [feature(i) for i in range(120)]
    for item in records:
        item["foreign"] = {}
    records[-1]["foreign"] = {"large": 10**100}
    replace_collection(
        repository, project, json.dumps({"features": records}).encode(), len(records)
    )
    builder_type = (
        display_storage._DisplayBuilder if persistent else query_adapters._IndexBuilder
    )
    previous = []

    class ObservedBuilder(builder_type):
        def __init__(self, *args, **kwargs):
            gc.collect()
            assert all(ref() is None for ref in previous)
            super().__init__(*args, **kwargs)
            previous.append(weakref.ref(self))

    monkeypatch.setattr(
        display_storage if persistent else query_adapters,
        "_DisplayBuilder" if persistent else "_IndexBuilder",
        ObservedBuilder,
    )
    query = IndexedGeometryQuery(max_cached_features=2)
    response = SpatialQueries(repository, query, Mock()).query_geometry(
        project.id, (-1, -1, 300, 20), 0.1
    )
    assert len(previous) == 2
    assert response.feature_collection["features"] == records
    assert query.cached_project_count == 1
    repository.close()


def test_cancel_and_expected_revision_never_publish_index(tmp_path, monkeypatch):
    repository = SqliteProjectRepository(tmp_path / "cancel.sqlite3")
    project = create(repository)
    query = IndexedGeometryQuery(max_cached_features=2)
    finish = repository._geometry_store._consume_features
    cancelled = False

    def after_eof(*args, **kwargs):
        nonlocal cancelled
        result = finish(*args, **kwargs)
        cancelled = True
        return result

    monkeypatch.setattr(repository._geometry_store, "_consume_features", after_eof)

    def check():
        if cancelled:
            raise OperationCancelled()

    with operation_cancellation(check), pytest.raises(OperationCancelled):
        query.query_streamed(repository, project.id, (-1, -1, 20, 20), 0.1)
    assert query.cached_project_count == 0
    token = set_expected_project_version('"2"')
    try:
        with pytest.raises(ProjectVersionConflict):
            query.query_streamed(repository, project.id, (-1, -1, 20, 20), 0.1)
    finally:
        reset_expected_project_version(token)
    assert query.cached_project_count == 0
    repository.close()


def test_pinned_revision_and_single_cold_reader(tmp_path, monkeypatch):
    repository = SqliteProjectRepository(tmp_path / "pinned.sqlite3")
    # Совместимость старого потокового провайдера без производного SQLite.
    monkeypatch.setattr(
        repository, "open_display_geometry", lambda _, **kwargs: nullcontext(None)
    )
    project = create(repository)
    second_repository = SqliteProjectRepository(repository.path)
    entered, release = Event(), Event()
    consume = repository._geometry_store.consume_features
    calls = 0

    def read(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            assert release.wait(5)
        return consume(*args, **kwargs)

    monkeypatch.setattr(repository._geometry_store, "consume_features", read)
    spatial = SpatialQueries(
        repository, IndexedGeometryQuery(max_cached_features=2), Mock()
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            spatial.query_geometry, project.id, (-1, -1, 20, 20), 0.1
        )
        assert entered.wait(5)
        second = executor.submit(
            spatial.query_geometry, project.id, (-1, -1, 20, 20), 0.1
        )
        release.set()
        assert first.result(5) == second.result(5)
    assert calls == 1
    spatial.geometry_query.discard(project.id)

    def update_after_pin(*args, **kwargs):
        current = second_repository.get(project.id)
        current.geometry_version += 1
        current.geometry.feature_collection["features"][0]["id"] = "changed"
        second_repository.save(current)
        return consume(*args, **kwargs)

    monkeypatch.setattr(
        repository._geometry_store, "consume_features", update_after_pin
    )
    old = spatial.query_geometry(project.id, (-1, -1, 20, 20), 0.1)
    assert old.feature_collection["metadata"]["geometry_version"] == 1
    assert old.feature_collection["features"][0]["id"] == "0"
    assert second_repository.get(project.id).geometry_version == 2
    repository.close()
    second_repository.close()


@pytest.mark.parametrize("has_source", [False, True])
def test_roles_utilities_and_context_match_existing_view(tmp_path, has_source):
    from app.dxf_import.contracts import ImportMode, ImportStatus, SourceFile
    from app.dxf_import.layers.contracts import Layer, LayerKind
    from app.dxf_import.source.review_contracts import SourceReview
    from app.geometry.networks.utility_contracts import UtilityContext
    from app.native_query.live.live_client import LiveSession

    repository = SqliteProjectRepository(tmp_path / "roles.sqlite3")
    graph = GeometrySnapshot(
        feature_collection={"features": [feature(i) for i in range(300)]}
    )
    graph.feature_collection["features"][1]["properties"]["source_context_only"] = True
    graph.feature_collection["features"][2]["properties"]["utility_context"] = {
        "network_type": "heat"
    }
    session = LiveSession(
        session_id="a" * 32,
        pid=1,
        plugin_version="0.1.48",
        source_path="source.dwg",
        source_sha256="b" * 64,
        snapshot_path="snapshot.json",
        snapshot_sha256="c" * 64,
        units_code=6,
        watched_databases=1,
        unavailable_xrefs=0,
    )
    project = repository.create(
        Project(
            name="Текущие назначения",
            geometry=graph,
            source_geometry=graph if has_source else None,
            import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
            source_review=SourceReview(),
            source_file=SourceFile(
                name="source.dwg",
                content_sha256="c" * 64,
                size=1,
                imported_at="2026-10-12",
                dxf_version="AC1032",
                units="m",
                entity_count=300,
                native_session=session,
            ),
            layers=[
                Layer(
                    id="road",
                    source_name="Дорога",
                    object_count=300,
                    suggested_kind=LayerKind.UTILITY,
                    mapped_kind=LayerKind.UTILITY,
                    utility_context=UtilityContext(network_type="water"),
                    mapping_confirmed=True,
                    color="#111111",
                )
            ],
        )
    )
    expected = IndexedGeometryQuery(max_cached_features=2).query(
        repository.get_geometry_view(project.id), (-1, -1, 400, 20), 0.1
    )
    actual = IndexedGeometryQuery(max_cached_features=2).query_streamed(
        repository, project.id, (-1, -1, 400, 20), 0.1
    )
    assert actual == expected
    assert actual.feature_collection["features"][1]["properties"]["kind"] == "road"
    assert actual.feature_collection["features"][0]["properties"]["kind"] == (
        "road" if has_source else "utility"
    )
    if not has_source:
        assert (
            actual.feature_collection["features"][0]["properties"]["utility_context"][
                "network_type"
            ]
            == "water"
        )
        assert (
            "utility_context"
            not in actual.feature_collection["features"][1]["properties"]
        )
        assert (
            actual.feature_collection["features"][2]["properties"]["utility_context"][
                "network_type"
            ]
            == "water"
        )
    repository.close()


@pytest.mark.parametrize(
    "raw,count",
    [
        (b"{}", 0),
        (b'{"features":[null,false,4,{}]}', 4),
        (b'{"features":[],"features":[null]}', 1),
        (b'{"features":[null],"features":[]}', 0),
    ],
)
def test_collection_and_invalid_geometry_policy_matches_existing_reader(
    tmp_path, raw, count
):
    repository = SqliteProjectRepository(tmp_path / "shape.sqlite3")
    project = create(repository, count=1)
    replace_collection(repository, project, raw, count)
    expected = IndexedGeometryQuery().query(
        repository.get_geometry_view(project.id), (-1, -1, 20, 20), 0.1
    )
    assert (
        IndexedGeometryQuery().query_streamed(
            repository, project.id, (-1, -1, 20, 20), 0.1
        )
        == expected
    )
    repository.close()


def test_twenty_thousand_objects_never_retain_input_graph(tmp_path, monkeypatch):
    from app.projects import geometry_stream

    count = 20_000
    repository = SqliteProjectRepository(tmp_path / "bounded.sqlite3")
    project = create(repository, count=1)
    pending = tmp_path / "incremental.gz"
    raw_sha, raw_bytes = sha256(), 0
    with gzip.open(pending, "wb") as output:

        def write(value):
            nonlocal raw_bytes
            output.write(value)
            raw_sha.update(value)
            raw_bytes += len(value)

        write(b'{"features":[')
        for index in range(count):
            if index:
                write(b",")
            item = feature(index)
            item["foreign"] = {}
            write(json.dumps(item).encode())
        write(b"]}")
    data = pending.read_bytes()
    ref = GeometryReference(
        schema_version="green-atlas.project-geometry/1",
        bytes=raw_bytes,
        sha256=raw_sha.hexdigest(),
        stored_bytes=len(data),
        stored_sha256=sha256(data).hexdigest(),
        features=count,
    )
    pending.rename(repository._geometry_store.path(project.id, ref))
    payload, _ = references(repository, project)
    payload["_geometry_storage"]["geometry"] = ref.model_dump(
        mode="json", by_alias=True
    )
    repository._connection.execute(
        "UPDATE projects SET payload=? WHERE id=?", (json.dumps(payload), project.id)
    )
    repository._connection.commit()
    del data, item

    class Tracked(dict):
        pass

    items = geometry_stream.ijson.items
    live = weakref.WeakValueDictionary()
    peak = 0
    accepted = 0

    def tracked(*args, **kwargs):
        nonlocal peak, accepted
        for value in items(*args, **kwargs):
            current = Tracked(value)
            live[accepted] = current
            accepted += 1
            peak = max(peak, len(live))
            yield current
            del current

    monkeypatch.setattr(geometry_stream.ijson, "items", tracked)
    monkeypatch.setattr(
        repository._geometry_store,
        "read",
        Mock(side_effect=AssertionError("full graph")),
    )
    monkeypatch.setattr(
        repository, "get_geometry_view", Mock(side_effect=AssertionError("full view"))
    )
    query = IndexedGeometryQuery(max_features=2, max_cached_features=2)
    result = SpatialQueries(repository, query, Mock()).query_geometry(
        project.id, (-1, -1, count + 1, 20), 0.1
    )
    assert accepted == count and peak <= 2 and len(live) == 0
    assert len(query._indexes[project.id].features) == count
    assert result.feature_collection["metadata"]["total_matches"] == count
    repository.close()
