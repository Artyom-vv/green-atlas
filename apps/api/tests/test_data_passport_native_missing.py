"""The source passport is readable before explicit native CAD preparation."""

from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from unittest.mock import Mock

import ijson
import pytest
from fastapi.testclient import TestClient
from test_hybrid_search import populate
from test_import_mapping_storage import imported as imported
from test_native_live_provider import setup as setup

from app.composition import create_runtime, get_application
from app.data_passport import build_data_passport
from app.dxf_import.contracts import ImportMode
from app.dxf_import.layers.contracts import LayerKind
from app.dxf_import.source.editor_source import open_source_editor
from app.dxf_import.source.review_contracts import SourceReview
from app.geometry.contracts import GeometrySnapshot
from app.geometry.coverage_contracts import (
    GeometryCoverage,
    GeometryCoverageIssue,
    LayerGeometryCoverage,
)
from app.main import app
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.projects.application import ProjectCatalogApplication
from app.projects.concurrency import ProjectVersionConflict
from app.projects.geometry_storage import geometry_references


def catalog(repository, geometry):
    return ProjectCatalogApplication(
        repository=repository,
        geometry=geometry,
        history=Mock(),
        commit_lock=RLock(),
        cancel_active=Mock(),
        invalidate_spatial=Mock(),
        now=lambda: datetime.now(UTC),
        new_id=lambda: "unused",
    )


def test_native_editor_passport_reads_metadata_and_keeps_addressed_gaps(
    imported, monkeypatch
):
    repository, project = imported
    project.layers[1].mapped_kind = LayerKind.ROAD
    project.layers[1].projected_geometry_types = {"LINE": 1}
    project.layers[1].geometry_complete = False
    project = repository.save(open_source_editor(project))
    original_get = repository.get
    reads = []

    def compact_only(identifier, *, lightweight=False):
        reads.append(lightweight)
        assert lightweight, "Паспорт редактора не загружает миллион объектов"
        return original_get(identifier, lightweight=True)

    monkeypatch.setattr(repository, "get", compact_only)
    monkeypatch.setattr(
        repository._geometry_store,
        "open",
        Mock(side_effect=AssertionError("Нет чтения gzip")),
    )
    provider = Mock(
        metadata_source_coverage=Mock(side_effect=PreparedGeometryUnavailable("missing")),
        source_coverage=Mock(side_effect=AssertionError("Нет полного покрытия")),
    )
    passport = catalog(repository, provider).get_data_passport(project.id)
    assert reads == [True]
    assert passport.source_file_name == project.source_file.name
    assert "ROAD" in passport.incomplete_layers
    assert str(PreparedGeometryUnavailable("missing")) in passport.gaps
    assert all(entry.display_feature_count is None for entry in passport.entries)
    assert passport.calculation_status == "not_ready"
    assert passport.mass_placement_status == "blocked"
    provider.source_coverage.assert_not_called()


@pytest.mark.parametrize("state", ["stale", "corrupt", "unsupported"])
def test_metadata_passport_preserves_prepared_integrity_refusals(imported, state):
    repository, project = imported
    project = repository.save(open_source_editor(project))
    failure = PreparedGeometryUnavailable(state)
    provider = Mock(metadata_source_coverage=Mock(side_effect=failure))
    with pytest.raises(PreparedGeometryUnavailable) as error:
        catalog(repository, provider).get_data_passport(project.id)
    assert error.value is failure


def test_metadata_passport_keeps_native_coverage_counts_and_source_hash_guard(imported):
    repository, project = imported
    project.layers[1].mapped_kind = LayerKind.ROAD
    project = repository.save(open_source_editor(project))
    record = LayerGeometryCoverage(
        linear_count=5,
        context_count=2,
        unresolved=[
            GeometryCoverageIssue(
                routes=["route-7"],
                reason="unavailable",
                detail="Не подготовлен один объект",
            )
        ],
    )
    coverage = GeometryCoverage(
        source_sha256=project.source_file.content_sha256, layers={"ROAD": record}
    )
    provider = Mock(metadata_source_coverage=Mock(return_value=coverage))
    passport = catalog(repository, provider).get_data_passport(project.id)
    road = next(entry for entry in passport.entries if entry.kind == "road")
    assert road.geometry_coverage == record
    assert road.display_feature_count is None
    assert not road.used_in_calculation
    provider.metadata_source_coverage.return_value = coverage.model_copy(
        update={"source_sha256": "f" * 64}
    )
    with pytest.raises(ValueError):
        catalog(repository, provider).get_data_passport(project.id)


