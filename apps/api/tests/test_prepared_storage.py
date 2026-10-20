"""Расчётные окна из бинарного ресурса с неизменным исходным SHA-снимком."""

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, nullcontext
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
from math import inf, nextafter
from pathlib import Path

import pytest
from pydantic import ValidationError
from shapely.geometry import Point
from test_hybrid_search import populate
from test_native_live_provider import setup as setup

from app.dxf_import.layers.contracts import LayerKind
from app.native_query.live.live_inventory import QueryObject
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.policy.source_participation import (
    SourceParticipation,
    SourceUse,
    SourceUseProof,
)
from app.native_query.preparation.face_contracts import NativeFace
from app.native_query.preparation.preparation_identity import digest
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    PreparedObject,
    PreparedProjection,
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.preparation.prepared_storage import (
    PreparedSqliteStore,
    _Builder,
)
from app.native_query.search.bounds_index import NativeBoundsIndex
from app.operations.progress import OperationCancelled, operation_cancellation
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.shared.failures import ProjectNotFound


@pytest.fixture
def saved(setup, tmp_path):
    live, _, project = setup
    populate(project)
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path / "prepared")
    store.save(snapshot)
    path = store.directory / f"{snapshot.key}.json"
    return snapshot, store, path


def rewrite(saved, records=None, **updates):
    snapshot, store, path = saved
    if records is not None:
        updates["records"] = tuple(records)
    changed = snapshot.model_copy(update=updates)
    store.files.save(changed.key, changed.model_dump(mode="json"))
    return changed, store, path


def resource(saved):
    snapshot, store, path = saved
    return PreparedSqliteStore(store.directory).open(path, snapshot.key)


def test_binary_verified_identity_is_accepted_by_the_source_store(saved):
    """Binary migration and source admission must share the platform identity."""
    snapshot, store, _ = saved
    actual = resource(saved)
    store.accept_verified(
        snapshot.key,
        actual.source_fingerprint,
        source_sha256=actual.registration.source_sha256,
    )
    assert store.unchanged(snapshot.key)
    assert store.verified_fingerprint(snapshot.key) == actual.source_fingerprint
    actual.assert_unchanged()


def test_bound_prepared_and_binary_resources_are_deleted_and_late_writers_refused(
    saved, tmp_path
):
    snapshot, _, _ = saved
    with closing(SqliteProjectRepository(tmp_path / "workspace.sqlite3")) as repository:
        project = repository.create(
            Project(id=snapshot.geometry_basis["project_id"], name="synthetic owner")
        )
        directory = Path(repository.path + ".prepared-geometry")
        store = PreparedStore(
            directory,
            database_path=Path(repository.path),
            resource_lifecycle=repository.resource_lifecycle,
        )
        store.save(snapshot)
        path = directory / f"{snapshot.key}.json"
        binary = PreparedSqliteStore(
            directory,
            database_path=Path(repository.path),
            resource_lifecycle=repository.resource_lifecycle,
        )
        value = binary.open(path, snapshot.key, project_id=project.id)
        assert value.count == len(snapshot.records)
        data = binary.root / f"{value.registration.sha256}.sqlite3"
        assert data.is_file()
        repository.delete(project.id)
        assert not path.exists() and not data.exists()
        assert (
            repository._connection.execute(
                "SELECT count(*) FROM prepared_binary_resources"
            ).fetchone()[0]
            == 0
        )
        with pytest.raises(ProjectNotFound):
            store.save(snapshot)
        with pytest.raises(ProjectNotFound):
            binary.open(path, snapshot.key, project_id=project.id)
        assert not path.exists() and not data.exists()


def extra(route, bounds, *, layer="Дополнительный", reason="", geometries=None):
    return PreparedObject(
        item=QueryObject(
            routes=(route,), layer=layer, bounds=bounds, curve=False, error=""
        ),
        geometries=(Point(200, 200).__geo_interface__,)
        if geometries is None
        else geometries,
        reason=reason,
        detail="Адресное замечание" if reason else "",
    )


