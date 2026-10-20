"""Геометрия вне строки SQLite: неизменность, CAS, холодное чтение и восстановление."""

import gzip
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import FrozenInstanceError
from hashlib import sha256
from pathlib import Path
from threading import Barrier, Event

import ijson
import pytest
from test_import_mapping_storage import imported as imported

from app.geometry.contracts import GeometrySnapshot
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.operations.progress import OperationCancelled, operation_cancellation
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project
from app.projects.geometry_storage import (
    GEOMETRY_STORAGE_KEY,
    GeometryReference,
    GeometryStore,
    geometry_references,
)
from app.projects.ports import ProjectGeometryView
from app.projects.workspace_backup import backup_workspace
from app.projects.workspace_restore import restore_workspace_backup
from app.shared.failures import ProjectNotFound


def collection(*, marker="исходный", size=4096):
    return {
        "type": "FeatureCollection",
        "bbox": [0.25, 0, 9, 9.75],
        "foreign": {"integer": 10**40, "fraction": 1.25, "marker": marker},
        "features": [
            {
                "type": "Feature",
                "id": f"route-{number}",
                "geometry": {"type": "Point", "coordinates": [number, number + 0.25]},
                "properties": {
                    "source_layer": "А" if number % 2 else "Б",
                    "attributes": {"исходное": marker, "complete": "x" * size},
                },
            }
            for number in range(10)
        ],
    }


def project_fixture(repository, *, identifier="legacy'../project"):
    data = collection()
    graph = GeometrySnapshot(
        feature_collection=data, calculation_scope="available_data", site_area_m2=10
    )
    project = repository.create(
        Project(
            id=identifier, name="Полный проект", geometry=graph, source_geometry=graph
        )
    )
    return project, deepcopy(data)


def stored(repository, project):
    payload = json.loads(
        repository._connection.execute(
            "SELECT payload FROM projects WHERE id=?", (project.id,)
        ).fetchone()[0]
    )
    return payload, geometry_references(payload)


def geometry_files(repository, project):
    return list(repository._geometry_store.directory(project.id).glob("*.json.gz"))


def assert_graphs(project, expected):
    assert project.geometry.feature_collection == expected
    assert project.source_geometry.feature_collection == expected
    assert GEOMETRY_STORAGE_KEY not in project.model_dump()


def test_sqlite_row_limit_create_save_history_cold_read_backup_and_cas(tmp_path):
    database = tmp_path / "projects.sqlite3"
    repository = SqliteProjectRepository(database)
    repository._connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 16384)
    project, expected = project_fixture(repository)
    assert len(project.model_dump_json().encode()) > 16384
    payload, references = stored(repository, project)
    assert len(json.dumps(payload).encode()) < 16384
    assert references["geometry"] == references["source_geometry"]
    assert len(geometry_files(repository, project)) == 1
    assert_graphs(repository.get(project.id), expected)
    stale = repository.get(project.id)
    project.name = "Новое имя"
    repository.save(project)
    with pytest.raises(ProjectVersionConflict):
        repository.save(stale)
    before = repository.get(project.id)
    project.name = "История"
    history = SqliteProjectHistory(repository)
    history.commit(project, before, "Изменение проекта")
    repository.publish_export(project, "artifact", b"export bytes")
    assert len(geometry_files(repository, project)) == 1
    assert not list(repository._geometry_store.root.rglob("*.pending"))
    assert_graphs(repository.list()[0], expected)
    backup = backup_workspace(database, reason="geometry-row-limit")
    restored = restore_workspace_backup(backup, tmp_path / "restored")
    repository.close()
    for path in (database, restored):
        reopened = SqliteProjectRepository(path)
        try:
            assert_graphs(reopened.get(project.id), expected)
            assert reopened.get_export(project.id, "artifact") == b"export bytes"
        finally:
            reopened.close()