def source_editor(project, session):
    project.source_file.native_session = session
    project.import_status.mode = ImportMode.AUTOCAD_LIVE
    project.source_review = SourceReview(issues=[])
    project.map_ready = True
    for layer in project.layers:
        layer.geometry_complete = True
        layer.projected_geometry_types = {"Polygon": 1}
    return project


def test_metadata_passport_does_not_load_an_unknown_cold_snapshot(
    setup, tmp_path, monkeypatch
):
    engine, native, project = setup
    source_editor(project, engine.session)
    populate(project)
    store = PreparedStore(tmp_path / "prepared")
    store.save(prepare_snapshot(engine, project))
    assert store.state(project, engine.session) == "available"
    load = Mock(side_effect=AssertionError("Нет холодного чтения полного снимка"))
    monkeypatch.setattr(store, "load_project", load)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    passport = build_data_passport(project, geometry=router, metadata_only=True)
    assert all(entry.status != "verified" for entry in passport.entries)
    assert all(entry.geometry_coverage is None for entry in passport.entries)
    assert not passport.used_in_calculation
    assert passport.mass_placement_status == "blocked"
    load.assert_not_called()
    assert not native.calls


def test_metadata_passport_does_not_claim_unknown_corruption_was_validated(
    setup, tmp_path
):
    engine, _, project = setup
    source_editor(project, engine.session)
    populate(project)
    store = PreparedStore(tmp_path / "prepared")
    snapshot = prepare_snapshot(engine, project)
    store.save(snapshot)
    (store.directory / f"{snapshot.key}.json").write_bytes(b'{"broken":')
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    passport = build_data_passport(project, geometry=router, metadata_only=True)
    assert all(entry.status != "verified" for entry in passport.entries)
    assert all(entry.geometry_coverage is None for entry in passport.entries)
    with pytest.raises(PreparedGeometryUnavailable) as full:
        router.source_coverage(project)
    assert full.value.state == "corrupt"
    with pytest.raises(PreparedGeometryUnavailable) as known:
        router.metadata_source_coverage(project)
    assert known.value.state == "corrupt"


@pytest.mark.parametrize("cached_counts", [False, True])
def test_metadata_passport_reuses_only_ready_current_coverage(
    setup, tmp_path, monkeypatch, cached_counts
):
    engine, _, project = setup
    source_editor(project, engine.session)
    populate(project)
    store = PreparedStore(tmp_path / "prepared")
    snapshot = prepare_snapshot(engine, project)
    store.save(snapshot)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    local = router._cache_snapshot(project, snapshot, None)
    provider = Mock(side_effect=AssertionError("Нет первого обхода расчётного перечня"))
    monkeypatch.setattr(local, "source_coverage", provider)
    record = LayerGeometryCoverage(
        area_count=1,
        unresolved=[
            GeometryCoverageIssue(
                routes=["BB"], reason="unavailable", detail="Объект не подготовлен"
            )
        ],
    )
    if cached_counts:
        local._coverage = GeometryCoverage(
            source_sha256=project.source_file.content_sha256,
            prepared_key=snapshot.key,
            layers={"Здания": record},
        )
    passport = build_data_passport(project, geometry=router, metadata_only=True)
    building = next(entry for entry in passport.entries if entry.kind == "building")
    assert building.geometry_coverage == (record if cached_counts else None)
    assert building.status != "verified"
    provider.assert_not_called()
    if cached_counts:
        local._coverage.source_sha256 = "f" * 64
        with pytest.raises(ValueError):
            build_data_passport(project, geometry=router, metadata_only=True)