def test_binary_resource_preserves_metadata_projection_order_and_source(saved):
    snapshot, _, path = saved
    before = path.read_bytes()
    actual = resource(saved)
    assert actual.header.records == actual.header.faces == ()
    assert actual.count == len(snapshot.records)
    assert [row.item for row in actual.records] == [
        row.item for row in snapshot.records
    ]
    assert all(row.geometries == () for row in actual.records)
    expected = PreparedProjection(snapshot)
    for record in snapshot.records:
        first = actual.get(record.item)
        assert actual.get(record.item) is first
        target = expected.get(record.item)
        assert (first.reason, first.detail, first.tolerance_m) == (
            target.reason,
            target.detail,
            target.tolerance_m,
        )
        assert [geometry.wkb for geometry in first.geometries] == [
            geometry.wkb for geometry in target.geometries
        ]
        assert actual.record(record.item).item == record.item
    assert actual.model_dump(mode="json") == snapshot.model_dump(mode="json")
    assert path.read_bytes() == before
    assert actual.records[-1] == actual.records[len(actual.records) - 1]
    assert actual.records[1:] == tuple(actual.records)[1:]
    with pytest.raises(IndexError):
        _ = actual.records[len(actual.records)]


def test_streamed_coverage_projection_matches_verified_individual_lookup(saved):
    snapshot, _, _ = saved
    actual = resource(saved)
    rows = list(actual.iter_coverage_records())
    assert [record.item for record, _, _ in rows] == [
        record.item for record in snapshot.records
    ]
    for record, encoded, marker in rows:
        if marker == -1:
            with pytest.raises(ValueError, match="Справочный объект"):
                actual.coverage_projection(record, encoded, marker)
            continue
        projection = actual.coverage_projection(record, encoded, marker)
        individual = actual.get(record.item)
        assert (
            projection.reason,
            projection.detail,
            projection.tolerance_m,
            [geometry.wkb for geometry in projection.geometries],
        ) == (
            individual.reason,
            individual.detail,
            individual.tolerance_m,
            [geometry.wkb for geometry in individual.geometries],
        )


def test_coverage_consumer_error_closes_stream_with_retained_traceback(
    saved, setup, monkeypatch
):
    actual = resource(saved)
    project = setup[2]
    engine = PreparedGeometryEngine(actual, project)
    connections = []
    connect = sqlite3.connect

    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connections.append(connection)
        return connection

    def reject_record(_record):
        raise RuntimeError("coverage consumer failed")

    monkeypatch.setattr(sqlite3, "connect", tracked)
    monkeypatch.setattr(engine, "_materialize_record", reject_record)
    with pytest.raises(RuntimeError, match="coverage consumer failed") as error:
        engine.source_coverage(project)
    assert error.value.__traceback__ is not None
    assert connections
    assert getattr(actual._sessions, "active", None) is None
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")


def test_confirmed_reference_keeps_ledger_without_physical_projection(setup, tmp_path):
    live, _, project = setup
    populate(project)
    project.layers[2].mapped_kind = LayerKind.IGNORE
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path / "reference")
    store.save(snapshot)
    actual = resource((snapshot, store, store.directory / f"{snapshot.key}.json"))
    ignored = next(row for row in actual.records if row.item.routes == ("CC",))
    assert ignored.geometries == () and ignored.reason == ignored.detail == ""
    assert ignored.participation == snapshot.records[2].participation
    with pytest.raises(ValueError, match="Справочный объект"):
        actual.get(ignored.item)


def test_reopened_resource_and_spatial_query_never_load_full_snapshot(
    saved, monkeypatch
):
    first = resource(saved)
    expected = [record.item for record in first.candidates((19, -1, 26, 1))]
    monkeypatch.setattr(
        PreparedStore,
        "load",
        lambda *args, **kwargs: pytest.fail("Полное чтение снимка"),
    )
    monkeypatch.setattr(
        _Builder, "consume", lambda *args, **kwargs: pytest.fail("Повторная миграция")
    )
    again = resource(saved)
    assert [record.item for record in again.candidates((19, -1, 26, 1))] == expected
    assert again.registration == first.registration
    for record in again.candidates((19, -1, 26, 1)):
        again.get(record.item)


