"""Производная подложка: точность, доверенная регистрация и отказ до выдачи."""

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from hashlib import sha256
from threading import Barrier, Event

import pytest
from test_import_mapping_storage import imported as imported

from app.dxf_import.layers.contracts import LayerKind
from app.dxf_import.source.editor_source import open_source_editor
from app.geometry.contracts import GeometrySnapshot
from app.operations.progress import OperationCancelled, operation_cancellation
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.projects.display_storage import DisplayRegistration, _DisplayBuilder
from app.projects.geometry_storage import geometry_references
from app.projects.workspace_backup import backup_workspace
from app.projects.workspace_restore import restore_workspace_backup
from app.shared.failures import DomainFailure, ProjectNotFound


def make_project(repository, features=None):
    features = (
        features
        if features is not None
        else [
            {
                "type": "Feature",
                "id": "source-route",
                "geometry": {
                    "type": "Point",
                    "coordinates": [1, -0.0, 3.25],
                    "foreign": {"exact": 10**40},
                },
                "properties": {
                    "source_layer": "Слой",
                    "kind": "building",
                    "custom": [None, False, 10**40],
                },
                "foreign": "kept",
            }
        ]
    )
    return repository.create(
        Project(
            name="подложка",
            geometry=GeometrySnapshot(
                feature_collection={"type": "FeatureCollection", "features": features}
            ),
        )
    )


def registration(repository, project):
    row = repository._connection.execute(
        "SELECT identity,sha256,bytes FROM project_display_resources WHERE project_id=?",
        (project.id,),
    ).fetchone()
    return DisplayRegistration(row[0], row[1], row[2]) if row else None


def forbid(*args, **kwargs):
    raise AssertionError("Full collection/build was not needed")


def test_lossless_roundtrip_all_ordinals_and_statuses(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    features = [
        *make_project(repository).geometry.feature_collection["features"],
        {
            "type": "Feature",
            "id": 17,
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[0.0, 0], [2, 0.0], [0, 2], [0.0, 0]]],
                "children": "foreign",
            },
            "properties": {},
        },
        {
            "type": "Feature",
            "geometry": {
                "type": "GeometryCollection",
                "geometries": [
                    {"type": "Point", "coordinates": [4, 5], "bbox": [4, 5, 4, 5]}
                ],
                "children": "foreign",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [2, 0], [0, 2]]]},
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [2**54 + 1, 1]},
        },
        {"type": "Feature", "geometry": {"type": "MultiPolygon", "coordinates": []}},
        {"type": "Feature", "geometry": None, "properties": {"reason": "missing_xref"}},
        {"type": "Feature", "properties": {"reason": "unsupported"}},
        {
            "type": "Feature",
            "geometry": {"type": "Alien", "coordinates": [3, 4]},
            "reason": "unsupported",
        },
        None,
    ]
    project = make_project(repository, features)
    try:
        with repository.open_display_geometry(project.id) as (_, resource):
            assert resource.count == 5
            assert [
                resource.feature(index) for index in range(len(features))
            ] == features
            # Equality alone misses number types and negative zero.
            assert json.dumps(
                [resource.feature(index) for index in range(len(features))],
                sort_keys=True,
            ) == json.dumps(features, sort_keys=True)
            assert list(resource.iter_indexes()) == [0, 1, 2, 3, 4]
            rows = resource.connection.execute(
                "SELECT ordinal,codec,status FROM features ORDER BY ordinal"
            ).fetchall()
            assert tuple(rows[0])[1:] == ("wkb", "renderable")
            assert tuple(rows[3])[1:] == ("raw", "renderable")
            assert tuple(rows[4])[1:] == ("raw", "renderable")
            assert [row[2] for row in rows[5:]] == [
                "empty",
                "null",
                "missing",
                "unsupported",
                "non_feature",
            ]
        record = registration(repository, project)
        path = repository._display_store.path(project.id, record)
        assert sha256(path.read_bytes()).hexdigest() == record.sha256
        assert not list(path.parent.glob("*.pending"))
    finally:
        repository.close()


