"""Native-проверка точки читает прежние свидетельства без полного графа карты."""

import gzip
import json
import os
from hashlib import sha256
from pathlib import Path
from unittest.mock import Mock

import ijson
import pytest
from test_hybrid_search import populate
from test_native_live_provider import setup as setup

from app.composition import create_runtime
from app.dxf_import.contracts import ImportMode
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.progress import OperationCancelled
from app.planning.changes.manual_application import ManualPlanningApplication
from app.planning.contracts import PlacementCheckRequest, PlanObject
from app.projects.geometry_storage import (
    MAX_VERIFIED_GEOMETRY_REFERENCES,
    GeometryReference,
    GeometryStore,
    geometry_references,
)


class _FullReader:
    """Совместимый reader без optional порта воспроизводит прежний полный путь."""

    def __init__(self, repository):
        self.repository = repository

    def get(self, project_id, *, lightweight=False):
        return self.repository.get(project_id, lightweight=lightweight)


@pytest.fixture
def prepared_runtime(setup, tmp_path, monkeypatch):
    live, client, project = setup
    populate(project)
    project.source_file.native_session = live.session
    project.import_status.mode = ImportMode.AUTOCAD_LIVE
    project.map_ready = True
    project.plan.objects.append(
        PlanObject(id="existing", kind="tree", x=50, y=5, radius=0.5)
    )
    store = PreparedStore(tmp_path / "prepared")
    store.save(prepare_snapshot(live, project))
    compatibility = Mock()
    router = ProjectLiveGeometry(compatibility, prepared_store=store)
    forbidden = Mock(side_effect=AssertionError("Native point check must stay local"))
    monkeypatch.setattr(router, "live_engine", forbidden)
    monkeypatch.setattr(client, "inspect", forbidden)
    runtime = create_runtime(tmp_path / "points.sqlite3", geometry_engine=router)
    runtime.project_repository.create(project)
    yield runtime, router, store, project, compatibility, forbidden
    runtime.close()


def full_reader(manual):
    return ManualPlanningApplication(
        _FullReader(manual.repository),
        manual.evaluation,
        manual.changes,
        manual._plan_spacing_index,
        manual._manual_edit_lock,
    )


@pytest.mark.parametrize(
    "candidate",
    [
        {"kind": "shrub", "x": 5, "y": 0, "radius": 0.5},
        {"kind": "shrub", "x": 23, "y": 0, "radius": 0.5},
        {"kind": "tree", "x": 50, "y": 5, "radius": 0.5},
        {"kind": "tree", "x": 60, "y": 50, "explain_geometry": True},
        {"kind": "tree", "x": 99, "y": 0},
        {"kind": "tree", "x": 120, "y": 0},
        {
            "kind": "tree",
            "x": 60,
            "y": 0,
            "species_revision_id": "tilia-cordata@2026-08-28.1",
            "size_class": "large",
            "scenario_horizon_year": 20,
        },
    ],
)
def test_native_point_projection_matches_full_reader_cold_and_warm(
    prepared_runtime, monkeypatch, candidate
):
    runtime, router, _, project, compatibility, forbidden = prepared_runtime
    manual = runtime.application.manual
    request = PlacementCheckRequest(**candidate)
    before = runtime.project_repository.get(project.id).model_dump(mode="json")
    expected = full_reader(manual).check_placement(project.id, request)
    router.discard(project.id)
    geometry_store = runtime.project_repository._geometry_store
    geometry_store._verified.clear()
    scan = Mock(wraps=geometry_store.consume_features)
    monkeypatch.setattr(geometry_store, "consume_features", scan)
    monkeypatch.setattr(
        runtime.project_repository,
        "_hydrate_geometry_row",
        Mock(side_effect=AssertionError("Point check cannot hydrate the full map")),
    )
    for _ in range(2):
        actual = manual.check_placement(project.id, request)
        assert actual.model_dump(mode="json") == expected.model_dump(mode="json")
    assert scan.call_count == 1
    assert (
        runtime.project_repository.get(project.id, lightweight=True).model_dump(
            mode="json"
        )["plan"]
        == before["plan"]
    )
    assert (
        runtime.project_repository.get(project.id, lightweight=True).state_version
        == before["state_version"]
    )
    assert not compatibility.mock_calls
    forbidden.assert_not_called()