def test_streamed_metadata_projection_and_proof_share_one_pinned_connection(
    saved, monkeypatch
):
    actual = resource(saved)
    connect = sqlite3.connect
    opened = []

    def tracked(*args, **kwargs):
        opened.append(args[0])
        return connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", tracked)
    for record in actual.iter_records():
        assert actual.record(record.item).item == record.item
        actual.get(record.item)
    assert len(opened) == 1
    assert getattr(actual._sessions, "active", None) is None


def test_read_scope_reuses_one_connection_for_independent_and_nested_reads(
    saved, monkeypatch
):
    actual = resource(saved)
    connect = sqlite3.connect
    opened = []

    def tracked(*args, **kwargs):
        connection = connect(*args, **kwargs)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked)
    with actual.read_scope():
        for expected in saved[0].records:
            with actual.read_scope():
                assert actual.record(expected.item).item == expected.item
                actual.get(expected.item)
        assert len(opened) == 1
    assert getattr(actual._sessions, "active", None) is None
    with pytest.raises(sqlite3.ProgrammingError):
        opened[0].execute("SELECT 1")


def test_cancelled_read_scope_releases_resources_before_the_next_query(saved):
    actual = resource(saved)

    def cancel():
        raise OperationCancelled("test")

    with pytest.raises(OperationCancelled), actual.read_scope():
        with operation_cancellation(cancel):
            actual.get(saved[0].records[0].item)
    assert getattr(actual._sessions, "active", None) is None
    assert actual.record(saved[0].records[0].item).item == saved[0].records[0].item


@pytest.mark.parametrize("file", ["source", "binary"])
def test_read_scope_rejects_replaced_paths_or_windows_prevents_replacement(saved, file):
    actual = resource(saved)
    path = actual.source if file == "source" else actual.path
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    replaced = False
    try:
        with actual.read_scope():
            try:
                replacement.replace(path)
            except PermissionError:
                # Windows держит проверенный файл без разрешения на удаление.
                assert os.name == "nt"
            else:
                replaced = True
    except PreparedGeometryUnavailable:
        assert replaced
    else:
        assert not replaced
        actual.assert_unchanged()


def test_path_replacement_during_stream_refuses_result_at_pinned_session_end(saved):
    actual = resource(saved)
    expected = (
        nullcontext() if os.name == "nt" else pytest.raises(PreparedGeometryUnavailable)
    )
    with expected:
        for index, record in enumerate(actual.iter_records()):
            if index == 0:
                replacement = actual.path.with_suffix(".replacement")
                replacement.write_bytes(actual.path.read_bytes())
                if os.name == "nt":
                    with pytest.raises(PermissionError) as denied:
                        replacement.replace(actual.path)
                    assert denied.value.winerror in {5, 32}
                    assert actual.path.read_bytes() == replacement.read_bytes()
                else:
                    replacement.replace(actual.path)
            actual.record(record.item)


def test_candidates_preserve_unknown_bounds_always_layers_and_source_order(saved):
    snapshot = saved[0]
    rows = (
        extra("D1", (200, 200, 200, 200)),
        snapshot.records[2],
        extra("D2", None, reason="unreadable", geometries=()),
        snapshot.records[0],
        snapshot.records[1],
    )
    actual = resource(rewrite(saved, rows))
    assert [record.item.routes for record in actual.candidates((19, -1, 26, 1))] == [
        ("CC",),
        ("D2",),
        ("AA",),
    ]
    assert [
        record.item.routes
        for record in actual.candidates((500, 500, 501, 501), always_layers={"Граница"})
    ] == [("D2",), ("AA",)]
    assert [
        record.item.routes
        for record in actual.candidates(
            (500, 500, 501, 501), always_keys={(("D1",), 0)}
        )
    ] == [("D1",), ("D2",)]
    with actual._connection() as connection:
        plan = connection.execute(
            "EXPLAIN QUERY PLAN SELECT ordinal FROM records WHERE minx IS NULL"
        ).fetchall()
    assert any("unknown_bounds" in row[-1] for row in plan)


