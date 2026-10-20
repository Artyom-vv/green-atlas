"""Точный дисковый кеш паспорта не заменяет проверку расчётного снимка."""

import errno
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

import pytest
from test_native_live_provider import setup as setup
from test_passport_coverage_lifetime import observe_snapshot_load
from test_passport_coverage_lifetime import prepared as prepared

import app.native_query.preparation.coverage_cache as cache_module
import app.native_query.project_runtime as runtime
from app.geometry.networks.utility_contracts import UtilityContext
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.coverage_cache import coverage_identity
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.operations.progress import OperationCancelled, operation_cancellation


def fresh(store):
    return ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(store.directory))


@pytest.fixture
def saved(prepared):
    project, snapshot, store, expected, client, live = prepared
    assert fresh(store).source_coverage(project) == expected
    return project, snapshot, store, expected, client, live


def forbid_hydration(monkeypatch, router):
    fail = Mock(side_effect=AssertionError("Сводка не загружает полный граф"))
    monkeypatch.setattr(router, "_load_snapshot", fail)
    monkeypatch.setattr(router.prepared_store, "load_project", fail)
    monkeypatch.setattr(runtime, "PreparedGeometryEngine", fail)
    return fail


def test_fresh_cache_hit_preserves_all_addressed_issues_without_hydration(
    saved, monkeypatch
):
    project, snapshot, store, expected, client, _ = saved
    router = fresh(store)
    fail = forbid_hydration(monkeypatch, router)
    digest = Mock(wraps=router._coverage_cache._file_digest)
    monkeypatch.setattr(router._coverage_cache, "_file_digest", digest)
    first = router.source_coverage(project)
    assert first.model_dump_json() == expected.model_dump_json()
    assert any(
        row.routes for layer in first.layers.values() for row in layer.unresolved
    )
    first.layers.clear()
    assert router.metadata_source_coverage(project) == expected
    assert router.source_coverage(project) == expected
    assert digest.call_count == 1
    assert not router._prepared and not client.calls
    assert router.prepared_store.verified_fingerprint(snapshot.key) is None
    assert router.prepared_store._verified == {}
    fail.assert_not_called()


def test_cache_hit_does_not_admit_a_calculation_engine(saved, monkeypatch):
    project, snapshot, store, expected, _, _ = saved
    router = fresh(store)
    assert router.source_coverage(project) == expected
    load = observe_snapshot_load(monkeypatch, router)
    engine = router.load_local(project)
    assert isinstance(engine, PreparedGeometryEngine)
    assert engine.source_coverage(project) == expected
    assert load.call_count == 1
    assert router.prepared_store.verified_fingerprint(snapshot.key) is not None


@pytest.mark.parametrize(
    "damage",
    [
        "json",
        "checksum",
        "schema",
        "identity",
        "source",
        "key",
        "coverage_source",
        "coverage_key",
        "size",
        "sha",
        "linear",
    ],
)
def test_invalid_cache_falls_back_to_full_validation(saved, monkeypatch, damage):
    project, snapshot, store, expected, _, _ = saved
    router = fresh(store)
    files = router._coverage_cache.files
    path = files.directory / f"{snapshot.key}.json"
    if damage == "json":
        path.write_bytes(b'{"broken":')
    elif damage == "checksum":
        envelope = json.loads(path.read_bytes())
        envelope["sha256"] = "0" * 64
        path.write_text(json.dumps(envelope))
    else:
        value = files.load(snapshot.key)
        if damage == "schema":
            value["schema_version"] = "green-atlas.coverage-cache/0"
        elif damage == "identity":
            value["identity"] = "0" * 64
        elif damage in {"source", "key", "sha"}:
            value[
                {
                    "source": "source_sha256",
                    "key": "prepared_key",
                    "sha": "prepared_sha256",
                }[damage]
            ] = "0" * 64
        elif damage.startswith("coverage_"):
            value["coverage"][
                "source_sha256" if damage == "coverage_source" else "prepared_key"
            ] = "0" * 64
        elif damage == "size":
            value["prepared_bytes"] += 1
        else:
            value["linear_layers"] = ["Здания"]
            value["identity"] = coverage_identity(project, frozenset({"Здания"}))
        files.save(snapshot.key, value)
    load = observe_snapshot_load(monkeypatch, router)
    assert router.source_coverage(project) == expected
    assert load.call_count == 1
    assert fresh(store).source_coverage(project) == expected