@pytest.mark.parametrize("scope", [None, "complete_source", "available_data"])
def test_point_projection_keeps_geometry_gate_scope_and_stale_versions(
    prepared_runtime, monkeypatch, scope
):
    runtime, _, _, project, _, _ = prepared_runtime
    saved = runtime.project_repository.get(project.id)
    saved.geometry.calculation_scope = scope
    runtime.project_repository.save(saved)
    manual = runtime.application.manual
    for changes in ({}, {"state_version": saved.state_version + 1}):
        request = PlacementCheckRequest(kind="tree", x=60, y=50, **changes)
        expected = full_reader(manual).check_placement(project.id, request)
        actual = manual.check_placement(project.id, request)
        assert actual.model_dump() == expected.model_dump()
    saved.geometry = None
    runtime.project_repository.save(saved)
    request = PlacementCheckRequest(kind="tree", x=60, y=50)
    expected = full_reader(manual).check_placement(project.id, request)
    assert (
        manual.check_placement(project.id, request).model_dump()
        == expected.model_dump()
    )
    assert not expected.allowed and expected.reason == "Сначала подготовьте карту"


@pytest.mark.parametrize("problem", ["missing", "corrupt", "stale", "source"])
def test_point_projection_preserves_prepared_failures(prepared_runtime, problem):
    runtime, router, store, project, compatibility, forbidden = prepared_runtime
    path = next(path for path in store.directory.glob("*.json") if len(path.stem) == 64)
    if problem == "missing":
        path.unlink()
    elif problem == "corrupt":
        path.write_bytes(b"!" + path.read_bytes()[1:])
    else:
        saved = runtime.project_repository.get(project.id)
        if problem == "stale":
            saved.layers[1].mapping_confirmed = False
        else:
            saved.source_file.content_sha256 = "f" * 64
        runtime.project_repository.save(saved)
    request = PlacementCheckRequest(kind="tree", x=60, y=50)
    errors = []
    for manual in (full_reader(runtime.application.manual), runtime.application.manual):
        router.discard(project.id)
        with pytest.raises((PreparedGeometryUnavailable, ValueError)) as error:
            manual.check_placement(project.id, request)
        errors.append(error.value)
    assert type(errors[0]) is type(errors[1])
    assert str(errors[0]) == str(errors[1])
    assert not compatibility.mock_calls
    forbidden.assert_not_called()


def stored(tmp_path: Path, content: bytes, *, features: int = 1):
    compressed = gzip.compress(content, compresslevel=1, mtime=0)
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


def test_verified_reference_keeps_all_reference_fields_and_stat_changes(
    tmp_path, monkeypatch
):
    store, reference, path = stored(tmp_path, b'{"features":[{"id":"one"}]}')
    scan = Mock(wraps=store.consume_features)
    monkeypatch.setattr(store, "consume_features", scan)
    with path.open("rb") as stream:
        store.verify_reference(stream, reference)
        store.verify_reference(stream, reference)
        assert stream.tell() == 0
    assert scan.call_count == 1
    for updates in (
        {"features": 2},
        {"sha256": "0" * 64},
        {"bytes": reference.bytes + 1},
    ):
        with path.open("rb") as stream, pytest.raises(ValueError):
            store.verify_reference(stream, reference.model_copy(update=updates))
    before = path.stat()
    original = path.read_bytes()
    path.write_bytes(b"!" + original[1:])
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    with path.open("rb") as stream, pytest.raises(ValueError):
        store.verify_reference(stream, reference)


