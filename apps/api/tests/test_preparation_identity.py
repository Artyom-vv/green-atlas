"""Локальные правки сохраняют физический вход, но не прежнюю политику подготовки."""

import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
from shapely.geometry import Point, shape
from test_hybrid_search import populate
from test_native_live_provider import StubNative, rectangle
from test_native_live_provider import setup as setup

from app.dxf_import.layers.categories import LayerCategory
from app.dxf_import.layers.contracts import LayerKind
from app.geometry.networks.utility_contracts import UtilityContext, UtilityType
from app.layer_recognition.review_policy import local_layer_proposal
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation import preparation_identity
from app.native_query.preparation.coverage_cache import coverage_identity
from app.native_query.preparation.derived_faces import area_layers
from app.native_query.preparation.preparation_identity import (
    LEGACY_RULES_REVISIONS,
    legacy_preparation_key,
    preparation_basis,
)
from app.native_query.preparation.prepared_snapshot import (
    PreparedGeometryUnavailable,
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.projects.contracts import Project

NativeSetup = tuple[LiveNativeGeometryEngine, StubNative, Project]


def network(setup):
    live, client, project = setup
    populate(project)
    project.source_file.native_session = live.session
    layer = project.layers[2]
    layer.mapped_kind = LayerKind.UTILITY
    layer.utility_context = UtilityContext(
        network_type="communication_cable",
        installation="underground",
        geometry_reference="outer_surface",
        review_status="confirmed",
        source_reference="Лист сети",
        confirmed_by="Первый проверяющий",
    )
    project.geometry.feature_collection["features"][2]["geometry"] = {
        "type": "LineString",
        "coordinates": [[20, -10], [20, 10]],
    }
    return live, client, project


def test_prepared_engine_shares_verified_items_and_rebinds_changed_roles(setup):
    live, _, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    record = next(row for row in snapshot.records if row.item.layer == "Здания")
    duplicate = replace(record, item=record.item.model_copy(update={"routes": ("DD",)}))
    snapshot = snapshot.model_copy(update={"records": (*snapshot.records, duplicate)})
    engine = PreparedGeometryEngine(snapshot, project)
    sources = {(item.routes, item.face_id): item for item in engine._source_objects}
    for row in snapshot.records:
        assert sources[(row.item.routes, row.item.face_id)] is row.item
    first = engine.source_participation(record.item, snapshot.key)
    second = engine.source_participation(duplicate.item, snapshot.key)
    assert first is second is record.participation

    project.layers[1].mapped_kind = LayerKind.RESTRICTED
    with pytest.raises(ValueError, match="устарела"):
        engine.assert_current(project)
    engine._bind_source_roles(project)
    changed = engine.source_participation(record.item, snapshot.key)
    assert changed.kind == LayerKind.RESTRICTED
    assert changed != first
    assert record.participation.kind == LayerKind.BUILDING
    assert engine.source_participation(duplicate.item, snapshot.key) is changed


def test_compact_project_read_does_not_hydrate_but_full_read_checks_corruption(
    setup, tmp_path
):
    from app.api import get_project
    from app.composition import create_runtime

    live, _, project = network(setup)
    runtime = create_runtime(tmp_path / "compact.sqlite3")
    try:
        runtime.project_repository.create(project)
        router = runtime.application.geometry
        snapshot = prepare_snapshot(live, project)
        router.prepared_store.save(snapshot)
        path = router.prepared_store.directory / f"{snapshot.key}.json"
        path.write_bytes(b"!" + path.read_bytes()[1:])
        original = runtime.application.projects._prepared_read_status
        strict_read = Mock(wraps=original)
        runtime.application.projects._prepared_read_status = strict_read
        compact = get_project(project.id, False, runtime.application)
        assert compact.prepared_state == "available"
        assert compact.geometry is None
        strict_read.assert_not_called()
        full = get_project(project.id, True, runtime.application)
        assert full.prepared_state == "corrupt"
        assert full.plan == compact.plan
        assert project.id not in router._prepared
        strict_read.assert_called_once()
        with pytest.raises(PreparedGeometryUnavailable):
            router.load_local(runtime.application.get(project.id))
    finally:
        runtime.close()


@pytest.mark.parametrize("tamper", ["key", "basis", "capture", "linear"])
def test_fresh_prepared_save_checks_storage_basis_before_writing(
    setup, tmp_path, tamper
):
    live, _, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    if tamper == "key":
        changed = snapshot.model_copy(update={"key": "f" * 64})
    elif tamper == "basis":
        changed = snapshot.model_copy(
            update={"geometry_basis": {**snapshot.geometry_basis, "factor": 2}}
        )
    elif tamper == "capture":
        changed = snapshot.model_copy(
            update={
                "capture": snapshot.capture.model_copy(
                    update={"units_code": (snapshot.capture.units_code + 1) % 25}
                )
            }
        )
    else:
        changed = snapshot.model_copy(update={"linear_layers": frozenset({"0"})})
    store = PreparedStore(tmp_path / "prepared")
    with pytest.raises(ValueError):
        store.save(changed)
    assert not store.directory.exists()


def test_fresh_capture_uses_verified_written_snapshot_without_reparse(setup, tmp_path):
    live, _, project = network(setup)
    store = PreparedStore(tmp_path / "prepared")
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    router.live_engine = Mock(return_value=live)
    original_load = store.load_project
    store.load_project = Mock(wraps=original_load)
    router._load_or_prepare_capture(project, None)
    assert store.load_project.call_count == 1
    engine = router._prepared[project.id]
    assert store.unchanged(engine.snapshot.key)
    assert router.load_local(project) is engine
    cold = ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(store.directory))
    assert cold.load_local(project).snapshot.model_dump(mode="json") == (
        engine.snapshot.model_dump(mode="json")
    )


