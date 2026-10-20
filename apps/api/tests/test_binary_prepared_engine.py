"""WKB-проверка сохраняет решения, читая только кандидатов одного окна."""

import sqlite3
from dataclasses import replace
from unittest.mock import Mock

import pytest
from shapely.geometry import shape
from test_hybrid_search import feature, populate
from test_native_live_provider import rectangle
from test_native_live_provider import setup as setup
from test_preparation_identity import network

from app.dxf_import.layers.contracts import LayerKind
from app.dxf_import.review.contracts import SourceObjectDecision
from app.geometry.networks.utility_contracts import UtilityType
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.face_contracts import NativeFace
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.preparation.prepared_storage import SqlitePreparedResource
from app.native_query.project_runtime import ProjectLiveGeometry


def test_entire_mask_portion_uses_one_checked_connection(setup, tmp_path, monkeypatch):
    project, snapshot, store, _ = saved(setup, tmp_path)
    work = rectangle(-50, -50, 50, 50)
    expected = PreparedGeometryEngine(snapshot, project).automatic_safe_geometry(
        project, work, 0.5, "shrub"
    )
    engine = ProjectLiveGeometry(Mock(), prepared_store=store).load_local(project)
    connect = sqlite3.connect
    opened = []

    def tracked(*args, **kwargs):
        opened.append(args[0])
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    actual = engine.automatic_safe_geometry(project, work, 0.5, "shrub")
    expected["ga_search_domain"].pop("elapsed_s")
    actual["ga_search_domain"].pop("elapsed_s")
    assert actual == expected
    assert len(opened) == 1
    assert getattr(engine.snapshot._sessions, "active", None) is None


def test_batch_of_points_uses_one_connection_and_preserves_measurements(
    setup, tmp_path, monkeypatch
):
    project, snapshot, store, _ = saved(setup, tmp_path)
    points = [(5, 0), (23, 0), (50, 0), (100, 0), (-100, -100)]
    expected = PreparedGeometryEngine(snapshot, project)._point_measurements(
        project, points
    )
    engine = ProjectLiveGeometry(Mock(), prepared_store=store).load_local(project)
    connect = sqlite3.connect
    opened = []

    def tracked(*args, **kwargs):
        opened.append(args[0])
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    assert engine._point_measurements(project, points) == expected
    assert len(opened) == 1


def test_empty_calculation_scope_still_refuses_changed_snapshot(setup, tmp_path):
    project, _, store, _ = saved(setup, tmp_path)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    path = engine.snapshot.path
    data = path.read_bytes()
    path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(PreparedGeometryUnavailable), router.calculation_scope(project):
        pytest.fail(
            "Повреждённое основание не допускает даже предварительное отсечение"
        )


def saved(setup, tmp_path):
    live, client, project = setup
    populate(project)
    project.source_file.native_session = live.session
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path / "prepared")
    store.save(snapshot)
    return project, snapshot, store, client


@pytest.mark.parametrize("mode", ["build", "ready"])
def test_point_read_never_walks_records_faces_or_builds_full_index(
    setup, tmp_path, monkeypatch, mode
):
    project, snapshot, store, client = saved(setup, tmp_path)
    baseline = PreparedGeometryEngine(snapshot, project)
    points = [(5, 0), (23, 0), (50, 0), (100, 0), (120, 0), (-100, -100)]
    expected = [
        baseline.explain_position(project, x, y, 0.5, "shrub") for x, y in points
    ]
    if mode == "ready":
        warm = ProjectLiveGeometry(Mock(), prepared_store=store)
        warm.load_local(project)
    router = ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(store.directory))
    forbidden = Mock(side_effect=AssertionError("Full snapshot read is forbidden"))
    monkeypatch.setattr(router.prepared_store, "load_project", forbidden)
    monkeypatch.setattr(SqlitePreparedResource, "iter_records", forbidden)
    monkeypatch.setattr(
        "app.native_query.measurement.prepared_provider.NativeBoundsIndex", forbidden
    )
    monkeypatch.setattr(
        "app.native_query.measurement.prepared_provider.PreparedProjection", forbidden
    )
    actual = router.load_local(project)
    assert isinstance(actual.snapshot, SqlitePreparedResource)
    monkeypatch.setattr(type(actual.snapshot.faces), "__iter__", forbidden)
    for _ in range(2):
        assert [
            actual.explain_position(project, x, y, 0.5, "shrub") for x, y in points
        ] == expected
        assert router.load_local(project) is actual
    assert actual._source_participations == {}
    forbidden.assert_not_called()
    assert not client.calls