@pytest.mark.parametrize(
    "content,features",
    [
        (b'{"features":{}}', 0),
        (b'{"features":[]} {}', 0),
        (
            b'{"features":[null,5],"features":[{"integer":1267650600228229401496703205376}]}',
            1,
        ),
    ],
)
def test_reference_verifier_matches_full_reader_json_contract(
    tmp_path, content, features
):
    store, reference, path = stored(tmp_path, content, features=features)
    with path.open("rb") as stream:
        try:
            store.read(stream, reference)
        except (ValueError, ijson.JSONError) as error:
            expected_error = type(error)
        else:
            expected_error = None
    store._verified.clear()
    with path.open("rb") as stream:
        if expected_error:
            with pytest.raises(expected_error):
                store.verify_reference(stream, reference)
            assert not store._verified
        else:
            store.verify_reference(stream, reference)
            assert len(store._verified) == 1


def test_cancelled_reference_verification_does_not_publish_receipt(
    tmp_path, monkeypatch
):
    store, reference, path = stored(tmp_path, b'{"features":[{"id":"one"}]}')
    finish = store._consume_features

    def cancel_after_read(*args, **kwargs):
        finish(*args, **kwargs)
        raise OperationCancelled()

    monkeypatch.setattr(store, "_consume_features", cancel_after_read)
    with path.open("rb") as stream, pytest.raises(OperationCancelled):
        store.verify_reference(stream, reference)
    assert not store._verified


def test_point_projection_race_preserves_stale_placement_response(
    prepared_runtime, monkeypatch
):
    runtime, _, _, project, _, _ = prepared_runtime
    repository = runtime.project_repository
    before = repository.get(project.id, lightweight=True)
    read = repository.get_placement_projection

    def change_before_projection(project_id, *, expected_version):
        current = repository.get(project_id)
        current.plan.version += 1
        repository.save(current)
        return read(project_id, expected_version=expected_version)

    monkeypatch.setattr(
        repository, "get_placement_projection", change_before_projection
    )
    result = runtime.application.manual.check_placement(
        project.id,
        PlacementCheckRequest(
            kind="tree", x=60, y=50, base_plan_version=before.plan.version
        ),
    )
    assert not result.allowed and result.code == "STALE_PLACEMENT_BASIS"
    assert result.plan_version == before.plan.version + 1
    assert result.state_version == before.state_version + 1


def test_point_projection_provider_without_capability_keeps_full_reader(
    prepared_runtime, monkeypatch
):
    runtime, router, _, project, _, _ = prepared_runtime
    manual = runtime.application.manual

    class CompatibilityProvider:
        def __getattr__(self, name):
            return getattr(router, name)

    monkeypatch.setattr(manual.evaluation, "geometry", CompatibilityProvider())
    hydrate = Mock(wraps=runtime.project_repository._hydrate_geometry_row)
    narrow = Mock(side_effect=AssertionError("Compatibility requires the full reader"))
    monkeypatch.setattr(runtime.project_repository, "_hydrate_geometry_row", hydrate)
    monkeypatch.setattr(runtime.project_repository, "get_placement_projection", narrow)
    result = manual.check_placement(
        project.id, PlacementCheckRequest(kind="shrub", x=5, y=0)
    )
    assert result.code == "NATIVE_OCCUPIED" and not result.allowed
    assert hydrate.call_count == 1
    narrow.assert_not_called()


@pytest.mark.parametrize("field", ["geometry", "source_geometry"])
@pytest.mark.parametrize("problem", ["corrupt", "missing"])
def test_point_projection_keeps_both_map_integrity_failures_after_cached_read(
    prepared_runtime, monkeypatch, field, problem
):
    runtime, _, _, project, _, _ = prepared_runtime
    repository = runtime.project_repository
    saved = repository.get(project.id)
    saved.source_geometry = saved.geometry.model_copy(deep=True)
    saved.source_geometry.feature_collection["foreign"] = "original source"
    repository.save(saved)
    # Успешный полный читатель заполняет квитанции обеих отдельных коллекций.
    repository.get(project.id)
    payload = json.loads(
        repository._connection.execute(
            "SELECT payload FROM projects WHERE id = ?", (project.id,)
        ).fetchone()[0]
    )
    references = geometry_references(payload)
    assert set(references) == {"geometry", "source_geometry"}
    path = repository._geometry_store.path(project.id, references[field])
    if problem == "missing":
        path.unlink()
    else:
        before = path.stat()
        original = path.read_bytes()
        path.write_bytes(b"!" + original[1:])
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert path.stat().st_size == before.st_size
    request = PlacementCheckRequest(kind="tree", x=60, y=50)
    with pytest.raises((ValueError, OSError)) as expected:
        full_reader(runtime.application.manual).check_placement(project.id, request)
    monkeypatch.setattr(
        repository,
        "_hydrate_geometry_row",
        Mock(side_effect=AssertionError("Corrupt map must fail without hydration")),
    )
    with pytest.raises(type(expected.value)) as actual:
        runtime.application.manual.check_placement(project.id, request)
    assert str(actual.value) == str(expected.value)
    assert (
        repository.get(project.id, lightweight=True).state_version
        == saved.state_version
    )