def test_written_snapshot_change_during_engine_construction_is_not_cached(
    setup, tmp_path, monkeypatch
):
    live, _, project = network(setup)
    store = PreparedStore(tmp_path / "prepared")
    snapshot = prepare_snapshot(live, project)
    store.save(snapshot)
    path = store.directory / f"{snapshot.key}.json"
    original_bytes = path.read_bytes()
    if os.name == "nt":
        # Детерминированно воспроизводим подтверждённое совпадение обоих времён
        # после быстрой записи: правильность должна опираться на проверенные байты.
        observed = store._fingerprint(snapshot.key)
        monkeypatch.setattr(store, "_fingerprint", lambda _: observed)

    def changed_after_construction(*args, **kwargs):
        engine = PreparedGeometryEngine(*args, **kwargs)
        path.write_bytes(b"!" + path.read_bytes()[1:])
        return engine

    monkeypatch.setattr(
        "app.native_query.project_runtime.PreparedGeometryEngine",
        changed_after_construction,
    )
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    with pytest.raises(PreparedGeometryUnavailable):
        router._cache_snapshot(project, snapshot, None)
    assert project.id not in router._prepared
    assert not store.unchanged(snapshot.key)

    if os.name == "nt":
        assert store.state(project, live.session) == "corrupt"
        path.write_bytes(original_bytes)
        # Отпечаток всё ещё принудительно прежний: восстановление различает SHA.
        assert store.state(project, live.session) == "available"
        monkeypatch.undo()
        assert store.load_project(project, live.session) is not None
        assert store.unchanged(snapshot.key)


def test_written_snapshot_checksum_failure_is_not_available(setup, tmp_path):
    live, _, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path / "prepared")
    store.files.matches_saved = Mock(return_value=False)
    with pytest.raises(PreparedGeometryUnavailable):
        store.save(snapshot)
    assert store.state(project, live.session) == "corrupt"
    assert not store.unchanged(snapshot.key)


def test_review_provenance_does_not_change_geometry_or_rules(setup):
    live, _, project = network(setup)
    before = preparation_basis(project, live.session)
    project.layers[2].utility_context.confirmed_by = "Другой проверяющий"
    after = preparation_basis(project, live.session)
    assert after.geometry == before.geometry
    assert after.calculation == before.calculation
    assert after.evidence != before.evidence