def test_metadata_coverage_refuses_changed_cached_bytes_without_reloading(
    setup, tmp_path, monkeypatch
):
    engine, _, project = setup
    source_editor(project, engine.session)
    populate(project)
    store = PreparedStore(tmp_path / "prepared")
    snapshot = prepare_snapshot(engine, project)
    store.save(snapshot)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    router._cache_snapshot(project, snapshot, None)
    load = Mock(side_effect=AssertionError("Нет повторной полной загрузки"))
    monkeypatch.setattr(store, "load_project", load)
    path = store.directory / f"{snapshot.key}.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.metadata_source_coverage(project)
    assert error.value.state == "stale"
    load.assert_not_called()


def test_metadata_coverage_does_not_rebind_a_changed_calculation_basis(
    setup, tmp_path, monkeypatch
):
    engine, _, project = setup
    source_editor(project, engine.session)
    populate(project)
    store = PreparedStore(tmp_path / "prepared")
    snapshot = prepare_snapshot(engine, project)
    store.save(snapshot)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    local = router._cache_snapshot(project, snapshot, None)
    rebind = Mock(side_effect=AssertionError("Нет обхода объектов при чтении метаданных"))
    monkeypatch.setattr(local, "_bind_source_roles", rebind)
    project.plan.planning_horizon_year += 1
    assert router.metadata_source_coverage(project) is None
    rebind.assert_not_called()


@pytest.mark.parametrize("state", ["stale", "corrupt", "unsupported"])
def test_metadata_coverage_preserves_known_snapshot_refusals(
    setup, tmp_path, monkeypatch, state
):
    engine, _, project = setup
    source_editor(project, engine.session)
    populate(project)
    store = PreparedStore(tmp_path / "prepared")
    snapshot = prepare_snapshot(engine, project)
    store.save(snapshot)
    store.record_failure(snapshot.key, state)
    load = Mock(side_effect=AssertionError("Нет обхода известного отказа"))
    monkeypatch.setattr(store, "load_project", load)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.metadata_source_coverage(project)
    assert error.value.state == state
    load.assert_not_called()


def test_metadata_passport_does_not_call_a_legacy_full_coverage_provider(imported):
    repository, project = imported
    project = repository.save(open_source_editor(project))
    provider = Mock(spec=["source_coverage"])
    provider.source_coverage.side_effect = AssertionError("Нет неявной полной проверки")
    passport = catalog(repository, provider).get_data_passport(project.id)
    assert all(entry.status != "verified" for entry in passport.entries)
    provider.source_coverage.assert_not_called()


def test_native_passport_before_preparation_is_readable_without_calculated_fallback(
    setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, native, project = setup
    project.source_file.native_session = engine.session
    # Even previously displayed geometry and optimistic map_ready cannot prove
    # a current calculation snapshot when the native prepared store is empty.
    project.map_ready = True
    project.geometry = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 0]]],
                    },
                    "properties": {"source_layer": "Здания", "kind": "building"},
                }
            ],
        }
    )
    compatibility = Mock()
    router = ProjectLiveGeometry(
        compatibility, prepared_store=PreparedStore(tmp_path / "prepared")
    )
    live = Mock(side_effect=AssertionError("Passport does not prepare CAD"))
    monkeypatch.setattr(router, "live_engine", live)
    runtime = create_runtime(tmp_path / "passport.sqlite3", geometry_engine=router)
    runtime.project_repository.create(project)
    before = runtime.application.get(project.id).model_dump()
    app.dependency_overrides[get_application] = lambda: runtime.application
    try:
        response = TestClient(app).get(f"/api/projects/{project.id}/data-passport")
        assert response.status_code == 200, response.json()
        passport = response.json()
        assert passport["calculation_status"] == "not_ready"
        assert passport["mass_placement_status"] == "blocked"
        assert passport["used_in_calculation"] == []
        assert all(not entry["used_in_calculation"] for entry in passport["entries"])
        assert all(entry["status"] != "verified" for entry in passport["entries"])
        assert all(entry["geometry_coverage"] is None for entry in passport["entries"])
        assert str(PreparedGeometryUnavailable("missing")) in passport["gaps"]
        building = next(e for e in passport["entries"] if e["kind"] == "building")
        assert building["object_count"] == 1
        assert building["display_feature_count"] == 1
        assert runtime.application.get(project.id).model_dump() == before
        assert not compatibility.mock_calls
        assert not native.calls
        live.assert_not_called()
    finally:
        app.dependency_overrides.pop(get_application, None)
        runtime.close()