@pytest.mark.parametrize(
    "coordinate", [1.00000001, -1.00000001, 1e-45, -1e-45, 1e200, -1e200]
)
def test_rtree_selection_remains_conservative_for_native_coordinates(saved, coordinate):
    row = extra(
        "D1",
        (coordinate, coordinate, coordinate, coordinate),
        geometries=(Point(coordinate, coordinate).__geo_interface__,),
    )
    actual = resource(rewrite(saved, (row,)))
    selected = tuple(
        actual.candidates((coordinate, coordinate, coordinate, coordinate))
    )
    expected = tuple(
        NativeBoundsIndex((row.item,), {}).candidates([(coordinate, coordinate, 0)], 0)
    )
    assert tuple(record.item for record in selected) == expected == (row.item,)


def test_rtree_float32_false_positive_does_not_change_exact_candidate_set(saved):
    coordinate = 1.00000001
    row = extra(
        "D1",
        (coordinate, coordinate, coordinate, coordinate),
        geometries=(Point(coordinate, coordinate).__geo_interface__,),
    )
    actual = resource(rewrite(saved, (row,)))
    outside = nextafter(coordinate, inf)
    window = (outside, outside, outside, outside)
    assert tuple(actual.candidates(window)) == ()
    assert (
        tuple(NativeBoundsIndex((row.item,), {}).candidates([(outside, outside, 0)], 0))
        == ()
    )
    assert [
        record.item
        for record in actual.candidates(window, always_layers={row.item.layer})
    ] == [row.item]
    assert [
        record.item
        for record in actual.candidates(
            window, always_keys={(row.item.routes, row.item.face_id)}
        )
    ] == [row.item]


@pytest.mark.parametrize("sidecar", ["-wal", "-shm", "-journal"])
def test_open_resource_rejects_unregistered_sqlite_sidecars(saved, sidecar):
    actual = resource(saved)
    Path(str(actual.path) + sidecar).write_bytes(b"unregistered")
    with pytest.raises((ValueError, PreparedGeometryUnavailable)):
        actual.get(saved[0].records[0].item)


@pytest.mark.parametrize("target", ["source", "binary"])
def test_cached_projection_cannot_mask_mutated_resources(saved, target):
    actual = resource(saved)
    item = saved[0].records[0].item
    actual.get(item)
    path = actual.source if target == "source" else actual.path
    before = path.stat()
    with path.open("r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"x")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1))
    with pytest.raises(PreparedGeometryUnavailable):
        actual.get(item)


@pytest.mark.parametrize("target", ["source", "binary"])
def test_streamed_coverage_rejects_mutated_resources(saved, target):
    actual = resource(saved)
    path = actual.source if target == "source" else actual.path
    before = path.stat()
    with path.open("r+b") as stream:
        stream.seek(-1, 2)
        stream.write(b"x")
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1))
    with pytest.raises(PreparedGeometryUnavailable):
        list(actual.iter_coverage_records())


def test_derivative_corruption_rebuilds_only_from_original_source(saved):
    actual = resource(saved)
    source = actual.source.read_bytes()
    actual.path.write_bytes(b"damaged")
    again = resource(saved)
    assert again.registration == actual.registration
    assert again.source.read_bytes() == source
    assert sha256(again.path.read_bytes()).hexdigest() == again.registration.sha256
    assert again.get(saved[0].records[0].item).geometries


def test_independent_concurrent_builders_publish_one_trusted_resource(saved):
    original = saved[2].read_bytes()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(resource, saved) for _ in range(2)]
        first, second = (future.result() for future in futures)
    assert first.registration == second.registration
    first.assert_unchanged()
    second.assert_unchanged()
    assert first.path == second.path
    assert saved[2].read_bytes() == original
    assert not list(first.path.parent.glob("*.pending"))
    assert resource(saved).registration == first.registration


def test_failed_registration_does_not_publish_authority_or_change_original(
    saved, monkeypatch
):
    from app.native_query.preparation import prepared_registry

    original = saved[2].read_bytes()
    save = prepared_registry.save_prepared_registration

    def fail(*args, **kwargs):
        raise OSError("registry unavailable")

    monkeypatch.setattr(prepared_registry, "save_prepared_registration", fail)
    with pytest.raises(OSError, match="registry unavailable"):
        resource(saved)
    directory = saved[1].directory / "binary"
    assert saved[2].read_bytes() == original
    assert not list(directory.glob("*.pending"))
    assert not (directory / f"{saved[0].key}.json").exists()
    source_sha256 = sha256(original).hexdigest()
    assert (
        prepared_registry.read_prepared_registration(
            directory / "catalog.sqlite3", saved[0].key, source_sha256
        )
        is None
    )
    monkeypatch.setattr(prepared_registry, "save_prepared_registration", save)
    repaired = resource(saved)
    assert (
        prepared_registry.read_prepared_registration(
            directory / "catalog.sqlite3", saved[0].key, source_sha256
        )
        == repaired.registration.__dict__
    )