def test_selected_layers_keep_attributes_order_and_revision(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        project, expected = project_fixture(repository)
        selected = repository.read_source_features(
            project.id, {"А"}, expected_version=project.state_version
        )
        assert selected == expected["features"][1::2]
        assert (
            repository.read_source_features(
                project.id, set(), expected_version=project.state_version
            )
            == []
        )
        repository.save(project)
        with pytest.raises(ProjectVersionConflict):
            repository.read_source_features(project.id, {"А"}, expected_version=1)
    finally:
        repository.close()


def test_mapping_and_native_binding_preserve_sidecars_without_restaging(
    imported, monkeypatch
):
    repository, project = imported
    before, references = stored(repository, project)
    paths = geometry_files(repository, project)
    original = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths}
    expected = repository.get(project.id).source_geometry.feature_collection

    def forbidden(*args):
        raise AssertionError("Metadata-only changes must not stage the source graph")

    monkeypatch.setattr(repository._geometry_store, "stage", forbidden)
    compact = repository.get(project.id, lightweight=True)
    repository.save_import_mappings(compact)
    session = compact.source_file.native_session.model_copy(update={"pid": 2})
    bound = repository.bind_native_session(compact, session)
    assert stored(repository, project)[1] == references
    assert repository.get(project.id).source_geometry.feature_collection == expected
    assert repository.get(project.id).source_file.native_session.pid == 2
    assert bound.state_version == 3
    assert {
        path: (path.read_bytes(), path.stat().st_mtime_ns) for path in paths
    } == original


def test_nested_reads_do_not_commit_outer_revision_and_cleanup_full_write(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        project, expected = project_fixture(repository)
        original_payload, _ = stored(repository, project)
        project.geometry.feature_collection = collection(marker="не записан")
        with pytest.raises(RuntimeError, match="rollback"):
            with repository.revision_transaction(project):
                repository._write_project(project)
                assert repository.get(project.id).state_version == 2
                repository.list()
                repository.read_source_features(project.id, {"А"}, expected_version=2)
                assert repository._connection.in_transaction
                raise RuntimeError("rollback")
        assert project.state_version == 1
        assert stored(repository, project)[0] == original_payload
        assert_graphs(repository.get(project.id), expected)
        assert len(geometry_files(repository, project)) == 1
        assert not list(repository._geometry_store.root.rglob("*.pending"))
    finally:
        repository.close()


def test_failed_export_after_full_write_rolls_back_sidecar_and_revision(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        project, expected = project_fixture(repository)
        repository.publish_export(project, "same", b"first")
        project.geometry.feature_collection = collection(marker="не выпущен")
        with pytest.raises(ValueError, match="уже существует"):
            repository.publish_export(project, "same", b"second")
        assert project.state_version == 2
        assert_graphs(repository.get(project.id), expected)
        assert len(geometry_files(repository, project)) == 1
    finally:
        repository.close()


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "changed",
        "raw-sha",
        "count",
        "symlink",
        "root-symlink",
        "directory-symlink",
        "conflict",
        "unknown-ref",
    ],
)
def test_missing_corrupt_or_conflicting_geometry_is_never_accepted(tmp_path, fault):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        project, _ = project_fixture(repository)
        payload, refs = stored(repository, project)
        path = repository._geometry_store.path(project.id, refs["source_geometry"])
        if fault == "missing":
            path.unlink()
        elif fault == "changed":
            data = bytearray(path.read_bytes())
            data[-1] ^= 1
            path.write_bytes(data)
        elif fault == "symlink":
            other = tmp_path / "aliased.gz"
            path.replace(other)
            path.symlink_to(other)
        elif fault in {"root-symlink", "directory-symlink"}:
            directory = (
                repository._geometry_store.root
                if fault == "root-symlink"
                else path.parent
            )
            other = tmp_path / "aliased-directory"
            directory.replace(other)
            directory.symlink_to(other, target_is_directory=True)
        else:
            if fault == "raw-sha":
                payload[GEOMETRY_STORAGE_KEY]["source_geometry"]["sha256"] = "0" * 64
            elif fault == "count":
                payload[GEOMETRY_STORAGE_KEY]["source_geometry"]["features"] += 1
            elif fault == "conflict":
                payload["source_geometry"]["feature_collection"]["features"] = [
                    {"type": "Feature"}
                ]
            elif fault == "unknown-ref":
                payload[GEOMETRY_STORAGE_KEY]["other"] = payload[GEOMETRY_STORAGE_KEY][
                    "geometry"
                ]
            with repository._connection:
                repository._connection.execute(
                    "UPDATE projects SET payload=? WHERE id=?",
                    (json.dumps(payload), project.id),
                )
        for read in (
            lambda: repository.get(project.id),
            lambda: repository.read_source_features(
                project.id, {"А"}, expected_version=1
            ),
        ):
            with pytest.raises((ValueError, OSError)):
                read()
    finally:
        repository.close()