def test_growth_scenario_reuses_prepared_geometry_and_derived_caches(setup, tmp_path):
    live, client, project = network(setup)
    assert project.plan is not None
    project.plan.planning_horizon_year = 20
    before = project.model_dump_json()
    store = PreparedStore(tmp_path / "prepared")
    store.save(prepare_snapshot(live, project))
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    router.live_engine = Mock(side_effect=AssertionError("Нет скрытой подготовки CAD"))
    engine = router.load_local(project)
    engine.position_violation(project, 20.85, 0, 0.5, "shrub")
    engine.automatic_safe_geometry(project, rectangle(20.5, -4, 28, 4), 0.5, "shrub")
    engine.source_coverage(project)
    bounds, hybrid, coverage = (
        engine._bounds_index_cache,
        engine._hybrid,
        engine._coverage,
    )
    measurements = dict(engine._cache)
    basis = preparation_basis(project, live.session)
    coverage_key = coverage_identity(project, engine.linear_layers)

    for year in (0, 1, 5, 20, 40):
        scenario = project.model_copy(
            update={
                "plan": project.plan.model_copy(update={"planning_horizon_year": year})
            }
        )
        assert preparation_basis(scenario, live.session) == basis
        assert coverage_identity(scenario, engine.linear_layers) == coverage_key
        assert store.state(scenario, live.session) == "available"
        assert router.load_local(scenario) is engine
        assert engine._bounds_index_cache is bounds
        assert engine._hybrid is hybrid
        assert engine._coverage is coverage
        assert engine._cache == measurements

    assert project.model_dump_json() == before
    router.live_engine.assert_not_called()
    assert not client.calls


@pytest.mark.parametrize("role", [LayerKind.RESTRICTED, LayerKind.UTILITY])
def test_old_casing_name_without_explicit_category_keeps_geometry_basis(
    setup: NativeSetup,
    role: LayerKind,
) -> None:
    live, _, project = setup
    layer = project.layers[1]
    layer.source_name = "ЭС_Футляры"
    layer.mapped_kind = role
    layer.entity_types = layer.projected_geometry_types = {"REGION": 1}
    before = preparation_basis(project, live.session)
    eligible = area_layers({layer.source_name: layer})
    assert (layer.source_name in eligible) == (role == LayerKind.RESTRICTED)
    assert local_layer_proposal(layer).category == LayerCategory.PROTECTIVE_CASING
    assert layer.category is None and layer.mapped_kind == role
    assert preparation_basis(project, live.session) == before
    assert area_layers({layer.source_name: layer}) == eligible


def test_explicit_casing_category_requires_new_snapshot_without_silent_reuse(
    setup: NativeSetup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live, _, project = setup
    populate(project)
    assert project.source_file is not None
    project.source_file.native_session = live.session
    layer = project.layers[1]
    layer.mapped_kind = LayerKind.RESTRICTED
    old = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path)
    store.save(old)
    old_path = tmp_path / f"{old.key}.json"
    old_bytes = old_path.read_bytes()
    before = preparation_basis(project, live.session)
    layer.category = LayerCategory.PROTECTIVE_CASING
    after = preparation_basis(project, live.session)
    assert after.geometry != before.geometry
    assert layer.source_name not in area_layers({layer.source_name: layer})
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    live_engine = Mock(side_effect=AssertionError("Нет скрытой подготовки CAD"))
    monkeypatch.setattr(router, "live_engine", live_engine)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.load_local(project)
    assert error.value.state == "stale"
    assert old_path.read_bytes() == old_bytes
    assert project.plan is not None
    live_engine.assert_not_called()


def test_network_correction_reuses_projection_but_rebuilds_point_and_domain(
    setup, tmp_path
):
    live, client, project = network(setup)
    store = PreparedStore(tmp_path)
    store.save(prepare_snapshot(live, project))
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    router.live_engine = Mock(
        side_effect=AssertionError("No CAD for network correction")
    )
    engine = router.load_local(project)
    projection = engine.projection
    assert engine.position_violation(project, 20.85, 0, 0.5, "shrub") is None
    old_domain = engine.automatic_safe_geometry(
        project, rectangle(20.5, -4, 28, 4), 0.5, "shrub"
    )
    assert shape(old_domain).covers(Point(20.85, 0))
    old_calculation = engine._basis_key
    project.layers[2].utility_context.network_type = UtilityType.HEAT
    updated = router.load_local(project)
    assert updated.projection is projection
    assert updated._basis_key != old_calculation
    assert (
        updated.position_violation(project, 20.85, 0, 0.5, "shrub").code
        == "NATIVE_CLEARANCE"
    )
    new_domain = updated.automatic_safe_geometry(
        project, rectangle(20.5, -4, 28, 4), 0.5, "shrub"
    )
    assert not shape(new_domain).covers(Point(20.85, 0))
    router.live_engine.assert_not_called()
    assert not client.calls


