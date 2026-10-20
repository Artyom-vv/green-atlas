"""Начальные назначения native-импорта сохраняют исходный CAD-граф и его ревизию.

Проверки используют отдельную SQLite-базу и синтетические объекты. AutoCAD,
распознавание моделью и пользовательские профили не участвуют в этом сценарии.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from shapely.geometry import box, mapping

from app.dxf_import.application import ImportApplication
from app.dxf_import.contracts import (
    ImportEditability,
    ImportMode,
    ImportStatus,
    SourceFile,
)
from app.dxf_import.layers.contracts import (
    BoundaryCandidate,
    BoundaryCandidateStatus,
    Layer,
    LayerKind,
    LayerMapping,
)
from app.dxf_import.source.editor_source import open_source_editor
from app.dxf_import.source.review_contracts import SourceReview
from app.geometry.contracts import DxfVerticalPrimitive, GeometrySnapshot
from app.geometry.networks.utility_contracts import UtilityContext
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.native_query.live.live_client import LiveSession
from app.native_query.preparation.face_contracts import TopologyRecipe
from app.planning.contracts import Plan
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    expected_project_version,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project, ProjectStatus
from app.projects.geometry_storage import GEOMETRY_STORAGE_KEY
from app.projects.workspace_backup import backup_workspace
from app.projects.workspace_restore import restore_workspace_backup
from app.shared.failures import DomainFailure, FailureReason, ProjectNotFound

SOURCE_BYTES = b'{"synthetic":"native-mapping-storage"}'
SOURCE_HASH = sha256(SOURCE_BYTES).hexdigest()


def native_feature(layer: str, handle: str, geometry: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": handle,
        "geometry": json.loads(json.dumps(geometry)),
        "properties": {
            "source_layer": layer,
            "source_handle": handle,
            "source_native_geometry": True,
            "source_geometry_content_sha256": SOURCE_HASH,
            "metadata": {"original": [handle, 1, None]},
        },
    }


@pytest.fixture
def imported(
    tmp_path: Path,
) -> Iterator[tuple[SqliteProjectRepository, Project]]:
    repository = SqliteProjectRepository(tmp_path / "import-mappings.sqlite3")
    project = Project(
        name="Синтетический native-импорт",
        status=ProjectStatus.IMPORTED,
        import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
        source_file=SourceFile(
            name="snapshot.json",
            content_sha256=SOURCE_HASH,
            size=len(SOURCE_BYTES),
            imported_at="2026-10-09T00:00:00+00:00",
            dxf_version="live",
            units="м",
            entity_count=3,
            native_session=LiveSession(
                session_id="a" * 32,
                pid=1,
                plugin_version="0.1.48",
                source_path=str(tmp_path / "synthetic.dwg"),
                source_sha256="b" * 64,
                snapshot_path=str(tmp_path / "snapshot.json"),
                snapshot_sha256=SOURCE_HASH,
                units_code=6,
                watched_databases=1,
                unavailable_xrefs=0,
            ),
        ),
        layers=[
            Layer(
                id=name,
                source_name=name,
                suggested_kind=kind,
                object_count=1,
                color="#334455",
                mapping_confirmed=False,
            )
            for name, kind in (
                ("SITE", LayerKind.SITE_BORDER),
                ("ROAD", LayerKind.ROAD),
                ("SECOND_SITE", LayerKind.SITE_BORDER),
            )
        ],
        source_geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    native_feature("SITE", "boundary", mapping(box(0, 0, 100, 100))),
                    native_feature(
                        "ROAD",
                        "road",
                        {"type": "LineString", "coordinates": [[0, 0], [100, 0]]},
                    ),
                    native_feature(
                        "SECOND_SITE", "second-boundary", mapping(box(20, 20, 80, 80))
                    ),
                ],
            },
            vertical_primitives=[
                DxfVerticalPrimitive(
                    primitive_id="elevation",
                    primitive_type="point",
                    vertices_m=[[5, 5, 12]],
                    source_layer="SITE",
                    source_entity_type="AcDbPoint",
                    source_file_units="м",
                    unit_scale_to_m=1,
                    vertical_evidence="explicit_xyz",
                )
            ],
        ),
        geometry_version=4,
    )
    with closing(repository):
        project = repository.create(project)
        repository.save_source(project.id, SOURCE_BYTES)
        yield repository, project


def application(repository: SqliteProjectRepository) -> ImportApplication:
    return ImportApplication(
        repository=repository,
        dxf_reader=Mock(),
        history=Mock(),
        invalidate_spatial=Mock(),
        now=lambda: datetime.now(UTC),
        recheck_plan=Mock(return_value=True),
        preserve_local_geometry=Mock(),
    )


def stored_row(repository: SqliteProjectRepository, project_id: str) -> sqlite3.Row:
    row = repository._connection.execute(
        "SELECT payload, projection, state_version, "
        "json_extract(payload, '$.source_geometry') AS source_json "
        "FROM projects WHERE id = ?",
        (project_id,),
    ).fetchone()
    assert row is not None
    return row


def road_mapping(*, visible: bool = True) -> LayerMapping:
    return LayerMapping(
        layer_id="ROAD", kind=LayerKind.ROAD, confirmed=True, visible=visible
    )


def test_editor_mapping_preserves_immutable_graph_and_updates_full_and_map_reads(
    imported: tuple[SqliteProjectRepository, Project], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = imported
    project.layers[1].mapped_kind = LayerKind.ROAD
    project.layers[1].geometry_complete = False
    context_feature = native_feature(
        "ROAD", "context", {"type": "Point", "coordinates": [5, 5]}
    )
    context_feature["properties"].update(
        {"source_context_only": True, "kind": "annotation"}
    )
    project.source_geometry.feature_collection["features"].append(context_feature)
    project = repository.save(open_source_editor(project))
    before = json.loads(stored_row(repository, project.id)["payload"])
    directory = repository._geometry_store.directory(project.id)
    files = {path.name: path.read_bytes() for path in directory.glob("*.json.gz")}
    original_get = repository.get

    def compact_only(project_id: str, *, lightweight: bool = False) -> Project:
        assert lightweight, "Сохранение назначений не читает полный CAD-граф"
        return original_get(project_id, lightweight=lightweight)

    with monkeypatch.context() as patch:
        patch.setattr(repository, "get", compact_only)
        patch.setattr(
            repository._geometry_store,
            "stage",
            Mock(side_effect=AssertionError("Нет записи gzip")),
        )
        service = application(repository)
        context = UtilityContext(network_type="water", installation="underground")
        saved = service.save_mappings(
            project.id,
            [
                LayerMapping(
                    layer_id="ROAD",
                    kind=LayerKind.UTILITY,
                    confirmed=True,
                    utility_context=context,
                )
            ],
        )
    after = json.loads(stored_row(repository, project.id)["payload"])
    assert after[GEOMETRY_STORAGE_KEY] == before[GEOMETRY_STORAGE_KEY]
    assert {
        path.name: path.read_bytes() for path in directory.glob("*.json.gz")
    } == files
    assert saved.geometry_version == project.geometry_version + 2
    assert saved.state_version == project.state_version + 1
    assert saved.map_ready and saved.source_geometry is None
    assert any(issue.source_layer == "ROAD" for issue in saved.source_review.issues)
    cast(Mock, service.invalidate_spatial).assert_called_once_with(project.id)
    hydrated = original_get(project.id)
    road = next(
        feature
        for feature in hydrated.geometry.feature_collection["features"]
        if feature["id"] == "road"
    )
    assert road["properties"]["kind"] == "utility"
    assert road["properties"]["utility_context"] == context.model_dump(mode="json")
    assert road["properties"]["metadata"] == {"original": ["road", 1, None]}
    annotation = next(
        feature
        for feature in hydrated.geometry.feature_collection["features"]
        if feature["id"] == "context"
    )
    assert annotation["properties"]["kind"] == "annotation"
    assert annotation["geometry"] == context_feature["geometry"]
    selected = repository.read_source_features(
        project.id,
        {"ROAD"},
        expected_version=saved.state_version,
        geometry_field="geometry",
    )
    assert selected == [road, annotation]
    viewport = IndexedGeometryQuery().query(hydrated, (-1, -1, 101, 101), 0.1)
    displayed = next(
        feature
        for feature in viewport.feature_collection["features"]
        if feature.get("id") == "road"
    )
    assert displayed["properties"]["kind"] == "utility"
    backup = backup_workspace(repository.path, reason="editor-role-overlay")
    restored = restore_workspace_backup(
        backup, Path(repository.path).parent / "restored-editor"
    )
    with closing(SqliteProjectRepository(restored)) as reopened:
        assert (
            reopened.get(project.id).geometry.feature_collection
            == hydrated.geometry.feature_collection
        )
    changed = service.save_mappings(project.id, [road_mapping()])
    fresh = original_get(project.id)
    updated_road = next(
        feature
        for feature in fresh.geometry.feature_collection["features"]
        if feature["id"] == "road"
    )
    assert updated_road["properties"]["kind"] == "road"
    assert "utility_context" not in updated_road["properties"]
    assert changed.geometry_version == saved.geometry_version + 2
    assert (
        json.loads(stored_row(repository, project.id)["payload"])[GEOMETRY_STORAGE_KEY]
        == before[GEOMETRY_STORAGE_KEY]
    )


def test_editor_visibility_only_does_not_invalidate_or_rewrite_graph(
    imported: tuple[SqliteProjectRepository, Project], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = imported
    project.layers[1].mapped_kind = LayerKind.ROAD
    project.layers[1].mapping_confirmed = True
    project = repository.save(open_source_editor(project))
    before = json.loads(stored_row(repository, project.id)["payload"])
    monkeypatch.setattr(
        repository._geometry_store,
        "stage",
        Mock(side_effect=AssertionError("Нет записи gzip")),
    )
    service = application(repository)
    saved = service.save_mappings(project.id, [road_mapping(visible=False)])
    after = json.loads(stored_row(repository, project.id)["payload"])
    assert saved.geometry_version == project.geometry_version
    assert after[GEOMETRY_STORAGE_KEY] == before[GEOMETRY_STORAGE_KEY]
    cast(Mock, service.invalidate_spatial).assert_not_called()


def test_editor_mapping_checks_selected_boundary_without_full_hydration(
    imported: tuple[SqliteProjectRepository, Project], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = imported
    project = repository.save(open_source_editor(project))
    original_get = repository.get
    monkeypatch.setattr(
        repository,
        "get",
        lambda identifier, *, lightweight=False: original_get(
            identifier, lightweight=True
        ),
    )
    saved = application(repository).save_mappings(
        project.id,
        [LayerMapping(layer_id="SITE", kind=LayerKind.SITE_BORDER, confirmed=True)],
    )
    assert saved.layers[0].mapped_kind == LayerKind.SITE_BORDER
    selected = repository.read_source_features(
        project.id,
        {"SITE"},
        expected_version=saved.state_version,
        geometry_field="geometry",
    )
    assert selected[0]["geometry"]["type"] == "Polygon"
    with pytest.raises(DomainFailure) as error:
        application(repository).save_mappings(
            project.id,
            [LayerMapping(layer_id="ROAD", kind=LayerKind.SITE_BORDER, confirmed=True)],
        )
    assert error.value.reason == FailureReason.LIVE_BOUNDARY_UNUSABLE
    assert (
        repository.get(project.id, lightweight=True).state_version
        == saved.state_version
    )


def test_editor_mapping_stale_revision_does_not_change_source(
    imported: tuple[SqliteProjectRepository, Project],
) -> None:
    repository, project = imported
    project = repository.save(open_source_editor(project))
    stale = repository.get(project.id, lightweight=True)
    application(repository).save_mappings(project.id, [road_mapping()])
    before = tuple(stored_row(repository, project.id))
    with pytest.raises(ProjectVersionConflict):
        repository.save_import_mappings(stale, source_editor=True)
    assert tuple(stored_row(repository, project.id)) == before


def test_editor_mapping_sql_failure_rolls_back_metadata_revision_and_source(
    imported: tuple[SqliteProjectRepository, Project], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = imported
    project = repository.save(open_source_editor(project))
    compact = repository.get(project.id, lightweight=True)
    before = tuple(stored_row(repository, project.id))
    original = compact.model_dump()
    write = repository._write_project

    def fail_after_update(changed, **options):
        write(changed, **options)
        raise sqlite3.OperationalError("fixture rollback")

    monkeypatch.setattr(repository, "_write_project", fail_after_update)
    token = set_expected_project_version(str(compact.state_version))
    try:
        with pytest.raises(sqlite3.OperationalError):
            repository.save_import_mappings(compact, source_editor=True)
        assert compact.model_dump() == original
        assert expected_project_version() == compact.state_version
        assert tuple(stored_row(repository, project.id)) == before
    finally:
        reset_expected_project_version(token)


@pytest.mark.parametrize(
    "guard", ["source_review", "geometry", "plan", "mode", "native_session"]
)
def test_editor_mapping_checks_saved_preconditions_in_same_cas(
    imported: tuple[SqliteProjectRepository, Project], guard: str
) -> None:
    repository, project = imported
    project = repository.save(open_source_editor(project))
    compact = repository.get(project.id, lightweight=True)
    with repository._connection:
        path, value = {
            "source_review": ("$.source_review", "null"),
            "geometry": ("$.geometry", "null"),
            "plan": ("$.plan", "{}"),
            "mode": ("$.import_status.mode", '"legacy_dxf"'),
            "native_session": ("$.source_file.native_session", "null"),
        }[guard]
        repository._connection.execute(
            "UPDATE projects SET payload=json_set(payload, ?, json(?)), projection=json_set(projection, ?, json(?)) WHERE id=?",
            (path, value, path, value, project.id),
        )
    before = tuple(stored_row(repository, project.id))
    with pytest.raises(DomainFailure) as error:
        repository.save_import_mappings(compact, source_editor=True)
    assert error.value.reason == FailureReason.SOURCE_REVIEW_CHANGED
    assert tuple(stored_row(repository, project.id)) == before


@pytest.mark.parametrize("opened", [False, True])
def test_native_session_binding_preserves_graph_source_and_release_records(
    imported: tuple[SqliteProjectRepository, Project],
    monkeypatch: pytest.MonkeyPatch,
    opened: bool,
) -> None:
    repository, project = imported
    assert project.source_file is not None
    session = project.source_file.native_session
    assert session is not None
    project.source_file.native_session = None
    if opened:
        project = open_source_editor(project)
    project = repository.save(project, source_components={"Native/host.dwg": b"CAD"})
    project = repository.publish_release(
        project, "retained", '{"fixture":true}', {"dxf": b"DXF"}
    )
    project = repository.save_with_receipt(project, "kept", "mutation", {"ok": True})
    before = stored_row(repository, project.id)
    before_receipt = repository.mutation_receipt(project.id, "kept", "mutation")
    compact = repository.get(project.id, lightweight=True)
    original = compact.model_dump(mode="json")
    monkeypatch.setattr(
        repository,
        "_payloads",
        Mock(side_effect=AssertionError("Привязка не сериализует полный CAD-граф")),
    )
    saved = application(repository).bind_native_session(compact, session)
    after = stored_row(repository, project.id)
    before_payload, after_payload = (
        json.loads(before["payload"]),
        json.loads(after["payload"]),
    )
    assert saved.state_version == original["state_version"] + 1
    assert saved.geometry_version == original["geometry_version"]
    assert compact.model_dump(mode="json") == original
    assert after_payload["source_file"]["native_session"] == session.model_dump(
        mode="json"
    )
    before_payload["source_file"]["native_session"] = after_payload["source_file"][
        "native_session"
    ]
    for field in ("state_version", "updated_at"):
        before_payload[field] = after_payload[field]
    assert after_payload == before_payload
    assert after["source_json"] == before["source_json"]
    assert (
        json.loads(after["projection"])["source_file"] == after_payload["source_file"]
    )
    assert repository.get_source(project.id) == SOURCE_BYTES
    assert repository.get_source_components(project.id) == {"Native/host.dwg": b"CAD"}
    assert repository.get_release(project.id, "retained") == '{"fixture":true}'
    assert repository.get_export(project.id, "dxf") == b"DXF"
    assert repository.mutation_receipt(project.id, "kept", "mutation") == before_receipt
    restored = repository.get(project.id)
    assert restored.source_file is not None
    assert restored.source_file.native_session == session


@pytest.mark.parametrize("changed", ["source", "geometry", "state"])
def test_native_session_binding_rejects_stale_revision_without_partial_write(
    imported: tuple[SqliteProjectRepository, Project], changed: str
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_file is not None
    session = compact.source_file.native_session
    assert session is not None
    winner = repository.get(project.id)
    if changed == "source":
        assert winner.source_file is not None
        winner.source_file.content_sha256 = "e" * 64
    elif changed == "geometry":
        winner.geometry_version += 1
    else:
        winner.name = "Новая ревизия"
    repository.save(winner)
    before = stored_row(repository, project.id)
    token = set_expected_project_version(str(compact.state_version))
    try:
        with pytest.raises(ProjectVersionConflict):
            repository.bind_native_session(compact, session)
        assert expected_project_version() == compact.state_version
    finally:
        reset_expected_project_version(token)
    assert tuple(stored_row(repository, project.id)) == tuple(before)
    assert repository.get_source(project.id) == SOURCE_BYTES


def test_native_session_binding_rejects_capture_mismatch(
    imported: tuple[SqliteProjectRepository, Project],
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_file is not None
    session = compact.source_file.native_session
    assert session is not None
    before = stored_row(repository, project.id)
    with pytest.raises(DomainFailure):
        repository.bind_native_session(
            compact, session.model_copy(update={"snapshot_sha256": "e" * 64})
        )
    assert tuple(stored_row(repository, project.id)) == tuple(before)


def test_already_open_editor_reads_only_projection_and_preserves_revision(
    imported: tuple[SqliteProjectRepository, Project], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = imported
    project = repository.save(open_source_editor(project))
    before = stored_row(repository, project.id)
    service = application(repository)
    original_get = repository.get
    reads: list[bool] = []

    def compact_get(project_id: str, *, lightweight: bool = False) -> Project:
        reads.append(lightweight)
        assert lightweight, "Открытый редактор не перечитывает CAD-граф"
        return original_get(project_id, lightweight=lightweight)

    monkeypatch.setattr(repository, "get", compact_get)
    monkeypatch.setattr(
        repository,
        "save",
        Mock(side_effect=AssertionError("Повторное открытие не пишет проект")),
    )
    opened = service.open_editor(project.id)
    assert reads == [True]
    assert opened.map_ready and opened.source_review == project.source_review
    assert opened.geometry_version == project.geometry_version
    assert opened.state_version == project.state_version
    assert tuple(stored_row(repository, project.id)) == tuple(before)
    cast(Mock, service.invalidate_spatial).assert_not_called()


@pytest.mark.parametrize("guard", ["source", "geometry", "mode"])
def test_native_session_binding_checks_stored_metadata_in_same_cas(
    imported: tuple[SqliteProjectRepository, Project], guard: str
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_file is not None
    session = compact.source_file.native_session
    assert session is not None
    if guard == "geometry":
        compact.geometry_version += 1
    elif guard == "source":
        repository._connection.execute(
            "UPDATE projects SET payload=json_set(payload, '$.source_file.content_sha256', ?) WHERE id=?",
            ("e" * 64, project.id),
        )
        repository._connection.commit()
    else:
        repository._connection.execute(
            "UPDATE projects SET projection=json_set(projection, '$.import_status.mode', ?) WHERE id=?",
            (ImportMode.SOURCE_DXF.value, project.id),
        )
        repository._connection.commit()
    before = stored_row(repository, project.id)
    with pytest.raises(DomainFailure) as failure:
        repository.bind_native_session(compact, session)
    assert failure.value.reason == FailureReason.HANDOFF_SOURCE_CHANGED
    assert tuple(stored_row(repository, project.id)) == tuple(before)


def test_native_session_binding_write_failure_keeps_request_and_storage_revision(
    imported: tuple[SqliteProjectRepository, Project],
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_file is not None
    session = compact.source_file.native_session
    assert session is not None
    before = stored_row(repository, project.id)
    original = compact.model_dump(mode="json")
    repository._connection.execute(
        "CREATE TRIGGER reject_binding BEFORE UPDATE ON projects BEGIN SELECT RAISE(ABORT, 'fixture rejection'); END"
    )
    token = set_expected_project_version(str(compact.state_version))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="fixture rejection"):
            repository.bind_native_session(compact, session)
        assert expected_project_version() == compact.state_version
    finally:
        reset_expected_project_version(token)
    assert compact.model_dump(mode="json") == original
    assert tuple(stored_row(repository, project.id)) == tuple(before)


def test_initial_editor_still_loads_full_source_and_preserves_features(
    imported: tuple[SqliteProjectRepository, Project], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = imported
    assert project.source_geometry is not None
    original = project.source_geometry.feature_collection
    original_get = repository.get
    reads: list[bool] = []

    def tracked_get(project_id: str, *, lightweight: bool = False) -> Project:
        reads.append(lightweight)
        return original_get(project_id, lightweight=lightweight)

    monkeypatch.setattr(repository, "get", tracked_get)
    opened = application(repository).open_editor(project.id)
    assert reads == [True, False]
    assert opened.geometry is not None
    assert opened.geometry.feature_collection == original
    assert opened.state_version == project.state_version + 1
    assert opened.geometry_version == project.geometry_version + 1
    assert opened.source_geometry is None and opened.map_ready
    assert repository.get_source(project.id) == SOURCE_BYTES


@pytest.mark.parametrize("refusal", ["read_only", "stale_if_match"])
def test_open_editor_projection_path_keeps_admission_and_version_guards(
    imported: tuple[SqliteProjectRepository, Project],
    monkeypatch: pytest.MonkeyPatch,
    refusal: str,
) -> None:
    repository, project = imported
    project = repository.save(open_source_editor(project))
    if refusal == "read_only":
        project.import_status.editability = ImportEditability.READ_ONLY
        repository.save(project)
    before = stored_row(repository, project.id)
    original_get = repository.get

    def compact_get(project_id: str, *, lightweight: bool = False) -> Project:
        assert lightweight
        return original_get(project_id, lightweight=lightweight)

    monkeypatch.setattr(repository, "get", compact_get)
    token = (
        set_expected_project_version(str(project.state_version - 1))
        if refusal == "stale_if_match"
        else None
    )
    try:
        with pytest.raises(
            ProjectVersionConflict if refusal == "stale_if_match" else ValueError
        ):
            application(repository).open_editor(project.id)
    finally:
        if token is not None:
            reset_expected_project_version(token)
    assert tuple(stored_row(repository, project.id)) == tuple(before)


@pytest.mark.parametrize("select_border", [False, True])
def test_initial_mapping_preserves_source_without_full_project_read(
    imported: tuple[SqliteProjectRepository, Project],
    monkeypatch: pytest.MonkeyPatch,
    select_border: bool,
) -> None:
    repository, project = imported
    service = application(repository)
    project.map_ready = True
    project.site_area_m2 = project.planning_area_m2 = project.allowed_area_m2 = 10
    project.planting_zones = [
        PlantingZoneAssignment(
            id="old-zone", label="Прежняя зона", geometry=mapping(box(0, 0, 5, 5))
        )
    ]
    repository.save(project)
    before = stored_row(repository, project.id)
    get = repository.get
    read_versions: list[bool] = []

    def compact_only(project_id: str, *, lightweight: bool = False) -> Project:
        read_versions.append(lightweight)
        assert lightweight, "Начальное назначение не должно читать полный CAD-граф"
        return get(project_id, lightweight=True)

    monkeypatch.setattr(repository, "get", compact_only)
    mappings = [road_mapping()]
    if select_border:
        mappings.append(
            LayerMapping(layer_id="SITE", kind=LayerKind.SITE_BORDER, confirmed=True)
        )
    saved = service.save_mappings(project.id, mappings)
    after = stored_row(repository, project.id)
    assert read_versions == [True]
    assert saved.source_geometry is None
    assert after["source_json"] == before["source_json"]
    assert repository.get_source(project.id) == SOURCE_BYTES
    assert saved.state_version == project.state_version + 1
    assert saved.geometry_version == project.geometry_version + 1
    assert not saved.map_ready and saved.planting_zones == []
    assert saved.site_area_m2 is saved.planning_area_m2 is saved.allowed_area_m2 is None
    payload = json.loads(after["payload"])
    projection = json.loads(after["projection"])
    for field in (
        "name",
        "created_at",
        "source_file",
        "coordinate_reference",
        "source_geometry",
    ):
        assert payload[field] == json.loads(before["payload"])[field]
    for field in ("geometry", "plan", "source_review"):
        assert field in payload and payload[field] is None
    assert (
        payload["state_version"] == projection["state_version"] == saved.state_version
    )
    assert payload["layers"] == projection["layers"]
    cast(Mock, service.invalidate_spatial).assert_called_once_with(project.id)
    cast(Mock, service.dxf_reader.read).assert_not_called()
    with closing(SqliteProjectRepository(repository.path)) as reopened:
        restored = reopened.get(project.id)
        assert restored.source_geometry is not None
        assert restored.source_geometry == project.source_geometry
        assert len(restored.source_geometry.feature_collection["features"]) == 3
        assert restored.source_file is not None
        assert restored.source_file.content_sha256 == SOURCE_HASH
        assert reopened.get_source(project.id) == SOURCE_BYTES
        assert restored.layers[1].mapping_confirmed
        assert restored.state_version == saved.state_version


def test_visibility_edit_keeps_geometry_version_and_readiness(
    imported: tuple[SqliteProjectRepository, Project],
) -> None:
    repository, project = imported
    service = application(repository)
    saved = service.save_mappings(project.id, [road_mapping()])
    changed = service.save_mappings(project.id, [road_mapping(visible=False)])
    assert changed.geometry_version == saved.geometry_version
    assert not changed.layers[1].visible
    assert changed.state_version == saved.state_version + 1
    cast(Mock, service.invalidate_spatial).assert_called_once_with(project.id)


def test_source_subset_is_ordered_revision_bound_and_empty_is_not_missing(
    imported: tuple[SqliteProjectRepository, Project],
) -> None:
    repository, project = imported
    assert project.source_geometry is not None
    features = project.source_geometry.feature_collection["features"]
    selected = repository.read_source_features(
        project.id, {"SECOND_SITE", "SITE"}, expected_version=project.state_version
    )
    assert selected == json.loads(json.dumps([features[0], features[2]]))
    for names in (set(), {"missing-layer"}):
        assert (
            repository.read_source_features(
                project.id, names, expected_version=project.state_version
            )
            == []
        )
    repository.save(project)
    with pytest.raises(ProjectVersionConflict):
        repository.read_source_features(project.id, {"SITE"}, expected_version=1)
    with pytest.raises(ProjectNotFound):
        repository.read_source_features("absent", {"SITE"}, expected_version=1)


@pytest.mark.parametrize(
    "fault", ["missing", "line", "missing_hash", "not_native", "thin", "candidate_only"]
)
def test_invalid_selected_border_rejects_without_publishing_mappings(
    imported: tuple[SqliteProjectRepository, Project],
    fault: str,
) -> None:
    repository, project = imported
    assert project.source_geometry is not None
    features = project.source_geometry.feature_collection["features"]
    border = features[0]
    if fault in {"missing", "candidate_only"}:
        features.pop(0)
    elif fault == "line":
        border["geometry"] = {"type": "LineString", "coordinates": [[0, 0], [5, 5]]}
    elif fault == "missing_hash":
        border["properties"].pop("source_geometry_content_sha256")
    elif fault == "not_native":
        border["properties"]["source_native_geometry"] = False
    else:
        border["geometry"] = mapping(box(0, 0, 100, 1))
    if fault == "candidate_only":
        project.layers[0].boundary_candidate = BoundaryCandidate(
            status=BoundaryCandidateStatus.USABLE,
            basis="synthetic-native-surface",
            area_m2=10000,
            inset_1_5m_area_m2=9409,
        )
    repository.save(project)
    before = stored_row(repository, project.id)
    service = application(repository)
    with pytest.raises(DomainFailure) as failure:
        service.save_mappings(
            project.id,
            [LayerMapping(layer_id="SITE", kind=LayerKind.SITE_BORDER, confirmed=True)],
        )
    assert failure.value.reason is FailureReason.LIVE_BOUNDARY_UNUSABLE
    assert failure.value.params == {"layer_names": ["SITE"]}
    assert tuple(stored_row(repository, project.id)) == tuple(before)
    assert repository.get_source(project.id) == SOURCE_BYTES
    cast(Mock, service.invalidate_spatial).assert_not_called()


def test_partial_mapping_still_validates_previously_selected_border(
    imported: tuple[SqliteProjectRepository, Project],
) -> None:
    repository, project = imported
    project.layers[0].mapped_kind = LayerKind.SITE_BORDER
    project.layers[0].mapping_confirmed = True
    assert project.source_geometry is not None
    project.source_geometry.feature_collection["features"].pop(0)
    repository.save(project)
    service = application(repository)
    with pytest.raises(DomainFailure) as failure:
        service.save_mappings(project.id, [road_mapping()])
    assert failure.value.reason is FailureReason.LIVE_BOUNDARY_UNUSABLE
    assert repository.get(project.id).layers[1].mapped_kind is None


@pytest.mark.parametrize(
    "outside_initial", ["prepared", "plan", "review", "dxf", "no_session"]
)
def test_projects_outside_initial_native_import_use_full_save(
    imported: tuple[SqliteProjectRepository, Project],
    monkeypatch: pytest.MonkeyPatch,
    outside_initial: str,
) -> None:
    repository, project = imported
    if outside_initial == "prepared":
        project.geometry = project.source_geometry
    elif outside_initial == "plan":
        project.plan = Plan()
    elif outside_initial == "review":
        project.source_review = SourceReview()
    elif outside_initial == "dxf":
        project.import_status.mode = ImportMode.SOURCE_DXF
    else:
        assert project.source_file is not None
        project.source_file.native_session = None
    repository.save(project)
    service = application(repository)
    full_save = Mock(wraps=repository.save)
    narrow_save = Mock(side_effect=AssertionError("Начальный порт здесь запрещён"))
    monkeypatch.setattr(repository, "save", full_save)
    monkeypatch.setattr(repository, "save_import_mappings", narrow_save)
    service.save_mappings(project.id, [road_mapping()])
    full_save.assert_called_once()
    narrow_save.assert_not_called()
    retained = repository.get(project.id)
    if outside_initial == "review":
        assert retained.geometry is not None
        assert project.source_geometry is not None
        assert [
            feature["geometry"]
            for feature in retained.geometry.feature_collection["features"]
        ] == [
            feature["geometry"]
            for feature in project.source_geometry.feature_collection["features"]
        ]
    else:
        assert retained.source_geometry == project.source_geometry


def test_stale_if_match_and_concurrent_save_publish_no_mapping(
    imported: tuple[SqliteProjectRepository, Project],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, project = imported
    service = application(repository)
    repository.save(project)
    before = stored_row(repository, project.id)
    token = set_expected_project_version("1")
    try:
        with pytest.raises(ProjectVersionConflict):
            service.save_mappings(project.id, [road_mapping()])
        assert expected_project_version() == 1
    finally:
        reset_expected_project_version(token)
    assert tuple(stored_row(repository, project.id)) == tuple(before)
    narrow_save = repository.save_import_mappings

    def concurrent_save(changed: Project) -> Project:
        with closing(SqliteProjectRepository(repository.path)) as writer:
            current = writer.get(project.id)
            current.name = "Другая ревизия"
            writer.save(current)
        return narrow_save(changed)

    monkeypatch.setattr(repository, "save_import_mappings", concurrent_save)
    with pytest.raises(ProjectVersionConflict):
        service.save_mappings(project.id, [road_mapping()])
    retained = repository.get(project.id)
    assert retained.name == "Другая ревизия"
    assert retained.layers[1].mapped_kind is None
    assert repository.get_source(project.id) == SOURCE_BYTES
    cast(Mock, service.invalidate_spatial).assert_not_called()


def test_write_failure_rolls_back_revision_projection_and_request_context(
    imported: tuple[SqliteProjectRepository, Project],
) -> None:
    repository, project = imported
    before = stored_row(repository, project.id)
    compact = repository.get(project.id, lightweight=True)
    compact.layers[1].mapped_kind = LayerKind.ROAD
    with repository._connection:
        repository._connection.execute(
            "CREATE TRIGGER reject_mapping BEFORE UPDATE ON projects "
            "BEGIN SELECT RAISE(ABORT, 'mapping storage fault'); END"
        )
    token = set_expected_project_version(str(project.state_version))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="mapping storage fault"):
            repository.save_import_mappings(compact)
        assert compact.state_version == project.state_version
        assert compact.updated_at == project.updated_at
        assert expected_project_version() == project.state_version
    finally:
        reset_expected_project_version(token)
    assert tuple(stored_row(repository, project.id)) == tuple(before)
    assert repository.get_source(project.id) == SOURCE_BYTES


@pytest.mark.parametrize(
    "guard",
    ["geometry", "plan", "source_review", "mode", "native_session", "source_geometry"],
)
def test_narrow_port_checks_saved_precondition_before_update(
    imported: tuple[SqliteProjectRepository, Project], guard: str
) -> None:
    repository, project = imported
    if guard == "geometry":
        project.geometry = project.source_geometry
    elif guard == "plan":
        project.plan = Plan()
    elif guard == "source_review":
        project.source_review = SourceReview()
    elif guard == "mode":
        project.import_status.mode = ImportMode.SOURCE_DXF
    elif guard == "native_session":
        assert project.source_file is not None
        project.source_file.native_session = None
    else:
        project.source_geometry = None
    repository.save(project)
    before = stored_row(repository, project.id)
    compact = repository.get(project.id, lightweight=True)
    compact.layers[1].mapped_kind = LayerKind.ROAD
    with pytest.raises(DomainFailure):
        repository.save_import_mappings(compact)
    assert tuple(stored_row(repository, project.id)) == tuple(before)
    assert compact.state_version == project.state_version
    assert repository.get_source(project.id) == SOURCE_BYTES


@pytest.mark.parametrize("opened", [False, True])
@pytest.mark.parametrize("clear", [False, True])
def test_preparation_recipe_save_preserves_all_other_stored_project_fields(
    imported: tuple[SqliteProjectRepository, Project],
    monkeypatch: pytest.MonkeyPatch,
    opened: bool,
    clear: bool,
) -> None:
    repository, project = imported
    assert project.source_file is not None
    if opened:
        project = open_source_editor(project)
    else:
        project.geometry = project.source_geometry
    project.plan = Plan()
    project.layers[1].mapped_kind = LayerKind.ROAD
    project.layers[1].mapping_confirmed = True
    assert project.source_file is not None
    if clear:
        project.source_file.preparation_recipe = TopologyRecipe(
            source_sha256=SOURCE_HASH, companion_layers=("SITE",)
        )
    project = repository.save(project, source_components={"Native/host.dwg": b"CAD"})
    project = repository.publish_release(
        project, "retained", '{"fixture":true}', {"dxf": b"DXF"}
    )
    project = repository.save_with_receipt(project, "kept", "mutation", {"ok": True})
    before = stored_row(repository, project.id)
    directory = repository._geometry_store.directory(project.id)
    files = {path.name: path.read_bytes() for path in directory.glob("*.json.gz")}
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_geometry is None and compact.source_file is not None
    recipe = (
        None
        if clear
        else TopologyRecipe(
            source_sha256=SOURCE_HASH,
            companion_layers=("SECOND_SITE", "SITE"),
            excluded_routes=("BB",),
        )
    )
    compact.source_file.preparation_recipe = recipe
    # Компактный вход может не содержать карту и иметь несохранённые поля.
    # Метод записывает только рецепт, сохраняя остальные значения из SQLite.
    compact.geometry = None
    compact.plan = None
    compact.name = "Несохранённое имя"
    compact.source_file.name = "Несохранённое имя исходника"
    compact.layers[1].mapped_kind = LayerKind.IGNORE
    monkeypatch.setattr(
        repository, "_payloads", Mock(side_effect=AssertionError("Нет полной записи"))
    )
    monkeypatch.setattr(
        repository._geometry_store,
        "stage",
        Mock(side_effect=AssertionError("Нет записи геометрии")),
    )
    token = set_expected_project_version(str(compact.state_version))
    try:
        saved = repository.save_preparation_recipe(compact)
        assert expected_project_version() == saved.state_version
    finally:
        reset_expected_project_version(token)
    after = stored_row(repository, project.id)
    for field in ("payload", "projection"):
        previous, current = json.loads(before[field]), json.loads(after[field])
        previous["source_file"]["preparation_recipe"] = (
            recipe.model_dump(mode="json") if recipe is not None else None
        )
        for name in ("state_version", "updated_at"):
            previous[name] = current[name]
        assert current == previous
    assert saved.state_version == project.state_version + 1
    assert saved.geometry_version == project.geometry_version
    assert after["source_json"] == before["source_json"]
    assert {
        path.name: path.read_bytes() for path in directory.glob("*.json.gz")
    } == files
    assert repository.get_source(project.id) == SOURCE_BYTES
    assert repository.get_source_components(project.id) == {"Native/host.dwg": b"CAD"}
    assert repository.get_release(project.id, "retained") == '{"fixture":true}'
    assert repository.get_export(project.id, "dxf") == b"DXF"
    assert repository.mutation_receipt(project.id, "kept", "mutation")["ok"] is True


@pytest.mark.parametrize("changed", ["source", "geometry", "state"])
def test_preparation_recipe_save_rejects_stale_project_revision(
    imported: tuple[SqliteProjectRepository, Project], changed: str
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_file is not None
    compact.source_file.preparation_recipe = TopologyRecipe(
        source_sha256=SOURCE_HASH, companion_layers=("SITE",)
    )
    winner = repository.get(project.id)
    if changed == "source":
        assert winner.source_file is not None
        winner.source_file.content_sha256 = "e" * 64
    elif changed == "geometry":
        winner.geometry_version += 1
    else:
        winner.name = "Новая ревизия"
    repository.save(winner)
    before = tuple(stored_row(repository, project.id))
    original = compact.model_dump()
    token = set_expected_project_version(str(compact.state_version))
    try:
        with pytest.raises(ProjectVersionConflict):
            repository.save_preparation_recipe(compact)
        assert expected_project_version() == compact.state_version
    finally:
        reset_expected_project_version(token)
    assert compact.model_dump() == original
    assert tuple(stored_row(repository, project.id)) == before


@pytest.mark.parametrize("field", ["payload", "projection"])
@pytest.mark.parametrize(
    "guard",
    [
        "source",
        "geometry",
        "native_snapshot",
        "native_source",
        "inventory",
        "units",
        "mode",
    ],
)
def test_preparation_recipe_save_checks_current_capture_in_same_cas(
    imported: tuple[SqliteProjectRepository, Project], field: str, guard: str
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_file is not None
    compact.source_file.preparation_recipe = TopologyRecipe(
        source_sha256=SOURCE_HASH, companion_layers=("SITE",)
    )
    path, value = {
        "source": ("$.source_file.content_sha256", "e" * 64),
        "geometry": ("$.geometry_version", compact.geometry_version + 1),
        "native_snapshot": ("$.source_file.native_session.snapshot_sha256", "e" * 64),
        "native_source": ("$.source_file.native_session.source_sha256", "e" * 64),
        "inventory": ("$.source_file.native_session.inventory_sha256", "e" * 64),
        "units": ("$.source_file.native_session.units_code", 4),
        "mode": ("$.import_status.mode", ImportMode.SOURCE_DXF.value),
    }[guard]
    with repository._connection:
        repository._connection.execute(
            f"UPDATE projects SET {field}=json_set({field}, ?, ?) WHERE id=?",
            (path, value, project.id),
        )
    before = tuple(stored_row(repository, project.id))
    original = compact.model_dump()
    with pytest.raises(DomainFailure) as error:
        repository.save_preparation_recipe(compact)
    assert error.value.reason == FailureReason.HANDOFF_SOURCE_CHANGED
    assert compact.model_dump() == original
    assert tuple(stored_row(repository, project.id)) == before


@pytest.mark.parametrize("invalid", ["recipe", "session", "missing_session", "source"])
def test_preparation_recipe_save_rejects_mismatched_or_missing_capture(
    imported: tuple[SqliteProjectRepository, Project], invalid: str
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_file is not None
    compact.source_file.preparation_recipe = TopologyRecipe(
        source_sha256="e" * 64 if invalid == "recipe" else SOURCE_HASH,
        companion_layers=("SITE",),
    )
    if invalid == "session":
        assert compact.source_file.native_session is not None
        compact.source_file.native_session = (
            compact.source_file.native_session.model_copy(
                update={"snapshot_sha256": "e" * 64}
            )
        )
    elif invalid == "missing_session":
        compact.source_file.native_session = None
    elif invalid == "source":
        compact.source_file = None
    before = tuple(stored_row(repository, project.id))
    with pytest.raises(DomainFailure):
        repository.save_preparation_recipe(compact)
    assert tuple(stored_row(repository, project.id)) == before


def test_preparation_recipe_save_rolls_back_sql_update_and_request_revision(
    imported: tuple[SqliteProjectRepository, Project], monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    assert compact.source_file is not None
    compact.source_file.preparation_recipe = TopologyRecipe(
        source_sha256=SOURCE_HASH, companion_layers=("SITE",)
    )
    before = tuple(stored_row(repository, project.id))
    original = compact.model_dump()
    write = repository._write_project

    def fail_after_update(changed, **options):
        write(changed, **options)
        raise sqlite3.OperationalError("recipe rollback")

    monkeypatch.setattr(repository, "_write_project", fail_after_update)
    token = set_expected_project_version(str(compact.state_version))
    try:
        with pytest.raises(sqlite3.OperationalError, match="recipe rollback"):
            repository.save_preparation_recipe(compact)
        assert expected_project_version() == compact.state_version
    finally:
        reset_expected_project_version(token)
    assert compact.model_dump() == original
    assert tuple(stored_row(repository, project.id)) == before


def test_preparation_recipe_save_rejects_if_match_for_another_revision(
    imported: tuple[SqliteProjectRepository, Project],
) -> None:
    repository, project = imported
    compact = repository.get(project.id, lightweight=True)
    before = tuple(stored_row(repository, project.id))
    token = set_expected_project_version(str(compact.state_version + 1))
    try:
        with pytest.raises(ProjectVersionConflict):
            repository.save_preparation_recipe(compact)
    finally:
        reset_expected_project_version(token)
    assert tuple(stored_row(repository, project.id)) == before
