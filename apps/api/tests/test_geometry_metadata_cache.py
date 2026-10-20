"""Сводки геометрии не повторяют JSON и не подменяют проверку исходных байтов."""

import gzip
import json
import os
from hashlib import sha256
from unittest.mock import Mock

import ijson
import pytest
from test_native_live_provider import setup as setup

from app.dxf_import.contracts import ImportMode
from app.dxf_import.layers.contracts import LayerKind
from app.dxf_import.source.editor_source import source_editor_review
from app.geometry.contracts import GeometrySnapshot
from app.operations.progress import OperationCancelled, operation_cancellation
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.projects.geometry_storage import (
    MAX_VERIFIED_GEOMETRY_REFERENCES,
    GeometryReference,
    GeometryStore,
    geometry_references,
)


def collection():
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 2]},
                "properties": {"source_layer": "ROAD", "kind": "road"},
            },
            {
                "type": "Feature",
                "geometry": None,
                "properties": {
                    "source_layer": "ROAD",
                    "kind": "road",
                    "source_context_only": True,
                },
            },
            {
                "type": "Feature",
                "properties": {"kind": "road"},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [3, 4]},
                "properties": {"source_layer": "BUILDING", "kind": "building"},
            },
        ],
    }


def stored(tmp_path, content=None, *, features=4, truncate_gzip=False):
    content = json.dumps(collection()).encode() if content is None else content
    compressed = gzip.compress(content, compresslevel=1, mtime=0)
    if truncate_gzip:
        compressed = compressed[:-3]
    reference = GeometryReference(
        schema="green-atlas.project-geometry/1",
        sha256=sha256(content).hexdigest(),
        stored_sha256=sha256(compressed).hexdigest(),
        bytes=len(content),
        stored_bytes=len(compressed),
        features=features,
    )
    path = tmp_path / "collection.gz"
    path.write_bytes(compressed)
    return GeometryStore(tmp_path / "store"), reference, path


def read(store, stream, reference, operation):
    if operation == "counts":
        return store.read_display_counts(stream, reference, {"road"})
    return store.read_kind_presence(stream, reference, "road")


def caches_empty(store):
    assert not store._display_counts
    assert not store._kind_presence
    assert not store._verified


def forbid_json(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("A warm summary cannot parse JSON"))
    monkeypatch.setattr(ijson, "parse", forbidden)
    monkeypatch.setattr(ijson, "items", forbidden)
    return forbidden


def test_warm_display_counts_check_sha_without_json_and_return_owned_copies(
    tmp_path, monkeypatch
):
    store, reference, path = stored(tmp_path)
    with path.open("rb") as stream:
        counts = store.read_display_counts(stream, reference, {"road"})
    assert counts == {"ROAD": 1}
    assert not store._verified
    counts["ROAD"] = 100
    counts["unrelated"] = 1
    verify = Mock(wraps=store.verify_stored)
    monkeypatch.setattr(store, "verify_stored", verify)
    forbidden = forbid_json(monkeypatch)
    for _ in range(2):
        with path.open("rb") as stream:
            actual = store.read_display_counts(stream, reference, {"road"})
            assert actual == {"ROAD": 1}
            assert stream.tell() == 0
            actual.clear()
    assert verify.call_count == 2
    forbidden.assert_not_called()
    assert not store._verified


def test_count_cache_separates_kind_sets_and_normalizes_set_order(
    tmp_path, monkeypatch
):
    store, reference, path = stored(tmp_path)
    scan = Mock(wraps=store._read_display_counts)
    monkeypatch.setattr(store, "_read_display_counts", scan)
    cases = [
        ({"road"}, {"ROAD": 1}),
        ({"building"}, {"BUILDING": 1}),
        ({"road", "building"}, {"ROAD": 1, "BUILDING": 1}),
        (set(), {}),
        ({"building", "road"}, {"ROAD": 1, "BUILDING": 1}),
    ]
    for kinds, expected in cases:
        with path.open("rb") as stream:
            assert store.read_display_counts(stream, reference, kinds) == expected
    assert scan.call_count == 4
    assert len(store._display_counts) == 4
    assert not store._verified


@pytest.mark.parametrize("kind,expected", [("road", True), ("missing", False)])
def test_kind_presence_caches_true_and_false_without_parsing_warm_json(
    tmp_path, monkeypatch, kind, expected
):
    store, reference, path = stored(tmp_path)
    with path.open("rb") as stream:
        assert store.read_kind_presence(stream, reference, kind) is expected
    assert len(store._verified) == 1
    verify = Mock(wraps=store.verify_stored)
    monkeypatch.setattr(store, "verify_stored", verify)
    forbidden = forbid_json(monkeypatch)
    with path.open("rb") as stream:
        assert store.read_kind_presence(stream, reference, kind) is expected
        assert stream.tell() == 0
    assert verify.call_count == 1
    forbidden.assert_not_called()