@pytest.mark.parametrize("state", ["stale", "corrupt", "unsupported"])
def test_native_passport_keeps_prepared_integrity_refusals_visible(
    setup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state
) -> None:
    engine, _, project = setup
    project.source_file.native_session = engine.session
    store = PreparedStore(tmp_path / "prepared")
    failure = PreparedGeometryUnavailable(state)
    monkeypatch.setattr(store, "load_project", Mock(side_effect=failure))
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    runtime = create_runtime(tmp_path / "passport.sqlite3", geometry_engine=router)
    runtime.project_repository.create(project)
    app.dependency_overrides[get_application] = lambda: runtime.application
    try:
        response = TestClient(app).get(f"/api/projects/{project.id}/data-passport")
        assert response.status_code == 400
        assert response.json()["details"]["reason"] == failure.reason
    finally:
        app.dependency_overrides.pop(get_application, None)
        runtime.close()


@pytest.mark.parametrize("with_source", [False, True])
@pytest.mark.parametrize("scope", ["complete_source", "available_data"])
def test_native_passport_streams_display_counts_with_identical_complete_json(
    imported, monkeypatch, with_source, scope
):
    repository, project = imported
    source = project.source_geometry
    assert source is not None
    for feature, layer in zip(source.feature_collection["features"], project.layers):
        feature["properties"]["kind"] = layer.suggested_kind.value
        layer.mapped_kind = layer.suggested_kind
        layer.mapping_confirmed = True
    features = source.feature_collection["features"]
    features.extend(
        [
            {**features[1], "properties": {"source_layer": " ROAD ", "kind": "road"}},
            {
                **features[1],
                "properties": {
                    "source_layer": "ROAD",
                    "kind": "road",
                    "source_context_only": True,
                },
            },
            {**features[1], "properties": {"source_layer": "ROAD", "kind": "allowed"}},
        ]
    )
    source.calculation_scope = scope
    project.geometry = source.model_copy(deep=True)
    source.feature_collection["features"] = []
    source.calculation_scope = None
    if not with_source:
        project.source_geometry = None
    project = repository.save(project)
    issue = GeometryCoverageIssue(
        routes=["unavailable-native-route"],
        reason="projection_missing",
        detail="Нет геометрии одного объекта",
    )
    coverage = GeometryCoverage(
        source_sha256=project.source_file.content_sha256,
        layers={
            "SITE": LayerGeometryCoverage(area_count=1),
            "SECOND_SITE": LayerGeometryCoverage(area_count=1),
            "ROAD": LayerGeometryCoverage(linear_count=1, unresolved=[issue]),
        },
    )
    expected = build_data_passport(
        repository.get(project.id), geometry=Mock(source_coverage=lambda _: coverage)
    ).model_dump_json()
    original_get = repository.get
    reads = []

    def compact_only(identifier, *, lightweight=False):
        reads.append(lightweight)
        assert lightweight, "Паспорт не загружает координаты обоих CAD-графов"
        return original_get(identifier, lightweight=True)

    def source_coverage(compact):
        for graph in (compact.geometry, compact.source_geometry):
            assert graph is None or graph.feature_collection["features"] == []
        return coverage

    monkeypatch.setattr(repository, "get", compact_only)
    monkeypatch.setattr(
        repository._geometry_store,
        "read",
        Mock(side_effect=AssertionError("Нет полной коллекции")),
    )
    actual = catalog(
        repository, Mock(source_coverage=source_coverage)
    ).get_data_passport(project.id)
    assert reads == [True]
    assert actual.model_dump_json() == expected
    road = next(entry for entry in actual.entries if entry.kind == "road")
    assert road.display_feature_count == 2
    assert road.geometry_coverage.unresolved == [issue]