def test_reference_mutation_during_scan_cannot_publish_receipt(tmp_path, monkeypatch):
    store, reference, path = stored(tmp_path, b'{"features":[{"id":"one"}]}')
    scan = store._consume_features

    def mutate_after_read(*args, **kwargs):
        result = scan(*args, **kwargs)
        observed = path.stat()
        os.utime(path, ns=(observed.st_atime_ns, observed.st_mtime_ns + 1_000_000))
        return result

    monkeypatch.setattr(store, "_consume_features", mutate_after_read)
    with path.open("rb") as stream, pytest.raises(ValueError, match="изменилась"):
        store.verify_reference(stream, reference)
    assert not store._verified


def test_verified_reference_receipts_are_bounded_and_do_not_retain_features(tmp_path):
    store = GeometryStore(tmp_path / "store")
    for index in range(MAX_VERIFIED_GEOMETRY_REFERENCES + 3):
        directory = tmp_path / str(index)
        directory.mkdir()
        content = json.dumps({"features": [{"id": str(index)}]}).encode()
        _, reference, path = stored(directory, content)
        with path.open("rb") as stream:
            store.verify_reference(stream, reference)
    assert len(store._verified) == MAX_VERIFIED_GEOMETRY_REFERENCES
    assert all(value is None for value in store._verified.values())


def test_display_count_read_cannot_certify_a_malformed_final_features_value(tmp_path):
    store, reference, path = stored(
        tmp_path, b'{"features":[{"id":"a"}],"features":false}'
    )
    # Прежний подсчёт читает features.item; его результат не доказывает форму
    # последнего features, которую обязан проверить полный читатель и посадка.
    with path.open("rb") as stream:
        assert store.read_display_counts(stream, reference, set()) == {}
    assert not store._verified
    with path.open("rb") as stream, pytest.raises(ValueError):
        store.verify_reference(stream, reference)
    assert not store._verified


def test_warm_reference_checks_compressed_sha_even_with_unchanged_timestamps(
    tmp_path, monkeypatch
):
    store, reference, path = stored(tmp_path, b'{"features":[{"id":"one"}]}')
    with path.open("rb") as stream:
        store.read(stream, reference)
        before = store._verification_key(stream, reference)
    assert before in store._verified
    scan = Mock(side_effect=AssertionError("Warm proof must not decode JSON again"))
    verify = Mock(wraps=store.verify_stored)
    monkeypatch.setattr(store, "consume_features", scan)
    monkeypatch.setattr(store, "verify_stored", verify)
    monkeypatch.setattr(store, "_verification_key", lambda *_: before)
    with path.open("rb") as stream:
        store.verify_reference(stream, reference)
    assert verify.call_count == 1
    scan.assert_not_called()

    original = path.read_bytes()
    path.write_bytes(b"!" + original[1:])
    with path.open("rb") as stream, pytest.raises(ValueError, match="целостность"):
        store.verify_reference(stream, reference)
    assert verify.call_count == 2
    scan.assert_not_called()


def test_cancelled_warm_reference_verification_does_not_return_cached_success(
    tmp_path, monkeypatch
):
    store, reference, path = stored(tmp_path, b'{"features":[{"id":"one"}]}')
    with path.open("rb") as stream:
        store.read(stream, reference)
    monkeypatch.setattr(store, "_digest", Mock(side_effect=OperationCancelled))
    with path.open("rb") as stream, pytest.raises(OperationCancelled):
        store.verify_reference(stream, reference)