@pytest.mark.parametrize("operation", ["counts", "kind"])
def test_warm_summary_checks_compressed_sha_even_if_fingerprint_is_unchanged(
    tmp_path, monkeypatch, operation
):
    store, reference, path = stored(tmp_path)
    with path.open("rb") as stream:
        read(store, stream, reference, operation)
        before = store._verification_key(stream, reference)
    monkeypatch.setattr(store, "_verification_key", lambda *_: before)
    verify = Mock(wraps=store.verify_stored)
    monkeypatch.setattr(store, "verify_stored", verify)
    forbidden = forbid_json(monkeypatch)
    with path.open("rb") as stream:
        read(store, stream, reference, operation)
    data = path.read_bytes()
    observed = path.stat()
    path.write_bytes(b"!" + data[1:])
    os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns))
    with path.open("rb") as stream, pytest.raises(ValueError, match="целостность"):
        read(store, stream, reference, operation)
    assert verify.call_count == 2
    forbidden.assert_not_called()
    assert (
        len(store._display_counts if operation == "counts" else store._kind_presence)
        == 1
    )


@pytest.mark.parametrize("operation", ["counts", "kind"])
@pytest.mark.parametrize(
    "fault",
    [
        "count",
        "raw-sha",
        "raw-size",
        "stored-sha",
        "syntax",
        "trailing",
        "truncated-gzip",
    ],
)
def test_failed_summary_never_publishes_a_cache_or_full_verification_receipt(
    tmp_path, operation, fault
):
    content = json.dumps(collection()).encode()
    if fault == "syntax":
        content = content[:-1] + b",broken}"
    elif fault == "trailing":
        content += b'\n{"features":[]}'
    store, reference, path = stored(
        tmp_path, content, truncate_gzip=fault == "truncated-gzip"
    )
    updates = {
        "count": {"features": 5},
        "raw-sha": {"sha256": "0" * 64},
        "raw-size": {"bytes": reference.bytes + 1},
        "stored-sha": {"stored_sha256": "0" * 64},
    }.get(fault, {})
    reference = reference.model_copy(update=updates)
    for _ in range(2):
        with (
            path.open("rb") as stream,
            pytest.raises((ValueError, ijson.JSONError, EOFError, OSError)),
        ):
            read(store, stream, reference, operation)
        caches_empty(store)


def test_count_cache_never_certifies_malformed_final_features(tmp_path, monkeypatch):
    store, reference, path = stored(
        tmp_path,
        b'{"features":[{"properties":{"source_layer":"ROAD","kind":"road"}}],"features":false}',
        features=1,
    )
    with path.open("rb") as stream:
        assert store.read_display_counts(stream, reference, {"road"}) == {"ROAD": 1}
    assert not store._verified
    with monkeypatch.context() as patch:
        forbidden = forbid_json(patch)
        with path.open("rb") as stream:
            assert store.read_display_counts(stream, reference, {"road"}) == {"ROAD": 1}
        forbidden.assert_not_called()
    assert not store._verified
    with path.open("rb") as stream, pytest.raises(ValueError):
        store.verify_reference(stream, reference)
    assert not store._verified
    with path.open("rb") as stream, pytest.raises(ValueError):
        store.read_kind_presence(stream, reference, "road")
    assert not store._kind_presence
    assert not store._verified


@pytest.mark.parametrize("operation", ["counts", "kind"])
def test_cancel_at_end_of_read_does_not_publish_summary(
    tmp_path, monkeypatch, operation
):
    store, reference, path = stored(tmp_path)
    cancelled = False
    method = "_read_display_counts" if operation == "counts" else "_consume_features"
    original = getattr(store, method)

    def complete_then_cancel(*args, **kwargs):
        nonlocal cancelled
        result = original(*args, **kwargs)
        cancelled = True
        return result

    def check_cancelled():
        if cancelled:
            raise OperationCancelled()

    monkeypatch.setattr(store, method, complete_then_cancel)
    with operation_cancellation(check_cancelled):
        with path.open("rb") as stream, pytest.raises(OperationCancelled):
            read(store, stream, reference, operation)
    caches_empty(store)


@pytest.mark.parametrize("operation", ["counts", "kind"])
def test_mutation_at_end_of_read_does_not_publish_summary(
    tmp_path, monkeypatch, operation
):
    store, reference, path = stored(tmp_path)
    method = "_read_display_counts" if operation == "counts" else "_consume_features"
    original = getattr(store, method)

    def complete_then_mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        observed = path.stat()
        os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns + 1_000_000))
        return result

    monkeypatch.setattr(store, method, complete_then_mutate)
    with path.open("rb") as stream, pytest.raises(ValueError, match="изменилась"):
        read(store, stream, reference, operation)
    caches_empty(store)


@pytest.mark.parametrize("operation", ["counts", "kind"])
def test_warm_summary_rejects_mutation_during_compressed_sha_check(
    tmp_path, monkeypatch, operation
):
    store, reference, path = stored(tmp_path)
    with path.open("rb") as stream:
        read(store, stream, reference, operation)
    keys_before = tuple(store._verified)
    original = store.verify_stored

    def verify_then_mutate(*args):
        original(*args)
        observed = path.stat()
        os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns + 1_000_000))

    monkeypatch.setattr(store, "verify_stored", verify_then_mutate)
    with path.open("rb") as stream, pytest.raises(ValueError, match="изменилась"):
        read(store, stream, reference, operation)
    assert tuple(store._verified) == keys_before