def test_restart_reuses_resource_without_full_geojson_or_rebuild(tmp_path, monkeypatch):
    path = tmp_path / "projects.sqlite3"
    repository = SqliteProjectRepository(path)
    project = make_project(repository)
    with repository.open_display_geometry(project.id) as (_, reader):
        expected_identity = reader.identity
    original_registration = registration(repository, project)
    repository.close()
    reopened = SqliteProjectRepository(path)
    try:
        monkeypatch.setattr(reopened._display_store, "build", forbid)
        monkeypatch.setattr(reopened._geometry_store, "read", forbid)
        with reopened.open_display_geometry(project.id) as (_, reader):
            assert reader.identity == expected_identity
            assert reader.query_indexes((0, -1, 2, 1)) == [0]
            assert (
                reader.feature(0) == project.geometry.feature_collection["features"][0]
            )
        assert registration(reopened, project) == original_registration
    finally:
        reopened.close()


@pytest.mark.parametrize("geometry", [42, "unsupported", [], True])
def test_non_geometry_values_are_unsupported_not_null(tmp_path, geometry):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    feature = {"geometry": geometry, "reason": "unreadable"}
    project = make_project(repository, [feature])
    try:
        with repository.open_display_geometry(project.id) as (_, reader):
            assert reader.count == 0
            assert reader.feature(0) == feature
            assert (
                reader.connection.execute("SELECT status FROM features").fetchone()[0]
                == "unsupported"
            )
    finally:
        repository.close()


def test_envelope_keeps_all_properties_without_coordinate_decode(tmp_path, monkeypatch):
    from app.projects import display_storage

    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    expected = dict(project.geometry.feature_collection["features"][0])
    del expected["geometry"]
    try:
        with repository.open_display_geometry(project.id) as (_, reader):
            monkeypatch.setattr(display_storage, "from_wkb", forbid)
            monkeypatch.setattr(display_storage, "mapping", forbid)
            assert reader.envelope(0) == expected
            assert json.dumps(reader.envelope(0), sort_keys=True) == json.dumps(
                expected, sort_keys=True
            )
            with pytest.raises(IndexError):
                reader.envelope(1)
    finally:
        repository.close()


def test_float_only_coordinates_skip_integer_restore_and_keep_exact_bits(
    tmp_path, monkeypatch
):
    from app.projects import display_storage

    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    feature = {
        "type": "Feature",
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[0.0, -0.0], [2.25, 0.0], [0.0, 2.5], [0.0, -0.0]]],
            "foreign": {"bbox": [1, 2, 3, 4]},
        },
    }
    project = make_project(repository, [feature])
    monkeypatch.setattr(display_storage, "_restore_numbers", forbid)
    try:
        with repository.open_display_geometry(project.id) as (_, reader):
            assert json.dumps(reader.feature(0), sort_keys=True) == json.dumps(
                feature, sort_keys=True
            )
            row = reader.connection.execute(
                "SELECT codec,integer_mask FROM features"
            ).fetchone()
            assert tuple(row) == ("wkb", b"")
    finally:
        repository.close()