@pytest.mark.parametrize("action", ["calculate", "source_coverage", "point", "explain"])
def test_cache_miss_is_addressed_without_starting_autocad(setup, tmp_path, action):
    live, _, project = network(setup)
    router = ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(tmp_path))
    router.live_engine = Mock(side_effect=AssertionError("Implicit CAD request"))
    with pytest.raises(PreparedGeometryUnavailable, match="план доступен"):
        if action == "point":
            router.position_violation(project, 23, 0, 0.5)
        elif action == "explain":
            router.explain_position(project, 23, 0, 0.5, "tree")
        else:
            getattr(router, action)(project)
    router.live_engine.assert_not_called()
    assert project.plan is not None


def test_only_explicit_preparation_contacts_cad(setup, tmp_path):
    live, client, project = network(setup)
    router = ProjectLiveGeometry(Mock(), prepared_store=PreparedStore(tmp_path))
    router.live_engine = Mock(return_value=live)
    router.prepare_current_capture(project)
    router.live_engine.assert_called_once_with(project)
    client.stale = True
    router.position_violation(project, 23, 0, 0.5)
    assert router.prepared_state(project) == "available"


@pytest.mark.parametrize("rules", LEGACY_RULES_REVISIONS)
def test_exact_known_legacy_key_requires_explicit_preparation(
    setup: NativeSetup, tmp_path: Path, rules: str
) -> None:
    live, _, project = network(setup)
    current = prepare_snapshot(live, project)
    old_key = legacy_preparation_key(project, live.session, rules)
    legacy = current.model_copy(
        update={
            "schema_version": "autocad-prepared-geometry/1",
            "key": old_key,
            "geometry_basis": None,
        }
    )
    store = PreparedStore(tmp_path)
    store.files.save(old_key, legacy.model_dump(mode="json", by_alias=True))
    old_bytes = (tmp_path / f"{old_key}.json").read_bytes()
    with pytest.raises(PreparedGeometryUnavailable) as error:
        store.load_project(project, live.session)
    assert error.value.state == "unsupported"
    assert (tmp_path / f"{old_key}.json").read_bytes() == old_bytes
    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{old_key}.json"]
    assert not store.unchanged(current.key)


def test_previous_geometry_policy_requires_explicit_preparation(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live, _, project = network(setup)
    with monkeypatch.context() as previous:
        previous.setattr(
            preparation_identity,
            "PREPARATION_POLICY_REVISION",
            "category-aware-groups/2",
        )
        old = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path)
    store.save(old)
    old_path = tmp_path / f"{old.key}.json"
    old_bytes = old_path.read_bytes()
    assert project.plan is not None
    old_plan = project.plan.model_copy(deep=True)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    live_engine = Mock(return_value=live)
    monkeypatch.setattr(router, "live_engine", live_engine)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.load_local(project)
    assert error.value.state == "stale"
    assert router.prepared_state(project) == "stale"
    assert project.plan == old_plan
    assert old_path.read_bytes() == old_bytes
    live_engine.assert_not_called()
    router.prepare_current_capture(project)
    assert router.prepared_state(project) == "available"
    prepared = router.load_local(project)
    assert isinstance(prepared, PreparedGeometryEngine)
    assert prepared.snapshot.key != old.key
    assert old_path.read_bytes() == old_bytes
    assert project.plan == old_plan
    live_engine.assert_called_once_with(project)


def test_opening_multiple_projects_bounds_projection_cache_and_reloads_locally(
    setup, tmp_path
):
    live, _, project = network(setup)
    store = PreparedStore(tmp_path)
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    projects = [
        project.model_copy(deep=True, update={"id": f"saved-{i}"}) for i in range(3)
    ]
    for current in projects:
        store.save(prepare_snapshot(live, current))
    first = router.load_local(projects[0])
    assert router.load_local(projects[0]) is first
    router.load_local(projects[1])
    # Выполнявшийся запрос удерживает своё основание; кеш следующего проекта
    # не удерживает его дополнительно. Повторное открытие читает тот же снимок.
    assert first.prepared_state(projects[0]) == "available"
    reopened = router.load_local(projects[0])
    assert reopened is not first
    assert reopened.snapshot.model_dump_json() == first.snapshot.model_dump_json()
    router.load_local(projects[2])
    assert projects[1].id not in router._prepared
    assert len(router._prepared) == 1
    assert router.load_local(projects[1]).prepared_state(projects[1]) == "available"
    assert len(router._prepared) == 1