def test_rtree_reads_only_nearby_obstacles_and_all_unlocated_and_site_records(
    setup, tmp_path, monkeypatch
):
    project, snapshot, store, _ = saved(setup, tmp_path)
    prototype = snapshot.records[1]
    distant = tuple(
        replace(
            prototype,
            item=prototype.item.model_copy(
                update={
                    "routes": (f"D{index:03X}",),
                    "bounds": (1000 + index, 0, 1001 + index, 1),
                }
            ),
            geometries=(rectangle(1000 + index, 0, 1001 + index, 1),),
        )
        for index in range(256)
    )
    unknown = replace(
        prototype,
        item=prototype.item.model_copy(update={"routes": ("EE",), "bounds": None}),
        geometries=(),
        reason="projection_missing",
        detail="Неизвестное положение объекта",
    )
    # The immutable key captures geometry inputs; this synthetic fixture extends
    # their verified record set before the first publication.
    store = PreparedStore(tmp_path / "extended")
    snapshot = snapshot.model_copy(
        update={"records": (*snapshot.records, *distant, unknown)}
    )
    store.save(snapshot)
    expected = PreparedGeometryEngine(snapshot, project).explain_position(
        project, 50, 0, 0.5, "shrub"
    )
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    decoded = []
    original = engine.projection.get

    def read(item, **kwargs):
        decoded.append(item.routes)
        return original(item, **kwargs)

    monkeypatch.setattr(engine.projection, "get", read)
    assert engine.explain_position(project, 50, 0, 0.5, "shrub") == expected
    assert decoded == [("AA",), ("EE",)]
    assert expected.unlocated_objects == 1
    # The site record is still selected outside the RTree query window.
    decoded.clear()
    engine.explain_position(project, 500, 500, 0.5, "shrub")
    assert decoded == [("AA",), ("EE",)]
    assert len(engine.snapshot.records) == 260


@pytest.mark.parametrize("role", ["normal", "unconfirmed", "ignore", "decision"])
def test_binary_domain_coverage_and_addressed_roles_match_full_snapshot(
    setup, tmp_path, role
):
    live, client, project = setup
    populate(project)
    project.source_file.native_session = live.session
    if role == "unconfirmed":
        project.layers[1].mapping_confirmed = False
    elif role == "ignore":
        project.layers[2].mapped_kind = LayerKind.IGNORE
    elif role == "decision":
        project.layers[1].mapping_confirmed = False
        project.source_file.object_decisions = [
            SourceObjectDecision(
                source={"handle": "BB"},
                geometry_sha256="a" * 64,
                interpretation="area",
                kind="building",
            )
        ]
    snapshot = prepare_snapshot(live, project)
    baseline = PreparedGeometryEngine(snapshot, project)
    store = PreparedStore(tmp_path / "prepared")
    store.save(snapshot)
    engine = ProjectLiveGeometry(Mock(), prepared_store=store).load_local(project)
    for x in (5, 23, 35, 120):
        assert engine.explain_position(
            project, x, 0, 0.5, "shrub"
        ) == baseline.explain_position(project, x, 0, 0.5, "shrub")
    actual = engine.automatic_safe_geometry(
        project, rectangle(-12, -4, 40, 4), 0.5, "shrub"
    )
    expected = baseline.automatic_safe_geometry(
        project, rectangle(-12, -4, 40, 4), 0.5, "shrub"
    )
    for field in ("geometry", "unresolved_geometry", "pending_geometry"):
        assert shape(actual["ga_search_domain"][field]).equals_exact(
            shape(expected["ga_search_domain"][field]), 0
        )
    assert engine.source_coverage(project) == baseline.source_coverage(project)
    assert [item.routes for item in engine._source_objects] == [
        item.routes for item in baseline._source_objects
    ]
    assert not client.calls


def test_binary_role_rebinding_drops_measurements_without_reloading_all_records(
    setup, tmp_path, monkeypatch
):
    live, _, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path / "prepared")
    store.save(snapshot)
    baseline = PreparedGeometryEngine(snapshot, project)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    before = engine.explain_position(project, 20.85, 0, 0.5, "shrub")
    assert before == baseline.explain_position(project, 20.85, 0, 0.5, "shrub")
    forbidden = Mock(side_effect=AssertionError("Rebinding cannot scan all objects"))
    monkeypatch.setattr(SqlitePreparedResource, "iter_records", forbidden)
    project.layers[2].utility_context.network_type = UtilityType.GAS
    actual = engine.explain_position(project, 20.85, 0, 0.5, "shrub")
    assert actual == baseline.explain_position(project, 20.85, 0, 0.5, "shrub")
    assert actual != before
    assert router.load_local(project) is engine
    forbidden.assert_not_called()


@pytest.mark.parametrize("file", ["source", "binary"])
def test_cached_point_measurement_cannot_mask_source_or_binary_tampering(
    setup, tmp_path, file
):
    project, _, store, _ = saved(setup, tmp_path)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    engine = router.load_local(project)
    engine.explain_position(project, 50, 0, 0.5, "shrub")
    path = engine.snapshot.source if file == "source" else engine.snapshot.path
    payload = path.read_bytes()
    path.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    with pytest.raises(PreparedGeometryUnavailable):
        engine.explain_position(project, 50, 0, 0.5, "shrub")