def test_rtree_expansion_does_not_change_exact_double_bounds(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(
        repository,
        [
            {"geometry": {"type": "Point", "coordinates": [16777217.0, 0]}, "id": 1},
            {"geometry": {"type": "Point", "coordinates": [16777216.0, 0]}, "id": 2},
        ],
    )
    try:
        with repository.open_display_geometry(project.id) as (_, reader):
            assert reader.query_indexes((16777216.1, -1, 16777216.2, 1)) == []
            assert reader.query_indexes((16777217, 0, 16777217, 0)) == [0]
            assert list(reader.iter_metadata([1, 0])) == [
                (1, {"id": 2, "properties": {}}),
                (0, {"id": 1, "properties": {}}),
            ]
            assert [index for index, _ in reader.iter_geometries([1, 0])] == [1, 0]
    finally:
        repository.close()


@pytest.mark.parametrize("coordinate", [1e100, -1e100, 1e-50, -1e-50, 16777217.0])
def test_rtree_extreme_float32_bounds_are_conservative(tmp_path, coordinate):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(
        repository,
        [{"geometry": {"type": "Point", "coordinates": [coordinate, coordinate]}}],
    )
    try:
        with repository.open_display_geometry(project.id) as (_, reader):
            assert reader.query_indexes(
                (coordinate, coordinate, coordinate, coordinate)
            ) == [0]
    finally:
        repository.close()


@pytest.mark.parametrize("fault", ["missing", "bytes", "rtree"])
def test_bad_derived_resource_rebuilds_from_verified_source(tmp_path, fault):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    try:
        with repository.open_display_geometry(project.id):
            pass
        record = registration(repository, project)
        path = repository._display_store.path(project.id, record)
        if fault == "missing":
            path.unlink()
        elif fault == "bytes":
            with path.open("r+b") as stream:
                stream.write(b"broken")
        else:
            with closing(sqlite3.connect(path)) as connection:
                with connection:
                    connection.execute("DELETE FROM bounds")
        with repository.open_display_geometry(project.id) as (_, reader):
            assert reader.query_indexes((0, -1, 2, 1)) == [0]
        record = registration(repository, project)
        assert sha256(path.read_bytes()).hexdigest() == record.sha256
    finally:
        repository.close()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_unregistered_sqlite_sidecar_is_rejected_and_rebuilt(
    tmp_path, monkeypatch, suffix
):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    try:
        with repository.open_display_geometry(project.id):
            pass
        path = repository._display_store.path(
            project.id, registration(repository, project)
        )
        sidecar = path.with_name(path.name + suffix)
        sidecar.write_bytes(b"unregistered bytes")
        original = repository._display_store.build
        builds = []

        def observed(*args):
            builds.append(True)
            return original(*args)

        monkeypatch.setattr(repository._display_store, "build", observed)
        with repository.open_display_geometry(project.id) as (_, reader):
            assert (
                reader.feature(0) == project.geometry.feature_collection["features"][0]
            )
        assert builds == [True]
        assert not sidecar.exists()
    finally:
        repository.close()


def test_sidecar_added_during_read_never_leaves_successful_context(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    try:
        with pytest.raises(ValueError, match="журнал"):
            with repository.open_display_geometry(project.id) as (_, reader):
                path = repository._display_store.path(
                    project.id, registration(repository, project)
                )
                path.with_name(path.name + "-wal").write_bytes(b"unregistered bytes")
                assert reader.count == 1
    finally:
        repository.close()


def test_source_corruption_never_uses_valid_derived_cache(tmp_path, monkeypatch):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    try:
        with repository.open_display_geometry(project.id):
            pass
        row = repository._connection.execute(
            "SELECT payload FROM projects WHERE id=?", (project.id,)
        ).fetchone()
        reference = geometry_references(json.loads(row[0]))["geometry"]
        path = repository._geometry_store.path(project.id, reference)
        with path.open("r+b") as stream:
            stream.seek(-1, 2)
            previous = stream.read(1)
            stream.seek(-1, 2)
            stream.write(bytes([previous[0] ^ 1]))
        monkeypatch.setattr(repository._display_store, "build", forbid)
        with pytest.raises(ValueError, match="целостность"):
            with repository.open_display_geometry(project.id):
                pytest.fail("Invalid source must not yield a resource")
    finally:
        repository.close()


@pytest.mark.parametrize("phase", ["consume", "published"])
def test_cancellation_does_not_register_or_leave_own_files(
    tmp_path, monkeypatch, phase
):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    cancelled = Event()
    if phase == "consume":
        original = _DisplayBuilder.accept

        def cancel(self, feature):
            original(self, feature)
            cancelled.set()

        monkeypatch.setattr(_DisplayBuilder, "accept", cancel)
    else:
        original = repository._display_store.publish

        def cancel(*args):
            result = original(*args)
            cancelled.set()
            return result

        monkeypatch.setattr(repository._display_store, "publish", cancel)
    try:

        def check():
            if cancelled.is_set():
                raise OperationCancelled()

        with operation_cancellation(check):
            with pytest.raises(OperationCancelled):
                with repository.open_display_geometry(project.id):
                    pytest.fail("Cancelled builder must not publish")
        assert registration(repository, project) is None
        assert not list(repository._display_store.root.rglob("*.pending"))
        assert not list(repository._display_store.root.rglob("*.sqlite3"))
    finally:
        repository.close()


def test_source_revision_change_during_build_is_rejected(tmp_path, monkeypatch):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    original = _DisplayBuilder.finish

    def change(self):
        result = original(self)
        changed = repository.get(project.id)
        changed.geometry_version += 1
        repository.save(changed)
        return result

    monkeypatch.setattr(_DisplayBuilder, "finish", change)
    try:
        with pytest.raises(DomainFailure):
            with repository.open_display_geometry(project.id):
                pytest.fail("Changed source must not publish")
        assert registration(repository, project) is None
        assert not list(repository._display_store.root.rglob("*.pending"))
        assert not list(repository._display_store.root.rglob("*.sqlite3"))
    finally:
        repository.close()


def test_replacement_during_open_is_not_returned(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    try:
        expected = (
            nullcontext()
            if os.name == "nt"
            else pytest.raises(ValueError, match="заменено")
        )
        with expected:
            with repository.open_display_geometry(project.id) as (_, reader):
                assert reader.count == 1
                path = repository._display_store.path(
                    project.id, registration(repository, project)
                )
                replacement = path.with_suffix(".replacement")
                replacement.write_bytes(path.read_bytes())
                if os.name == "nt":
                    with pytest.raises(PermissionError) as denied:
                        replacement.replace(path)
                    assert denied.value.winerror in {5, 32}
                    assert (
                        reader.feature(0)
                        == project.geometry.feature_collection["features"][0]
                    )
                else:
                    replacement.replace(path)
    finally:
        repository.close()


def test_source_path_replacement_during_read_is_rejected(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    try:
        expected = (
            nullcontext()
            if os.name == "nt"
            else pytest.raises(ValueError, match="Сохранённая геометрия заменена")
        )
        with expected:
            with repository.open_display_geometry(project.id) as (_, reader):
                row = repository._connection.execute(
                    "SELECT payload FROM projects WHERE id=?", (project.id,)
                ).fetchone()
                reference = geometry_references(json.loads(row[0]))["geometry"]
                path = repository._geometry_store.path(project.id, reference)
                replacement = path.with_suffix(".replacement")
                replacement.write_bytes(path.read_bytes())
                if os.name == "nt":
                    with pytest.raises(PermissionError) as denied:
                        replacement.replace(path)
                    assert denied.value.winerror in {5, 32}
                    assert path.read_bytes() == replacement.read_bytes()
                else:
                    replacement.replace(path)
                assert reader.feature(0)
    finally:
        repository.close()


def test_backup_restore_discards_absent_derived_file_and_rebuilds(tmp_path):
    path = tmp_path / "projects.sqlite3"
    repository = SqliteProjectRepository(path)
    project = make_project(repository)
    with repository.open_display_geometry(project.id):
        pass
    original = registration(repository, project)
    backup = backup_workspace(path, reason="display-roundtrip")
    restored = restore_workspace_backup(backup, tmp_path / "restored")
    repository.close()
    reopened = SqliteProjectRepository(restored)
    try:
        assert registration(reopened, project) == original
        assert not reopened._display_store.path(project.id, original).exists()
        with reopened.open_display_geometry(project.id) as (_, reader):
            assert (
                reader.feature(0) == project.geometry.feature_collection["features"][0]
            )
        reopened.delete(project.id)
        assert registration(reopened, project) is None
        assert not list(reopened._display_store.root.rglob("*.sqlite3"))
        with pytest.raises(ProjectNotFound):
            with reopened.open_display_geometry(project.id):
                pass
    finally:
        reopened.close()


def test_plan_change_reuses_resource_and_geometry_change_retains_pinned_old(
    tmp_path, monkeypatch
):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    try:
        with repository.open_display_geometry(project.id):
            pass
        original = registration(repository, project)
        oldpath = repository._display_store.path(project.id, original)
        changed = repository.get(project.id)
        changed.name = "другое имя"
        repository.save(changed)
        with monkeypatch.context() as scoped:
            scoped.setattr(repository._display_store, "build", forbid)
            with repository.open_display_geometry(project.id):
                pass
        assert registration(repository, project) == original
        changed = repository.get(project.id)
        changed.geometry_version += 1
        repository.save(changed)
        with monkeypatch.context() as scoped:
            scoped.setattr(repository._display_store, "build", forbid)
            with repository.open_display_geometry(project.id):
                pass
        assert registration(repository, project) == original
        changed = repository.get(project.id)
        changed.geometry = changed.geometry.model_copy(deep=True)
        changed.geometry.feature_collection["features"][0]["geometry"]["coordinates"][
            0
        ] = 9
        changed.geometry_version += 1
        repository.save(changed)
        with repository.open_display_geometry(project.id) as (_, reader):
            assert reader.query_indexes((8, -1, 10, 1)) == [0]
        assert registration(repository, project).identity != original.identity
        assert oldpath.exists()
        assert len(list(oldpath.parent.glob("*.sqlite3"))) == 2
    finally:
        repository.close()


def test_role_revision_reuses_same_binary_and_projects_current_kind(
    imported, monkeypatch
):
    repository, project = imported
    project = repository.save(open_source_editor(project))
    with repository.open_display_geometry(project.id) as (_, reader):
        original_feature = reader.feature(1)
    original = registration(repository, project)
    compact = repository.get(project.id, lightweight=True)
    layer = next(layer for layer in compact.layers if layer.source_name == "ROAD")
    layer.mapped_kind = LayerKind.ROAD
    layer.mapping_confirmed = True
    compact.geometry_version += 1
    repository.save_import_mappings(compact, source_editor=True)
    monkeypatch.setattr(repository._display_store, "build", forbid)
    with repository.open_display_geometry(project.id) as (view, reader):
        assert view.geometry_version > project.geometry_version
        updated = reader.feature(1)
        assert updated["properties"]["kind"] == "road"
        assert original_feature["properties"].get("kind") != "road"
        assert reader.metadata(1)["properties"]["kind"] == "road"
        assert reader.envelope(1)["properties"]["kind"] == "road"
    assert registration(repository, project) == original


def test_role_revision_during_context_is_rejected_even_same_binary(tmp_path):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    try:
        with pytest.raises(DomainFailure):
            with repository.open_display_geometry(project.id) as (_, reader):
                original = registration(repository, project)
                changed = repository.get(project.id)
                changed.geometry_version += 1
                repository.save(changed)
                assert registration(repository, project) == original
                assert reader.feature(0)
    finally:
        repository.close()


def test_two_builders_and_readers_publish_one_registered_resource(
    tmp_path, monkeypatch
):
    path = tmp_path / "projects.sqlite3"
    repository = SqliteProjectRepository(path)
    project = make_project(repository)
    second = SqliteProjectRepository(path)
    original = _DisplayBuilder.finish
    barrier = Barrier(2)

    def synchronized(self):
        result = original(self)
        barrier.wait(timeout=5)
        return result

    monkeypatch.setattr(_DisplayBuilder, "finish", synchronized)

    def read(current):
        with current.open_display_geometry(project.id) as (_, reader):
            return reader.identity, reader.feature(0)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(read, (repository, second)))
        assert results[0] == results[1]
        assert registration(repository, project) == registration(second, project)
        assert len(list(repository._display_store.root.rglob("*.sqlite3"))) == 1
        assert not list(repository._display_store.root.rglob("*.pending"))
    finally:
        repository.close()
        second.close()


def test_failed_builder_initialization_cleans_pending(tmp_path, monkeypatch):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)

    def unavailable(self, identity):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(_DisplayBuilder, "_initialize", unavailable)
    try:
        with pytest.raises(sqlite3.OperationalError, match="disk is full"):
            with repository.open_display_geometry(project.id):
                pass
        assert registration(repository, project) is None
        assert not list(repository._display_store.root.rglob("*.pending"))
    finally:
        repository.close()


def test_incomplete_rtree_builder_is_rejected_before_registration(
    tmp_path, monkeypatch
):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    original = _DisplayBuilder.accept

    def incomplete(self, feature):
        original(self, feature)
        self.connection.execute("DELETE FROM bounds")

    monkeypatch.setattr(_DisplayBuilder, "accept", incomplete)
    try:
        with pytest.raises(ValueError, match="Неполный пространственный"):
            with repository.open_display_geometry(project.id):
                pass
        assert registration(repository, project) is None
        assert not list(repository._display_store.root.rglob("*.pending"))
    finally:
        repository.close()


@pytest.mark.parametrize("explicit_wait_check", [False, True])
def test_waiter_can_cancel_before_build_owner_releases(tmp_path, explicit_wait_check):
    repository = SqliteProjectRepository(tmp_path / "projects.sqlite3")
    project = make_project(repository)
    waiting, cancelled = Event(), Event()

    def check():
        waiting.set()
        if cancelled.is_set():
            raise OperationCancelled()

    def read():
        if explicit_wait_check:
            with repository.open_display_geometry(project.id, wait_check=check):
                pytest.fail("Cancelled waiter must not acquire owner lock")
        else:
            with operation_cancellation(check):
                with repository.open_display_geometry(project.id):
                    pytest.fail("Cancelled waiter must not acquire owner lock")

    try:
        with repository._display_build_lock:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(read)
                assert waiting.wait(timeout=5)
                cancelled.set()
                with pytest.raises(OperationCancelled):
                    future.result(timeout=5)
                assert registration(repository, project) is None
        assert not list(repository._display_store.root.rglob("*.pending"))
    finally:
        repository.close()