def test_native_passport_display_summary_preserves_geometry_integrity_failure(imported):
    import json

    repository, project = imported
    project.geometry = project.source_geometry.model_copy(deep=True)
    project = repository.save(project)
    row = repository._connection.execute(
        "SELECT payload FROM projects WHERE id = ?", (project.id,)
    ).fetchone()
    reference = geometry_references(json.loads(row["payload"]))["geometry"]
    path = repository._geometry_store.path(project.id, reference)
    content = path.read_bytes()
    path.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    with pytest.raises(ValueError, match="целостность"):
        catalog(repository, Mock()).get_data_passport(project.id)


def test_native_passport_rejects_revision_changed_during_display_summary(
    imported, monkeypatch
):
    repository, project = imported
    project.geometry = project.source_geometry.model_copy(deep=True)
    project = repository.save(project)
    original_counts = repository._geometry_store.read_display_counts

    def concurrent_revision(stream, reference, kinds):
        counts = original_counts(stream, reference, kinds)
        project.name = "Конкурентное изменение"
        repository.save(project)
        return counts

    monkeypatch.setattr(
        repository._geometry_store, "read_display_counts", concurrent_revision
    )
    with pytest.raises(ProjectVersionConflict):
        catalog(repository, Mock()).get_data_passport(project.id)


@pytest.mark.parametrize("integer", [7, 2**100, -(2**100)])
def test_passport_display_summary_preserves_numeric_modes_and_complete_counts(
    tmp_path, integer
):
    import json

    from test_project_geometry_storage import collection, stored_collection

    value = collection(size=8)
    value["foreign"]["integer"] = 7
    for feature in value["features"]:
        feature["properties"]["kind"] = "road"
    value["features"][0]["properties"]["source_layer"] = 12.5
    value["features"][-1]["properties"]["large_integer"] = integer
    store, reference, path = stored_collection(tmp_path, json.dumps(value).encode())
    before = path.read_bytes(), path.stat().st_mtime_ns
    with store.open("numeric-project", reference) as stream:
        counts = store.read_display_counts(stream, reference, {"road"})
    assert counts == {"12.5": 1, "А": 5, "Б": 4}
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("fault", ["count", "raw-sha", "syntax", "trailing"])
def test_passport_display_summary_never_publishes_unverified_counts(tmp_path, fault):
    import json

    from test_project_geometry_storage import collection, stored_collection

    content = json.dumps(collection(size=8)).encode()
    if fault == "syntax":
        content = content[:-1] + b",broken}"
    elif fault == "trailing":
        content += b'\n{"features":[]}'
    store, reference, _ = stored_collection(tmp_path, content)
    if fault == "count":
        reference = reference.model_copy(update={"features": 11})
    elif fault == "raw-sha":
        reference = reference.model_copy(update={"sha256": "0" * 64})
    with store.open("numeric-project", reference) as stream:
        with pytest.raises((ValueError, ijson.JSONError)):
            store.read_display_counts(stream, reference, {"road"})


@pytest.mark.parametrize(
    "value,features",
    [
        ([], 0),
        (None, 0),
        ({"features": {}}, 0),
        ({"features": None}, 0),
        (
            {"features": {"item": {"properties": {"source_layer": "ROAD", "kind": "road"}}}},
            1,
        ),
    ],
)
def test_passport_summary_rejects_invalid_collection_shape_like_full_read(
    tmp_path, value, features
):
    import json

    from test_project_geometry_storage import stored_collection

    store, reference, _ = stored_collection(tmp_path, json.dumps(value).encode())
    reference = reference.model_copy(update={"features": features})
    for read in (store.read, lambda stream, ref: store.read_display_counts(stream, ref, {"road"})):
        with store.open("numeric-project", reference) as stream:
            with pytest.raises(ValueError):
                read(stream, reference)


@pytest.mark.parametrize("value", [{}, {"features": []}, {"type": "FeatureCollection", "features": []}])
def test_passport_summary_preserves_valid_empty_collection(tmp_path, value):
    import json

    from test_project_geometry_storage import stored_collection

    store, reference, _ = stored_collection(tmp_path, json.dumps(value).encode())
    reference = reference.model_copy(update={"features": 0})
    with store.open("numeric-project", reference) as stream:
        assert store.read(stream, reference) == value
    with store.open("numeric-project", reference) as stream:
        assert store.read_display_counts(stream, reference, {"road"}) == {}