def test_corrupt_source_is_never_repaired_from_derivative(saved):
    actual = resource(saved)
    actual.source.write_text('{"payload":"{}","sha256":"' + "0" * 64 + '"}')
    damaged = actual.source.read_bytes()
    with pytest.raises(ValueError):
        resource(saved)
    assert actual.source.read_bytes() == damaged


@pytest.mark.parametrize(
    "mode", ["duplicate", "empty", "invalid", "unsupported", "missing"]
)
def test_invalid_prepared_records_never_publish_derivative(saved, mode):
    row = saved[0].records[0]
    rows = (row, row)
    if mode != "duplicate":
        geometry = {
            "empty": {"type": "Point", "coordinates": []},
            "invalid": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 1], [0, 1], [1, 0], [0, 0]]],
            },
            "unsupported": {"type": "GeometryCollection", "geometries": []},
        }.get(mode)
        rows = (replace(row, geometries=() if geometry is None else (geometry,)),)
    changed = rewrite(saved, rows)
    with pytest.raises((ValueError, TypeError)):
        resource(changed)
    directory = saved[1].directory / "binary"
    assert not list(directory.glob("*.pending"))
    assert not list(directory.glob("*.sqlite3"))
    assert not list(directory.glob(f"{saved[0].key}.json"))


def test_stale_source_participation_is_rejected_before_publish(saved):
    row = saved[0].records[0]
    proof = row.participation.model_copy(update={"prepared_key": "0" * 64})
    changed = rewrite(saved, (replace(row, participation=proof),))
    with pytest.raises(ValueError, match="Участие"):
        resource(changed)


@pytest.mark.parametrize(
    "mode",
    [
        "topology",
        "boundary",
        "missing_face",
        "excluded",
        "missing_recipe",
        "boundary_error",
    ],
)
def test_topology_and_boundary_evidence_is_validated_against_recipe_and_native_faces(
    saved, mode
):
    snapshot, store, _ = saved
    basis = deepcopy(snapshot.geometry_basis)
    recipe = {
        "source_sha256": snapshot.capture.snapshot_sha256,
        "companion_layers": ["Дорога"],
        "excluded_routes": ["CC"] if mode == "excluded" else [],
    }
    if mode != "missing_recipe":
        basis["topology_recipe"] = recipe
    key = digest(basis)
    rows = []
    for record in snapshot.records:
        proof = record.participation.model_copy(update={"prepared_key": key})
        if record.item.routes == ("CC",):
            boundary = mode in {"boundary", "boundary_error"}
            proof = SourceParticipation(
                use=SourceUse.CONTEXT if boundary else SourceUse.TOPOLOGY,
                proof=SourceUseProof.PREPARED_BOUNDARY
                if boundary
                else SourceUseProof.PREPARED_TOPOLOGY,
                source_sha256=snapshot.capture.snapshot_sha256,
                prepared_key=key,
                source_layer="Дорога",
            )
            if mode == "boundary_error":
                record = replace(record, reason="Адресная ошибка")
        rows.append(replace(record, participation=proof))
    face = NativeFace(
        id=1,
        anchor="AA",
        layer="Граница",
        routes=("AA", "CC"),
        physical_routes=("AA",),
        area=4,
        bounds=(-1, -1, 1, 1),
        display=((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, -1, 0)),
    )
    changed = snapshot.model_copy(
        update={
            "key": key,
            "geometry_basis": basis,
            "records": tuple(rows),
            "faces": () if mode == "missing_face" else (face,),
        }
    )
    store.files.save(key, changed.model_dump(mode="json"))
    data = changed, store, store.directory / f"{key}.json"
    if mode in {"topology", "boundary"}:
        actual = resource(data)
        assert actual.record(rows[2].item).participation == rows[2].participation
        assert tuple(actual.faces) == (face,)
    else:
        with pytest.raises(ValueError):
            resource(data)