@pytest.mark.parametrize("change", ["counts", "semantic", "evidence", "policy"])
def test_changed_coverage_basis_cannot_reuse_persisted_result(
    saved, monkeypatch, change
):
    project, _, store, expected, _, _ = saved
    if change == "counts":
        project.layers[1].object_count += 1
    elif change == "semantic":
        project.layers[1].utility_context = UtilityContext(network_type="water")
    elif change == "evidence":
        project.layers[1].utility_context = UtilityContext(
            source_reference="Другой документ"
        )
    else:
        monkeypatch.setattr(cache_module, "COVERAGE_CACHE_POLICY", "next-policy")
    router = fresh(store)
    load = observe_snapshot_load(monkeypatch, router)
    assert router.source_coverage(project) == expected
    assert load.call_count == (0 if change == "horizon" else 1)


@pytest.mark.parametrize("in_memory", [False, True])
def test_horizon_keeps_exact_source_coverage_without_new_snapshot(
    saved, monkeypatch, in_memory
):
    project, _, store, expected, _, _ = saved
    router = fresh(store)
    if in_memory:
        assert router.source_coverage(project) == expected
    project.plan.planning_horizon_year += 1
    # Горизонт влияет на габариты растения в запросе, а не полноту CAD-снимка.
    fail = forbid_hydration(monkeypatch, router)
    assert router.metadata_source_coverage(project) == (expected if in_memory else None)
    assert router.source_coverage(project) == expected
    fail.assert_not_called()


@pytest.mark.parametrize("change", ["roles", "source", "units", "revision"])
def test_changed_geometry_requires_matching_preparation(saved, change):
    project, snapshot, store, _, _, _ = saved
    if change == "roles":
        project.layers[1].mapping_confirmed = False
    elif change == "source":
        project.source_file.content_sha256 = "f" * 64
        project.source_file.native_session = (
            project.source_file.native_session.model_copy(
                update={"snapshot_sha256": "f" * 64}
            )
        )
    elif change == "units":
        project.source_file.native_session = (
            project.source_file.native_session.model_copy(update={"units_code": 4})
        )
    else:
        project.source_file.native_session = (
            project.source_file.native_session.model_copy(
                update={"inventory_sha256": "f" * 64}
            )
        )
    router = fresh(store)
    assert router._coverage_cache.read(project, snapshot.key) is None
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.source_coverage(project)
    assert error.value.state == "stale"
    assert not router._coverage_reads


def test_counts_invalidate_both_memory_summary_and_cached_engine(prepared):
    project, _, store, _, _, live = prepared
    missing = project.layers[1].model_copy(
        update={"id": "empty", "source_name": "Пустой", "object_count": 0}
    )
    project.layers.append(missing)
    store.save(prepare_snapshot(live, project))
    router = fresh(store)
    engine = router.load_local(project)
    before = router.source_coverage(project)
    assert before.layers["Пустой"].unresolved == []
    missing.object_count = 1
    assert router.metadata_source_coverage(project) is None
    result = router.source_coverage(project)
    assert [issue.reason for issue in result.layers["Пустой"].unresolved] == [
        "inventory_missing"
    ]
    assert router._prepared[project.id] is engine
    assert fresh(store).source_coverage(project) == result


def test_precomputed_engine_coverage_does_not_repeat_projection(prepared, monkeypatch):
    project, _, store, expected, _, _ = prepared
    router = fresh(store)
    engine = router.load_local(project)
    assert engine.source_coverage(project) == expected
    monkeypatch.setattr(
        engine.projection,
        "get",
        Mock(side_effect=AssertionError("Готовая сводка не обходит проекции повторно")),
    )
    assert router.source_coverage(project) == expected


def test_metadata_only_refuses_changed_counts_without_a_router_summary(prepared):
    project, _, store, _, _, _ = prepared
    router = fresh(store)
    engine = router.load_local(project)
    engine.source_coverage(project)
    project.layers[1].object_count += 1
    assert router.metadata_source_coverage(project) is None


@pytest.mark.parametrize("change", ["corrupt", "valid_bytes", "missing"])
def test_prepared_bytes_remain_authoritative(saved, monkeypatch, change):
    project, snapshot, store, expected, _, _ = saved
    path = store.directory / f"{snapshot.key}.json"
    if change == "corrupt":
        path.write_bytes(b'{"broken":')
    elif change == "missing":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b" ")
    router = fresh(store)
    load = observe_snapshot_load(monkeypatch, router)
    if change == "valid_bytes":
        assert router.source_coverage(project) == expected
    else:
        with pytest.raises(PreparedGeometryUnavailable) as error:
            router.source_coverage(project)
        assert error.value.state == ("corrupt" if change == "corrupt" else "stale")
        assert not router._coverage_reads
    assert load.call_count == 1