@pytest.mark.parametrize("operation", ["counts", "kind"])
def test_summary_caches_are_bounded_lru_and_retain_only_metadata(tmp_path, operation):
    store, reference, path = stored(tmp_path)

    def read_index(index):
        with path.open("rb") as stream:
            if operation == "counts":
                return store.read_display_counts(
                    stream, reference, {"road", f"kind-{index}"}
                )
            return store.read_kind_presence(stream, reference, f"kind-{index}")

    for index in range(MAX_VERIFIED_GEOMETRY_REFERENCES):
        read_index(index)
    cache = store._display_counts if operation == "counts" else store._kind_presence
    first, second = list(cache)[:2]
    read_index(0)
    read_index(MAX_VERIFIED_GEOMETRY_REFERENCES)
    assert len(cache) == MAX_VERIFIED_GEOMETRY_REFERENCES
    assert first in cache and second not in cache
    if operation == "counts":
        assert all(
            isinstance(key, str) and isinstance(count, int)
            for item in cache.values()
            for key, count in item.items()
        )
    else:
        assert all(isinstance(item, bool) for item in cache.values())


def test_kind_cache_separates_geometry_requirement_and_role_identity(
    tmp_path, monkeypatch
):
    value = collection()
    value["features"] = [{"properties": {"source_layer": "ROAD", "kind": "ignore"}}]
    store, reference, path = stored(tmp_path, json.dumps(value).encode(), features=1)
    applied = []

    def role(kind):
        def apply(features):
            applied.append(kind)
            for feature in features:
                feature["properties"]["kind"] = kind

        return apply

    scan = Mock(wraps=store.consume_features)
    monkeypatch.setattr(store, "consume_features", scan)
    cases = [
        ("road-v1", "road", False, True),
        ("building-v2", "building", False, False),
        ("road-v1", "road", True, False),
        ("road-v1", "road", False, True),
    ]
    for identity, mapped, required, expected in cases:
        with path.open("rb") as stream:
            assert (
                store.read_kind_presence(
                    stream,
                    reference,
                    "road",
                    roles=role(mapped),
                    roles_identity=identity,
                    require_geometry=required,
                )
                is expected
            )
    assert applied == ["road", "building", "road"]
    assert scan.call_count == 3
    assert len(store._kind_presence) == 3


def test_repository_kind_cache_uses_current_role_revision_with_same_geometry(
    setup, tmp_path, monkeypatch
):
    engine, _, project = setup
    project.source_file.native_session = engine.session
    project.import_status.mode = ImportMode.AUTOCAD_LIVE
    project.plan = None
    project.source_review = source_editor_review(project)
    project.geometry.feature_collection = {
        "type": "FeatureCollection",
        "features": [{"properties": {"source_layer": "Дорога", "kind": "ignore"}}],
    }
    repository = SqliteProjectRepository(tmp_path / "roles.sqlite3")
    try:
        project = repository.create(project)
        before = json.loads(
            repository._connection.execute(
                "SELECT payload FROM projects WHERE id=?", (project.id,)
            ).fetchone()[0]
        )
        reference = geometry_references(before)["geometry"]
        scan = Mock(wraps=repository._geometry_store.consume_features)
        monkeypatch.setattr(repository._geometry_store, "consume_features", scan)
        assert (
            repository.has_geometry_kind(project.id, expected_version=1, kind="road")
            is True
        )
        revised = repository.get(project.id, lightweight=True)
        revised.layers[2].mapped_kind = LayerKind.BUILDING
        revised = repository.save_import_mappings(revised, source_editor=True)
        after = json.loads(
            repository._connection.execute(
                "SELECT payload FROM projects WHERE id=?", (project.id,)
            ).fetchone()[0]
        )
        assert geometry_references(after)["geometry"] == reference
        assert revised.state_version == 2
        with pytest.raises(ProjectVersionConflict):
            repository.has_geometry_kind(project.id, expected_version=1, kind="road")
        assert (
            repository.has_geometry_kind(project.id, expected_version=2, kind="road")
            is False
        )
        assert (
            repository.has_geometry_kind(project.id, expected_version=2, kind="road")
            is False
        )
        assert scan.call_count == 2
    finally:
        repository.close()


def test_repository_rechecks_revision_after_kind_query(tmp_path, monkeypatch):
    repository = SqliteProjectRepository(tmp_path / "race.sqlite3")
    try:
        project = repository.create(
            Project(
                name="Ревизия чтения",
                geometry=GeometrySnapshot(feature_collection=collection()),
            )
        )
        original = repository._geometry_store.read_kind_presence

        def concurrent_change(*args, **kwargs):
            result = original(*args, **kwargs)
            project.name = "Следующая ревизия"
            repository.save(project)
            return result

        monkeypatch.setattr(
            repository._geometry_store, "read_kind_presence", concurrent_change
        )
        with pytest.raises(ProjectVersionConflict) as error:
            repository.has_geometry_kind(project.id, expected_version=1, kind="road")
        assert error.value.current_version == 2
    finally:
        repository.close()