def test_face_display_is_binary_and_decoded_lazily_without_full_snapshot(saved):
    face = NativeFace(
        id=1,
        anchor="AA",
        layer="Граница",
        routes=("AA",),
        physical_routes=("AA",),
        area=4,
        bounds=(-1, -1, 1, 1),
        display=((-1, -1, -0.0), (1, -1, 0), (1, 1, 0), (-1, -1, -0.0)),
    )
    actual = resource(rewrite(saved, faces=(face,)))
    assert actual.header.faces == () and len(actual.faces) == 1
    assert tuple(actual.faces) == (face,)
    assert actual.faces[-1] == face and actual.faces[:] == (face,)
    with pytest.raises(IndexError):
        _ = actual.faces[1]


def test_face_coverage_routes_do_not_decode_display_coordinates(saved, monkeypatch):
    face = NativeFace(
        id=1,
        anchor="AA",
        layer="Граница",
        routes=("AA",),
        physical_routes=("AA",),
        area=4,
        bounds=(-1, -1, 1, 1),
        display=((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, -1, 0)),
    )
    actual = resource(rewrite(saved, faces=(face,)))
    monkeypatch.setattr(
        "app.native_query.preparation.prepared_storage.from_wkb",
        lambda *args, **kwargs: pytest.fail("Контуры для покрытия не нужны"),
    )
    assert tuple(actual.iter_face_routes()) == ((1, ("AA",)),)


def test_repaired_face_restores_internal_alias_without_weakening_native_contract(saved):
    snapshot, store, path = saved
    face = NativeFace(
        id=1,
        anchor="AA",
        layer="Граница",
        routes=("AA", "BB"),
        physical_routes=("AA", "BB"),
        area=4,
        bounds=(-1, -1, 1, 1),
        display=((-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, -1, 0)),
        repairs=({"from": "AA", "to": "BB", "a": (-1, -1, -0.0), "b": (1, -1, 0)},),
    )
    changed = snapshot.model_copy(update={"faces": (face,)})
    store.files.save(changed.key, changed.model_dump(mode="json", by_alias=True))
    actual = resource((changed, store, path))
    decoded = actual.faces[0]
    assert decoded == face and decoded.repairs == face.repairs
    assert decoded.model_dump_json(by_alias=True) == face.model_dump_json(by_alias=True)
    assert tuple(actual.faces) == (face,)
    # The alias adaptation belongs to trusted SQLite metadata, not the DTO
    # receiving native evidence. Public JSON still rejects internal field names.
    with pytest.raises(ValidationError):
        NativeFace.model_validate(face.model_dump(mode="json"))


def test_cancelled_migration_has_no_registration_or_partial_binary(saved, monkeypatch):
    original = _Builder.record
    processed = 0

    def record(builder, value):
        nonlocal processed
        processed += 1
        original(builder, value)

    def cancel():
        if processed:
            raise OperationCancelled("test")

    monkeypatch.setattr(_Builder, "record", record)
    with operation_cancellation(cancel), pytest.raises(OperationCancelled):
        resource(saved)
    directory = saved[1].directory / "binary"
    assert not list(directory.iterdir())


def test_changed_path_and_symlink_are_not_accepted_for_cached_resource(saved, tmp_path):
    actual = resource(saved)
    other = tmp_path / "other.json"
    other.write_bytes(actual.source.read_bytes())
    actual.source.unlink()
    actual.source.symlink_to(other)
    with pytest.raises((ValueError, OSError, PreparedGeometryUnavailable)):
        actual.assert_unchanged()


def test_schema_and_unknown_fields_follow_prepared_snapshot_validation(saved):
    snapshot, store, path = saved
    value = snapshot.model_dump(mode="json")
    value["schema_version"] = "autocad-prepared-geometry/99"
    store.files.save(snapshot.key, value)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        resource(saved)
    assert error.value.state == "unsupported"
    value["schema_version"] = snapshot.schema_version
    value["unexpected"] = True
    store.files.save(snapshot.key, value)
    with pytest.raises(ValueError):
        resource(saved)
    assert json.loads(path.read_bytes())["sha256"]