class ReadHook:
    """Изменение файла во время реального чтения закреплённого дескриптора."""

    def __init__(self, stream, action):
        self.stream, self.action = stream, action

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.stream.close()

    def fileno(self):
        return self.stream.fileno()

    def read(self, size):
        chunk = self.stream.read(size)
        if self.action is not None:
            action, self.action = self.action, None
            action()
        return chunk


def hook_digest(monkeypatch, action):
    original = cache_module.read_regular
    monkeypatch.setattr(
        cache_module, "read_regular", lambda path: ReadHook(original(path), action)
    )


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("change", ["replace", "append", "unlink", "basis"])
def test_digest_refuses_changes_during_pinned_read(
    prepared, monkeypatch, change, existing
):
    project, snapshot, store, _, _, _ = prepared
    if existing:
        fresh(store).source_coverage(project)
    path = store.directory / f"{snapshot.key}.json"

    def mutate():
        if change == "replace":
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            replacement.replace(path)
        elif change == "append":
            with path.open("ab") as stream:
                stream.write(b" ")
        elif change == "unlink":
            path.unlink()
        else:
            project.layers[1].object_count += 1

    hook_digest(monkeypatch, mutate)
    router = fresh(store)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.source_coverage(project)
    assert error.value.state == "stale"
    assert not router._prepared and not router._coverage_reads


def test_cold_metadata_does_not_implicitly_read_persisted_coverage(saved, monkeypatch):
    project, _, store, _, _, _ = saved
    router = fresh(store)
    read = Mock(side_effect=AssertionError("Метаданные не читают неизвестные байты"))
    monkeypatch.setattr(router._coverage_cache, "read", read)
    assert router.metadata_source_coverage(project) is None
    read.assert_not_called()


def test_derived_symlink_falls_back_without_overwriting_target(
    saved, monkeypatch, tmp_path
):
    project, snapshot, store, expected, _, _ = saved
    router = fresh(store)
    cache_path = router._coverage_cache.files.directory / f"{snapshot.key}.json"
    target = tmp_path / "unrelated.json"
    original = cache_path.read_bytes()
    target.write_bytes(original)
    cache_path.unlink()
    try:
        cache_path.symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create file symlinks")
        raise
    load = observe_snapshot_load(monkeypatch, router)
    assert router.source_coverage(project) == expected
    assert load.call_count == 1
    assert target.read_bytes() == original
    assert cache_path.is_symlink()


def test_prepared_symlink_is_never_admitted_from_a_coverage_cache(saved, tmp_path):
    project, snapshot, store, _, _, _ = saved
    path = store.directory / f"{snapshot.key}.json"
    target = tmp_path / "copied-prepared.json"
    target.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError as error:
        if getattr(error, "winerror", None) == 1314:
            pytest.skip("Windows account cannot create file symlinks")
        raise
    router = fresh(store)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.source_coverage(project)
    assert error.value.state == "corrupt"
    assert not router._coverage_reads


@pytest.mark.parametrize("existing", [False, True])
def test_cancellation_during_digest_never_publishes_summary(
    prepared, monkeypatch, existing
):
    project, _, store, _, _, _ = prepared
    if existing:
        fresh(store).source_coverage(project)
    cancelled = False

    def stop():
        nonlocal cancelled
        cancelled = True

    def check():
        if cancelled:
            raise OperationCancelled()

    hook_digest(monkeypatch, stop)
    router = fresh(store)
    with operation_cancellation(check), pytest.raises(OperationCancelled):
        router.source_coverage(project)
    assert not router._prepared and not router._coverage_reads
    assert not router._coverage_cache._verified
    assert bool(list(router._coverage_cache.files.directory.glob("*.json"))) == existing


def test_cache_disk_full_does_not_discard_valid_coverage(prepared, monkeypatch):
    project, _, store, expected, _, _ = prepared
    router = fresh(store)
    monkeypatch.setattr(
        router._coverage_cache.files,
        "save",
        Mock(side_effect=OSError(errno.ENOSPC, "full")),
    )
    assert router.source_coverage(project) == expected
    assert router.metadata_source_coverage(project) == expected
    assert not list(router._coverage_cache.files.directory.glob("*.json"))


def test_concurrent_cold_reads_hash_once_and_copy_compact_result(saved, monkeypatch):
    project, _, store, expected, _, _ = saved
    router = fresh(store)
    forbid_hydration(monkeypatch, router)
    digest = Mock(wraps=router._coverage_cache._file_digest)
    monkeypatch.setattr(router._coverage_cache, "_file_digest", digest)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: router.source_coverage(project), range(2)))
    assert results == [expected, expected]
    assert results[0] is not results[1]
    assert digest.call_count == 1