def test_same_capture_with_changed_legacy_metadata_is_not_adopted(setup, tmp_path):
    live, _, project = network(setup)
    old_key = legacy_preparation_key(project, live.session, LEGACY_RULES_REVISIONS[0])
    legacy = prepare_snapshot(live, project).model_copy(
        update={
            "schema_version": "autocad-prepared-geometry/1",
            "key": old_key,
            "geometry_basis": None,
        }
    )
    store = PreparedStore(tmp_path)
    store.files.save(old_key, legacy.model_dump(mode="json", by_alias=True))
    project.layers[2].utility_context.confirmed_by = "Изменён до миграции"
    with pytest.raises(PreparedGeometryUnavailable):
        store.load_project(project, live.session)


def test_corrupt_projection_is_addressed_and_repair_preserves_original_bytes(
    setup, tmp_path
):
    live, _, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path)
    first = replace(
        snapshot.records[0], geometries=({"type": "Polygon", "coordinates": []},)
    )
    corrupt = snapshot.model_copy(update={"records": (first, *snapshot.records[1:])})
    store.files.save(corrupt.key, corrupt.model_dump(mode="json", by_alias=True))
    original = (tmp_path / f"{snapshot.key}.json").read_bytes()
    router = ProjectLiveGeometry(Mock(), prepared_store=store)
    with pytest.raises(PreparedGeometryUnavailable) as error:
        router.load_local(project)
    assert error.value.state == "corrupt"
    assert router.prepared_state(project) == "corrupt"
    # A well-formed envelope cannot hide an invalid projection from save repair.
    store.save(snapshot)
    assert list(tmp_path.glob("*.damaged"))[0].read_bytes() == original
    assert router.load_local(project).position_violation(project, 23, 0, 0.5) is None
    assert original


def test_valid_prepared_file_is_immutable_even_if_same_key_is_reused(setup, tmp_path):
    live, _, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path)
    store.save(snapshot)
    original = (tmp_path / f"{snapshot.key}.json").read_bytes()
    with pytest.raises(ValueError, match="Конфликт"):
        store.save(snapshot.model_copy(update={"records": ()}))
    assert (tmp_path / f"{snapshot.key}.json").read_bytes() == original


@pytest.mark.parametrize("field", ["capture", "factor", "linear_layers"])
def test_prepared_metadata_must_match_its_geometry_preimage(setup, tmp_path, field):
    live, _, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    replacement = {
        "capture": snapshot.capture.model_copy(update={"snapshot_sha256": "f" * 64}),
        "factor": 1000,
        "linear_layers": frozenset({"Injected"}),
    }[field]
    store = PreparedStore(tmp_path)
    invalid = snapshot.model_copy(update={field: replacement})
    store.files.save(invalid.key, invalid.model_dump(mode="json", by_alias=True))
    with pytest.raises(PreparedGeometryUnavailable) as error:
        store.load_project(project, live.session)
    assert error.value.state == "corrupt"


def test_changed_projection_tolerance_requires_new_preparation(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.regulations.placement_config import PLACEMENT_CONFIG

    live, _, project = network(setup)
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path)
    store.save(snapshot)
    original_path = tmp_path / f"{snapshot.key}.json"
    original_bytes = original_path.read_bytes()
    changed_policy = PLACEMENT_CONFIG.hybrid_search.model_copy(
        update={"projection_area_relative_tolerance": 0.002}
    )
    monkeypatch.setattr(
        preparation_identity,
        "PLACEMENT_CONFIG",
        PLACEMENT_CONFIG.model_copy(update={"hybrid_search": changed_policy}),
    )
    with pytest.raises(PreparedGeometryUnavailable) as error:
        store.load_project(project, live.session)
    assert error.value.state == "stale"
    assert original_path.read_bytes() == original_bytes