def test_unconfirmed_remote_site_remains_an_always_candidate(setup, tmp_path):
    live, _, project = setup
    populate(project)
    project.source_file.native_session = live.session
    site = project.layers[0]
    site.mapping_confirmed = False
    project.layers.append(
        site.model_copy(
            update={
                "id": "confirmed-site",
                "source_name": "Территория",
                "mapping_confirmed": True,
            }
        )
    )
    original = live.inventory.objects[0]
    live.inventory = live.inventory.model_copy(
        update={
            "objects": (
                original.model_copy(update={"bounds": (1000, 1000, 1010, 1010)}),
                *live.inventory.objects[1:],
                original.model_copy(update={"route": "DD", "layer": "Территория"}),
            ),
        }
    )
    project.geometry.feature_collection["features"][0]["geometry"] = rectangle(
        1000, 1000, 1010, 1010
    )
    project.geometry.feature_collection["features"].append(
        feature("DD", "Территория", rectangle(-100, -100, 100, 100))
    )
    snapshot = prepare_snapshot(live, project)
    baseline = PreparedGeometryEngine(snapshot, project)
    store = PreparedStore(tmp_path / "prepared")
    store.save(snapshot)
    engine = ProjectLiveGeometry(Mock(), prepared_store=store).load_local(project)
    expected = baseline.explain_position(project, 50, 0, 0.5, "shrub")
    assert engine.explain_position(project, 50, 0, 0.5, "shrub") == expected
    assert {
        item.routes for item in engine._bounds_index.candidates([(50, 0, 0)], 10)
    } == {
        ("AA",),
        ("DD",),
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_rtree_rounding_does_not_add_outside_candidates(setup, tmp_path, reverse):
    project, snapshot, _, _ = saved(setup, tmp_path)
    prototype = snapshot.records[1]
    low = 2.000000000001
    extra = replace(
        prototype,
        item=prototype.item.model_copy(
            update={
                "routes": ("EE",),
                "bounds": (3, 1, low, 0) if reverse else (low, 0, 3, 1),
            }
        ),
        geometries=(rectangle(low, 0, 3, 1),),
    )
    snapshot = snapshot.model_copy(update={"records": (*snapshot.records, extra)})
    baseline = PreparedGeometryEngine(snapshot, project)
    store = PreparedStore(tmp_path / "rounding")
    store.save(snapshot)
    engine = ProjectLiveGeometry(Mock(), prepared_store=store).load_local(project)
    expected = [item.routes for item in baseline._bounds_index.window((0, 0, 0, 0), 2)]
    assert expected == [("AA",), ("BB",)]
    assert [
        item.routes for item in engine._bounds_index.window((0, 0, 0, 0), 2)
    ] == expected
    assert [
        item.routes for item in engine._bounds_index.candidates([(0, 0, 0)], 2)
    ] == expected

    points = [(0, 0, 0), (10, 0, 0)]
    assert [item.routes for item in engine._bounds_index.candidates(points, 2)] == [
        item.routes for item in baseline._bounds_index.candidates(points, 2)
    ]


def test_streaming_coverage_does_not_select_the_same_metadata_twice(
    setup, tmp_path, monkeypatch
):
    project, snapshot, store, _ = saved(setup, tmp_path)
    expected = PreparedGeometryEngine(snapshot, project).source_coverage(project)
    engine = ProjectLiveGeometry(Mock(), prepared_store=store).load_local(project)
    record = Mock(side_effect=AssertionError("Поток уже содержит эту запись"))
    monkeypatch.setattr(engine.snapshot, "record", record)
    assert engine.source_coverage(project) == expected
    record.assert_not_called()
    assert engine._source_participations == {}


def test_coverage_reads_face_routes_without_decoding_face_display_coordinates(
    setup, tmp_path, monkeypatch
):
    project, snapshot, _, _ = saved(setup, tmp_path)
    face = NativeFace(
        id=1,
        anchor="BB",
        layer="Здания",
        routes=("BB",),
        physical_routes=("BB",),
        area=200,
        bounds=(0, -10, 10, 10),
        display=((0, -10, 0), (10, -10, 0), (10, 10, 0), (0, 10, 0), (0, -10, 0)),
    )
    records = tuple(
        replace(record, item=record.item.model_copy(update={"face_id": 1}))
        if record.item.routes == ("BB",)
        else record
        for record in snapshot.records
    )
    snapshot = snapshot.model_copy(update={"records": records, "faces": (face,)})
    expected = PreparedGeometryEngine(snapshot, project).source_coverage(project)
    store = PreparedStore(tmp_path / "faces")
    store.save(snapshot)
    engine = ProjectLiveGeometry(Mock(), prepared_store=store).load_local(project)
    forbidden = Mock(
        side_effect=AssertionError("Координаты отображения не нужны паспорту")
    )
    monkeypatch.setattr(type(engine.snapshot.faces), "__iter__", forbidden)
    assert engine.source_coverage(project) == expected
    assert expected.layers["Здания"].physical_count == 1
    forbidden.assert_not_called()