def test_backup_requires_every_reference_and_restore_does_not_publish_partial_data(
    tmp_path,
):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        project, _ = project_fixture(repository)
        backup = backup_workspace(repository.path, reason="geometry-integrity")
        manifest_path = backup / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        assert len(manifest["geometry_files"]) == 1
        manifest["geometry_files"] = []
        manifest_path.write_text(json.dumps(manifest))
        destination = tmp_path / "invalid-restore"
        with pytest.raises((ValueError, FileNotFoundError)):
            restore_workspace_backup(backup, destination)
        assert not destination.exists()
        before = set(Path(str(repository.path) + ".backups").iterdir())
        geometry_files(repository, project)[0].unlink()
        with pytest.raises((ValueError, FileNotFoundError)):
            backup_workspace(repository.path, reason="missing-geometry")
        assert set(Path(str(repository.path) + ".backups").iterdir()) == before
    finally:
        repository.close()


def test_legacy_inline_and_memory_projects_keep_existing_contract(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    memory = SqliteProjectRepository(":memory:")
    try:
        project, expected = project_fixture(memory)
        repository.create(Project(id=project.id, name=project.name))
        with repository._connection:
            repository._connection.execute(
                "UPDATE projects SET payload=? WHERE id=?",
                (project.model_dump_json(), project.id),
            )
        assert_graphs(repository.get(project.id), expected)
        assert (
            repository.read_source_features(project.id, {"А"}, expected_version=1)
            == expected["features"][1::2]
        )
        assert GEOMETRY_STORAGE_KEY not in stored(memory, project)[0]
        assert repository.list()[0].id == project.id
    finally:
        memory.close()
        repository.close()


def test_list_keeps_pinned_revision_without_applying_request_if_match(
    tmp_path, monkeypatch
):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    other = SqliteProjectRepository(repository.path)
    try:
        project, expected = project_fixture(repository)
        hydrate = repository._hydrate_geometry_row

        def remove_after_pinning(*args):
            other.delete(project.id, expected_version=1)
            return hydrate(*args)

        monkeypatch.setattr(repository, "_hydrate_geometry_row", remove_after_pinning)
        token = set_expected_project_version('"99"')
        try:
            listed = repository.list()
        finally:
            reset_expected_project_version(token)
        assert_graphs(listed[0], expected)
        with pytest.raises(ProjectNotFound):
            other.get(project.id)
    finally:
        other.close()
        repository.close()


def test_delete_during_staging_preserves_owned_pending_until_failed_cas(
    tmp_path, monkeypatch
):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    other = SqliteProjectRepository(repository.path)
    ready, resume = Event(), Event()
    try:
        project, _ = project_fixture(repository)
        project.geometry.feature_collection = collection(marker="late")
        stage_collection = repository._geometry_store._stage_collection

        def pause(*args):
            result = stage_collection(*args)
            ready.set()
            assert resume.wait(5)
            return result

        monkeypatch.setattr(repository._geometry_store, "_stage_collection", pause)
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(repository.save, project)
            assert ready.wait(5)
            other.delete(project.id)
            assert list(repository._geometry_store.root.rglob("*.pending"))
            resume.set()
            with pytest.raises(ProjectNotFound):
                future.result(timeout=5)
        assert not list(repository._geometry_store.root.rglob("*.pending"))
        assert not geometry_files(repository, project)
    finally:
        resume.set()
        other.close()
        repository.close()


def test_committed_delete_remains_successful_when_file_cleanup_is_denied(
    tmp_path, monkeypatch
):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    try:
        project, _ = project_fixture(repository)

        def denied(*args):
            raise PermissionError("opened file")

        monkeypatch.setattr(repository._geometry_store, "discard_project", denied)
        repository.delete(project.id)
        with pytest.raises(ProjectNotFound):
            repository.get(project.id)
    finally:
        repository.close()


def test_competing_cas_writers_cannot_unlink_the_same_winning_sidecar(
    tmp_path, monkeypatch
):
    first = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    second = SqliteProjectRepository(first.path)
    barrier = Barrier(2)
    try:
        project, _ = project_fixture(first)
        copies = [repository.get(project.id) for repository in (first, second)]
        replacement = collection(marker="одна новая ревизия")
        for repository, candidate in zip((first, second), copies, strict=True):
            candidate.geometry.feature_collection = deepcopy(replacement)
            stage = repository._geometry_store._stage_collection

            def synchronized(*args, original=stage):
                result = original(*args)
                barrier.wait(timeout=5)
                return result

            monkeypatch.setattr(
                repository._geometry_store, "_stage_collection", synchronized
            )
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [
                pool.submit(repository.save, candidate)
                for repository, candidate in zip((first, second), copies, strict=True)
            ]
            successes, conflicts = [], []
            for job in jobs:
                try:
                    successes.append(job.result(timeout=5))
                except ProjectVersionConflict as error:
                    conflicts.append(error)
        assert len(successes) == len(conflicts) == 1
        assert first.get(project.id).geometry.feature_collection == replacement
        assert second.get(project.id).state_version == 2
        assert len(geometry_files(first, project)) == 2
        assert not list(first._geometry_store.root.rglob("*.pending"))
    finally:
        second.close()
        first.close()


def stored_collection(tmp_path, content):
    compressed = gzip.compress(content, compresslevel=1, mtime=0)
    reference = GeometryReference(
        schema="green-atlas.project-geometry/1",
        sha256=sha256(content).hexdigest(),
        stored_sha256=sha256(compressed).hexdigest(),
        bytes=len(content),
        stored_bytes=len(compressed),
        features=10,
    )
    store = GeometryStore(tmp_path / "geometry")
    directory = store.directory("numeric-project")
    directory.mkdir(parents=True)
    path = store.path("numeric-project", reference)
    path.write_bytes(compressed)
    return store, reference, path


@pytest.mark.parametrize("integer", [7, 2**100, -(2**100)])
def test_library_numeric_modes_keep_exact_json_and_sidecar_bytes(
    tmp_path, monkeypatch, integer
):
    expected = collection(size=32)
    expected["foreign"]["integer"] = 7
    precise = [0.1, -0.0, 1.2345678901234567, 1e-300, 1.7976931348623157e308]
    # Переполнение появляется после уже прочитанных объектов выбранного слоя.
    expected["features"][-1]["properties"]["numeric"] = {
        "integer": integer,
        "precise": precise,
        "nested": [integer, {"fraction": precise[-1]}],
    }
    content = json.dumps(expected, ensure_ascii=False).encode()
    store, reference, path = stored_collection(tmp_path, content)
    before = path.read_bytes(), path.stat().st_mtime_ns
    modes = []
    read = store._read_collection

    def observe(*args, native_numbers):
        modes.append(native_numbers)
        return read(*args, native_numbers=native_numbers)

    monkeypatch.setattr(store, "_read_collection", observe)
    with store.open("numeric-project", reference) as stream:
        actual = store.read(stream, reference)
    assert actual == json.loads(content)
    assert [
        value.hex()
        for value in actual["features"][-1]["properties"]["numeric"]["precise"]
    ] == [value.hex() for value in precise]
    assert modes == ([True] if integer == 7 else [True, False])
    modes.clear()
    with store.open("numeric-project", reference) as stream:
        selected = store.read(stream, reference, {"А"})
    assert selected == expected["features"][1::2]
    assert modes == ([True] if integer == 7 else [True, False])
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert sha256(content).hexdigest() == reference.sha256


def test_float_overflow_uses_previous_json_numeric_semantics(tmp_path, monkeypatch):
    value = collection(size=32)
    value["foreign"]["integer"] = 7
    value["foreign"]["large_float"] = "numeric-overflow"
    content = json.dumps(value).encode().replace(b'"numeric-overflow"', b"1e400")
    store, reference, _ = stored_collection(tmp_path, content)
    modes = []
    read = store._read_collection

    def observe(*args, native_numbers):
        modes.append(native_numbers)
        return read(*args, native_numbers=native_numbers)

    monkeypatch.setattr(store, "_read_collection", observe)
    with store.open("numeric-project", reference) as stream:
        assert store.read(stream, reference) == json.loads(content)
    assert modes == [True, False]


@pytest.mark.parametrize("layers", [None, {"А"}, set()])
@pytest.mark.parametrize("fault", ["syntax", "trailing", "count", "raw-sha"])
def test_fast_parser_keeps_complete_integrity_and_only_retries_numeric_overflow(
    tmp_path, monkeypatch, layers, fault
):
    value = collection(size=32)
    value["foreign"]["integer"] = 7
    content = json.dumps(value).encode()
    if fault == "syntax":
        content = content[:-1] + b',"misleading":"integer overflow",broken}'
    elif fault == "trailing":
        content += b'\n{"features":[]}'
    store, reference, _ = stored_collection(tmp_path, content)
    if fault == "count":
        reference = reference.model_copy(update={"features": 11})
    elif fault == "raw-sha":
        reference = reference.model_copy(update={"sha256": "0" * 64})
    modes = []
    read = store._read_collection

    def observe(*args, native_numbers):
        modes.append(native_numbers)
        return read(*args, native_numbers=native_numbers)

    monkeypatch.setattr(store, "_read_collection", observe)
    with store.open("numeric-project", reference) as stream:
        with pytest.raises(
            ijson.JSONError if fault in {"syntax", "trailing"} else ValueError
        ):
            store.read(stream, reference, layers)
    assert modes == [True]


@pytest.mark.parametrize("phase", ["hash", "compatibility"])
def test_collection_cancellation_is_not_swallowed_by_numeric_retry(
    tmp_path, monkeypatch, phase
):
    content = json.dumps(collection(size=32)).encode()
    store, reference, path = stored_collection(tmp_path, content)
    before = path.read_bytes()
    cancelled = phase == "hash"
    modes = []
    read = store._read_collection

    def observe(*args, native_numbers):
        nonlocal cancelled
        modes.append(native_numbers)
        if not native_numbers:
            cancelled = True
        return read(*args, native_numbers=native_numbers)

    def check():
        if cancelled:
            raise OperationCancelled("Отменено")

    monkeypatch.setattr(store, "_read_collection", observe)
    with (
        operation_cancellation(check),
        store.open("numeric-project", reference) as stream,
    ):
        with pytest.raises(OperationCancelled):
            store.read(stream, reference, {"А"})
    assert modes == ([] if phase == "hash" else [True, False])
    assert path.read_bytes() == before


@pytest.mark.parametrize("calculated", [False, True])
def test_geometry_view_reads_only_current_graph_and_preserves_viewport(
    tmp_path, monkeypatch, calculated
):
    repository = SqliteProjectRepository(tmp_path / "viewport.sqlite3")
    source = GeometrySnapshot(feature_collection=collection(marker="исходник", size=16))
    mapped = GeometrySnapshot(feature_collection=collection(marker="карта", size=16))
    project = repository.create(
        Project(
            name="Карта без второго графа",
            source_geometry=source,
            geometry=mapped if calculated else None,
            geometry_version=3,
            site_area_m2=123,
            planning_area_m2=100,
            allowed_area_m2=90,
        )
    )
    full = repository.get(project.id)
    expected = IndexedGeometryQuery().query(full, (-1, -1, 20, 20), 0.1)
    _, references = stored(repository, project)
    read = repository._geometry_store.read
    opened = []

    def selected_only(stream, reference, *args, **kwargs):
        opened.append(reference)
        return read(stream, reference, *args, **kwargs)

    monkeypatch.setattr(repository._geometry_store, "read", selected_only)
    view = repository.get_geometry_view(project.id)
    assert isinstance(view, ProjectGeometryView) and not isinstance(view, Project)
    assert opened == [references["geometry" if calculated else "source_geometry"]]
    assert (view.geometry if calculated else view.source_geometry).model_dump() == (
        full.geometry if calculated else full.source_geometry
    ).model_dump()
    assert view.source_geometry is None if calculated else view.geometry is None
    assert IndexedGeometryQuery().query(view, (-1, -1, 20, 20), 0.1) == expected
    with pytest.raises(FrozenInstanceError):
        view.geometry_version = 999


def test_geometry_view_does_not_read_unselected_source_and_checks_selected_sha(
    tmp_path,
):
    repository = SqliteProjectRepository(tmp_path / "selected.sqlite3")
    project = repository.create(
        Project(
            name="Выбранная карта",
            geometry=GeometrySnapshot(
                feature_collection=collection(marker="карта", size=16)
            ),
            source_geometry=GeometrySnapshot(
                feature_collection=collection(marker="исходник", size=16)
            ),
        )
    )
    _, references = stored(repository, project)
    repository._geometry_store.path(project.id, references["source_geometry"]).unlink()
    assert (
        repository.get_geometry_view(project.id).geometry.feature_collection
        == project.geometry.feature_collection
    )
    with pytest.raises(FileNotFoundError):
        repository.get(project.id)
    path = repository._geometry_store.path(project.id, references["geometry"])
    content = bytearray(path.read_bytes())
    content[-1] ^= 1
    path.write_bytes(content)
    with pytest.raises(ValueError, match="целостность"):
        repository.get_geometry_view(project.id)


def test_geometry_view_keeps_pinned_geometry_and_metadata_from_same_sql_revision(
    tmp_path, monkeypatch
):
    first = SqliteProjectRepository(tmp_path / "pinned-view.sqlite3")
    project = first.create(
        Project(
            name="Первая ревизия",
            geometry=GeometrySnapshot(
                feature_collection=collection(marker="первая", size=16)
            ),
            geometry_version=1,
        )
    )
    second = SqliteProjectRepository(first.path)
    read = first._geometry_store.read
    changed = False

    def concurrent_change(stream, reference, *args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            current = second.get(project.id)
            current.geometry = GeometrySnapshot(
                feature_collection=collection(marker="вторая", size=16)
            )
            current.geometry_version += 1
            second.save(current)
        return read(stream, reference, *args, **kwargs)

    monkeypatch.setattr(first._geometry_store, "read", concurrent_change)
    old_view = first.get_geometry_view(project.id)
    assert old_view.geometry_version == 1
    assert old_view.geometry.feature_collection == project.geometry.feature_collection
    current_view = first.get_geometry_view(project.id)
    assert current_view.geometry_version == 2
    assert current_view.geometry.feature_collection["foreign"]["marker"] == "вторая"


@pytest.mark.parametrize("projection", [False, True])
def test_legacy_inline_geometry_view_preserves_selected_numbers_and_metadata(
    projection,
):
    repository = SqliteProjectRepository(":memory:")
    project, expected = project_fixture(repository)
    if not projection:
        repository._connection.execute(
            "UPDATE projects SET projection=NULL WHERE id=?", (project.id,)
        )
        repository._connection.commit()
    view = repository.get_geometry_view(project.id)
    assert view.geometry.feature_collection == expected
    assert view.source_geometry is None
    assert view.geometry.feature_collection["foreign"]["integer"] == 10**40


@pytest.mark.parametrize("has_source", [False, True])
def test_geometry_view_matches_native_editor_roles_without_false_editor_detection(
    tmp_path, has_source
):
    from app.dxf_import.contracts import ImportMode, ImportStatus, SourceFile
    from app.dxf_import.layers.contracts import Layer, LayerKind
    from app.dxf_import.source.review_contracts import SourceReview
    from app.native_query.live.live_client import LiveSession

    repository = SqliteProjectRepository(tmp_path / "editor-view.sqlite3")
    graph = GeometrySnapshot(feature_collection=collection(size=16))
    session = LiveSession(
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
    )
    project = repository.create(
        Project(
            name="Роли редактора",
            geometry=graph,
            source_geometry=graph if has_source else None,
            import_status=ImportStatus(mode=ImportMode.AUTOCAD_LIVE),
            source_review=SourceReview(),
            source_file=SourceFile(
                name="snapshot.json",
                content_sha256="c" * 64,
                size=1,
                imported_at="2026-10-12",
                dxf_version="AC1032",
                units="m",
                entity_count=10,
                native_session=session,
            ),
            layers=[
                Layer(
                    id="layer-a",
                    source_name="А",
                    object_count=5,
                    suggested_kind=LayerKind.ROAD,
                    mapped_kind=LayerKind.ROAD,
                    mapping_confirmed=True,
                    color="#111111",
                )
            ],
        )
    )
    full = repository.get(project.id)
    view = repository.get_geometry_view(project.id)
    assert view.geometry.model_dump() == full.geometry.model_dump()
    first_props = view.geometry.feature_collection["features"][1]["properties"]
    assert first_props.get("kind") == (None if has_source else "road")