def test_known_corrupt_state_is_not_overridden_by_cache(saved, monkeypatch):
    project, snapshot, store, _, _, _ = saved
    router = fresh(store)
    router.prepared_store.record_failure(snapshot.key, "corrupt")
    read = Mock(
        side_effect=AssertionError("Известное повреждение требует полной проверки")
    )
    monkeypatch.setattr(router._coverage_cache, "read", read)
    load = Mock(side_effect=PreparedGeometryUnavailable("corrupt"))
    monkeypatch.setattr(router, "_load_snapshot", load)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.source_coverage(project)
    assert error.value.state == "corrupt"
    load.assert_called_once_with(project, project.source_file.native_session)
    read.assert_not_called()


def test_known_failure_revalidates_unchanged_source_without_full_json(
    saved, monkeypatch
):
    project, snapshot, store, expected, _, _ = saved
    router = fresh(store)
    router.prepared_store.record_failure(snapshot.key, "corrupt")
    assert router.prepared_store.state(project, project.source_file.native_session) == (
        "corrupt"
    )
    assert router.prepared_store.verified_fingerprint(snapshot.key) is None
    read = Mock(side_effect=AssertionError("Известный отказ не заменяется сводкой"))
    monkeypatch.setattr(router._coverage_cache, "read", read)
    full_json = Mock(side_effect=AssertionError("WKB не загружает полный JSON"))
    monkeypatch.setattr(router.prepared_store, "load_project", full_json)
    load = observe_snapshot_load(monkeypatch, router)

    # Известный отказ запрещает кеш паспорта, но проверенные исходные байты
    # могут восстановить допуск через тот же проверяемый бинарный адаптер.
    assert router.source_coverage(project) == expected
    load.assert_called_once_with(project, project.source_file.native_session)
    read.assert_not_called()
    full_json.assert_not_called()
    assert router.prepared_store.state(project, project.source_file.native_session) == (
        "available"
    )
    assert router.prepared_store.verified_fingerprint(snapshot.key) is not None
    assert not router._prepared


def test_registered_binary_cannot_hide_known_corrupt_source(saved, monkeypatch):
    project, snapshot, store, _, _, _ = saved
    # saved уже построил корректный WKB и паспорт. Портится именно исходник,
    # а не производная база, которую разрешено перестраивать.
    assert list((store.directory / "binary").glob("*.sqlite3"))
    path = store.directory / f"{snapshot.key}.json"
    path.write_bytes(b'{"broken":')
    router = fresh(store)
    router.prepared_store.record_failure(snapshot.key, "corrupt")
    assert router.prepared_store.state(project, project.source_file.native_session) == (
        "corrupt"
    )
    read = Mock(side_effect=AssertionError("Повреждение не заменяется паспортом"))
    monkeypatch.setattr(router._coverage_cache, "read", read)
    full_json = Mock(side_effect=AssertionError("Проверка WKB остаётся потоковой"))
    monkeypatch.setattr(router.prepared_store, "load_project", full_json)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.source_coverage(project)
    assert error.value.state == "corrupt"
    assert router.prepared_store.state(project, project.source_file.native_session) == (
        "corrupt"
    )
    assert router.prepared_store.verified_fingerprint(snapshot.key) is None
    assert not router._coverage_reads and not router._prepared
    assert path.read_bytes() == b'{"broken":'
    read.assert_not_called()
    full_json.assert_not_called()


def test_known_failure_is_bound_to_exact_source_fingerprint(saved, monkeypatch):
    project, snapshot, store, expected, _, _ = saved
    router = fresh(store)
    router.prepared_store.record_failure(snapshot.key, "corrupt")
    session = project.source_file.native_session
    assert router.prepared_store.state(project, session) == "corrupt"
    path = store.directory / f"{snapshot.key}.json"
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    replacement.replace(path)
    # Замена даже теми же байтами снимает только прежнее свидетельство отказа.
    # SHA-проверка паспорта по-прежнему обязательна перед новым допуском.
    assert router.prepared_store.state(project, session) == "available"
    forbid_hydration(monkeypatch, router)
    digest = Mock(wraps=router._coverage_cache._file_digest)
    monkeypatch.setattr(router._coverage_cache, "_file_digest", digest)
    assert router.source_coverage(project) == expected
    digest.assert_called_once_with(snapshot.key)
    assert router.prepared_store.verified_fingerprint(snapshot.key) is None