def test_project_read_checks_corruption_before_editing_and_keeps_plan(setup, tmp_path):
    from app.composition import create_runtime

    live, _, project = network(setup)
    project.map_ready = True
    runtime = create_runtime(tmp_path / "open.sqlite3")
    try:
        runtime.project_repository.create(project)
        router = runtime.application.geometry
        router.live_engine = Mock(
            side_effect=AssertionError("No AutoCAD while opening")
        )
        snapshot = prepare_snapshot(live, project)
        router.prepared_store.save(snapshot)
        corrupt = snapshot.model_copy(
            update={
                "records": (
                    replace(
                        snapshot.records[0],
                        geometries=({"type": "Polygon", "coordinates": []},),
                    ),
                    *snapshot.records[1:],
                )
            }
        )
        router.prepared_store.files.save(
            corrupt.key, corrupt.model_dump(mode="json", by_alias=True)
        )
        saved = runtime.application.get(project.id, lightweight=True)
        assert (
            runtime.application.projects.list_projects()[0].resume_kind == "workspace"
        )
        read = runtime.application.projects.read_workspace(saved)
        assert read.prepared_state == "corrupt"
        assert read.plan == saved.plan
        router.live_engine.assert_not_called()
    finally:
        runtime.close()


def test_warm_project_read_detects_changed_file_and_recovers_without_cad(
    setup, tmp_path
):
    from app.composition import create_runtime

    live, client, project = network(setup)
    project.map_ready = True
    runtime = create_runtime(tmp_path / "warm-open.sqlite3")
    try:
        runtime.project_repository.create(project)
        router = runtime.application.geometry
        router.live_engine = Mock(side_effect=AssertionError("No CAD while reopening"))
        snapshot = prepare_snapshot(live, project)
        router.prepared_store.save(snapshot)
        saved = runtime.application.get(project.id, lightweight=True)
        first = runtime.application.projects.read_workspace(saved)
        assert first.prepared_state == "available"
        assert project.id in router._prepared

        path = router.prepared_store.directory / f"{snapshot.key}.json"
        original = path.read_bytes()
        previous_stat = path.stat()
        # Same length and restored mtime must not conceal changed durable input.
        path.write_bytes(b"!" + original[1:])
        os.utime(path, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
        assert path.stat().st_size == previous_stat.st_size
        assert path.stat().st_mtime_ns == previous_stat.st_mtime_ns
        damaged = runtime.application.projects.read_workspace(saved)
        assert damaged.prepared_state == "corrupt"
        assert damaged.plan == first.plan
        assert project.id not in router._prepared
        assert (
            runtime.application.projects.list_projects()[0].resume_kind == "workspace"
        )

        path.write_bytes(original)
        repaired = runtime.application.projects.read_workspace(saved)
        assert repaired.prepared_state == "available"
        assert repaired.plan == first.plan
        router.live_engine.assert_not_called()
        assert not client.calls
    finally:
        runtime.close()


def test_local_workspace_roundtrip_retains_plan_history_and_all_three_bases(
    setup, tmp_path
):
    from app.composition import create_runtime
    from app.dxf_import.layers.contracts import LayerMapping
    from app.planning.changes.contracts import (
        PlanChangeSetApplyRequest,
        PlanChangeSetDraft,
    )

    live, client, project = network(setup)
    project.map_ready = True
    database = tmp_path / "roundtrip.sqlite3"
    runtime = create_runtime(database)
    try:
        runtime.project_repository.create(project)
        router = runtime.application.geometry
        router.prepared_store.save(prepare_snapshot(live, project))
        router.live_engine = Mock(side_effect=AssertionError("CAD closed"))
        layer = project.layers[2]
        context = layer.utility_context.model_copy(
            update={"confirmed_by": "Новый проверяющий"}
        )
        corrected = runtime.application.save_mappings(
            project.id,
            [
                LayerMapping(
                    layer_id=layer.id,
                    kind=layer.mapped_kind,
                    confirmed=True,
                    utility_context=context,
                )
            ],
        )
        assert corrected.map_ready
        assert corrected.geometry_version == project.geometry_version
        assert corrected.geometry == project.geometry
        preview = runtime.application.preview_change_set(
            project.id,
            PlanChangeSetDraft(
                base_plan_version=corrected.plan.version,
                label="Сохранённая посадка",
                operations=[
                    {
                        "type": "add",
                        "object": {"kind": "shrub", "x": 50, "y": 1.25, "radius": 0.5},
                    }
                ],
            ),
        )
        assert preview.can_apply
        request = PlanChangeSetApplyRequest(
            preview_id=preview.id,
            digest=preview.digest,
            base_plan_version=preview.base_plan_version,
        )
        applied = runtime.application.apply_change_set(project.id, request)
        committed = runtime.application.get(project.id)
        expected_plan = committed.plan.model_dump(mode="json")
        expected_history = runtime.application.get_plan_history(project.id).model_dump(
            mode="json"
        )
        assert all(
            expected_plan["validation_basis"][name]
            for name in (
                "geometry_digest",
                "calculation_digest",
                "evidence_digest",
            )
        )
        assert applied.plan.objects[0].x == 50
        router.live_engine.assert_not_called()
    finally:
        runtime.close()
    restored = create_runtime(database)
    try:
        restored.application.geometry.live_engine = Mock(
            side_effect=AssertionError("No CAD after restart")
        )
        read = restored.application.projects.read_workspace(
            restored.application.get(project.id, lightweight=True)
        )
        assert read.resume_kind == "workspace"
        assert read.prepared_state == "available"
        assert read.plan.model_dump(mode="json") == expected_plan
        assert (
            restored.application.get_plan_history(project.id).model_dump(mode="json")
            == expected_history
        )
        receipt = restored.application.get_applied_change_set_receipt(
            project.id,
            request.preview_id,
            request.digest,
            request.base_plan_version,
            corrected.state_version,
        )
        assert receipt["status"] == "applied"
        assert read.layers[2].utility_context.confirmed_by == "Новый проверяющий"
        restored.application.geometry.position_violation(read, 50, 1.25, 0.5, "shrub")
        restored.application.geometry.live_engine.assert_not_called()
        assert not client.calls
    finally:
        restored.close()


@pytest.mark.skipif(os.name != "nt", reason="Проверка удержанного Windows HANDLE")
def test_windows_prepared_read_scope_pins_bytes_once_and_releases_on_error(
    setup, tmp_path, monkeypatch
):
    from app.desktop import tickets
    from app.native_query.transport import windows_job_files

    live, _, project = network(setup)
    store = PreparedStore(tmp_path / "prepared")
    snapshot = prepare_snapshot(live, project)
    store.save(snapshot)
    path = store.directory / f"{snapshot.key}.json"
    original_bytes = path.read_bytes()
    observed = {"bytes": 0, "directory_scans": 0}
    scandir = windows_job_files.os.scandir

    def counted_scandir(directory):
        if isinstance(directory, (str, Path)) and Path(directory) == path.parent:
            observed["directory_scans"] += 1
        return scandir(directory)

    monkeypatch.setattr(windows_job_files.os, "scandir", counted_scandir)
    open_regular = tickets.read_regular

    class ObservedRead:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def read(self, size=-1):
            value = self.stream.read(size)
            observed["bytes"] += len(value)
            return value

        def readinto(self, buffer):
            size = self.stream.readinto(buffer)
            observed["bytes"] += size
            return size

    def counted_open(candidate):
        stream = open_regular(candidate)
        return ObservedRead(stream) if candidate == path else stream

    monkeypatch.setattr(tickets, "read_regular", counted_open)
    with store.read_scope(snapshot.key):
        scans_at_entry = observed["directory_scans"]
        for _ in range(100):
            assert store.unchanged(snapshot.key)
        with store.read_scope(snapshot.key):
            assert store.unchanged(snapshot.key)
        assert observed["directory_scans"] == scans_at_entry
        with pytest.raises(PermissionError):
            path.write_bytes(b"!" + original_bytes[1:])
    # Сто проверок и вложенная порция читают источник для SHA только один раз.
    assert observed["bytes"] == len(original_bytes)
    assert path.read_bytes() == original_bytes

    with pytest.raises(ValueError, match="scope-failure"):
        with store.read_scope(snapshot.key):
            raise ValueError("scope-failure")
    # Исключение не оставляет HANDLE или право повторного использования порции.
    path.write_bytes(b"!" + original_bytes[1:])
    assert not store.unchanged(snapshot.key)
