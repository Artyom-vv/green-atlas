"""Mapping edits change local meaning without silently preparing CAD again."""

from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn
from unittest.mock import Mock

import pytest
from shapely.geometry import box, mapping
from test_application_boundaries import HistoryReset, ImportReader
from test_native_live_provider import StubNative
from test_native_live_provider import setup as setup
from test_prepared_geometry import local
from test_utility_context import confirmed_context, mapped_project

from app.dxf_import.application import ImportApplication
from app.dxf_import.layers.contracts import LayerKind, LayerMapping
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.contracts import GeometrySnapshot
from app.geometry.networks.utility_contracts import UtilityContext
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.preparation_identity import (
    LEGACY_RULES_REVISIONS,
    legacy_preparation_key,
    preparation_basis,
)
from app.native_query.preparation.prepared_snapshot import (
    PreparedStore,
    prepare_snapshot,
)
from app.native_query.project_runtime import ProjectLiveGeometry
from app.planning.contracts import PlanObject
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.regulations.placement_config import PLACEMENT_CONFIG
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation

NativeSetup = tuple[LiveNativeGeometryEngine, StubNative, Project]


def native_service(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[ImportApplication, Project, ProjectLiveGeometry, PreparedStore, Mock]:
    live, _, project = setup
    project.layers[2].mapped_kind = LayerKind.UTILITY
    project.layers[2].utility_context = UtilityContext.model_validate(
        {
            **confirmed_context().model_dump(),
            "geometry_reference": "outer_surface",
        }
    )
    engine, client, project = local(setup)
    project.map_ready = True
    project.geometry_version = 3
    assert project.plan is not None
    # CC's measured outer surface ends at x=25. The rule file requires 1.5 m
    # for gas and 2 m for water: this point is clear only for the former.
    project.plan.objects = [
        PlanObject(id="tree", kind="tree", x=26.75, y=0, radius=0.5)
    ]
    store = PreparedStore(tmp_path / "prepared")
    store.save(engine.snapshot)
    router = ProjectLiveGeometry(ShapelyGeometryEngine(), prepared_store=store)
    live_engine = Mock(side_effect=AssertionError("No CAD on metadata update"))
    monkeypatch.setattr(router, "live_engine", live_engine)
    validator = RuleBasedPlanValidator(geometry=router)
    validation = PlanValidation(validator, basis_reader=router.validation_input_basis)
    validation.refresh(project, project.plan)
    repository = SqliteProjectRepository(":memory:")
    project = repository.create(project)

    def recheck(current: Project) -> bool:
        if router.prepared_state(current) != "available":
            return False
        assert current.plan is not None
        validation.refresh(current, current.plan)
        return True

    service = ImportApplication(
        repository=repository,
        dxf_reader=ImportReader(),
        history=HistoryReset([]),
        invalidate_spatial=validator.discard,
        now=lambda: datetime.now(UTC),
        recheck_plan=recheck,
        preserve_local_geometry=router.preserve_local_geometry,
    )
    return service, project, router, store, live_engine


@pytest.mark.parametrize("has_plan", [False, True])
@pytest.mark.parametrize("confirmed", [True, None])
@pytest.mark.parametrize(
    "field,value", [("confirmed_by", "New reviewer"), ("network_type", "water")]
)
def test_direct_mapping_loads_current_snapshot_before_local_context_edit(
    setup: NativeSetup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    has_plan: bool,
    confirmed: bool | None,
    field: str,
    value: str,
) -> None:
    service, project, router, store, live_engine = native_service(
        setup, tmp_path, monkeypatch
    )
    snapshot = router._prepared[project.id].snapshot
    assert project.plan is not None
    if confirmed is None:
        project.layers[2].mapping_confirmed = None
        project.plan.validation_basis = None
        project = service.repository.save(project)
        snapshot = prepare_snapshot(setup[0], project)
    assert project.source_file is not None
    assert project.source_file.native_session is not None
    original_key = snapshot.key
    # WKB-адаптер читает исходные байты по требованию. Тестовая копия должна
    # быть получена до удаления файлов, имитирующего холодное открытие.
    original_snapshot = snapshot.model_dump(mode="json", by_alias=True)
    router.discard(project.id)
    for path in store.directory.glob("*.json"):
        path.unlink()
    store.files.save(original_key, original_snapshot)
    original_path = store.directory / f"{original_key}.json"
    original_bytes = original_path.read_bytes()
    if not has_plan:
        project.plan = None
        project = service.repository.save(project)
    assert project.source_file is not None
    assert project.source_file.native_session is not None
    original_basis = preparation_basis(project, project.source_file.native_session)
    assert set(store.directory.glob("*.json")) == {original_path}
    assert not router._prepared

    # Первая локальная правка после открытия: снимок ещё не загружен в кеш движка.
    assert project.layers[2].utility_context is not None
    context = UtilityContext.model_validate(
        {**project.layers[2].utility_context.model_dump(), field: value}
    )
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=project.layers[2].id,
                kind=LayerKind.UTILITY,
                confirmed=confirmed,
                utility_context=context,
            )
        ],
    )

    assert saved.map_ready and saved.geometry == project.geometry
    assert saved.geometry_version == project.geometry_version
    assert router.prepared_state(saved) == "available"
    assert original_path.read_bytes() == original_bytes
    prepared = router.load_local(saved)
    assert isinstance(prepared, PreparedGeometryEngine)
    assert prepared.snapshot.schema_version == "autocad-prepared-geometry/2"
    assert prepared.snapshot.key == original_basis.geometry
    after = prepared.validation_input_basis(saved)
    assert after.geometry_digest == original_basis.geometry
    if field == "confirmed_by":
        assert after.calculation_digest == original_basis.calculation
        assert after.evidence_digest != original_basis.evidence
    else:
        assert after.calculation_digest != original_basis.calculation
    if has_plan:
        assert saved.plan is not None
        assert project.plan is not None
        assert saved.plan.validation_basis is not None
        assert saved.plan.version == project.plan.version
        assert saved.plan.validation_basis.geometry_digest == original_basis.geometry
        if field == "network_type" and confirmed:
            assert any(issue.code == "NATIVE_CLEARANCE" for issue in saved.plan.issues)
    else:
        assert saved.plan is None
    live_engine.assert_not_called()


def test_direct_physical_mapping_does_not_adopt_v1_as_new_physical_basis(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, project, router, store, live_engine = native_service(
        setup, tmp_path, monkeypatch
    )
    snapshot = router._prepared[project.id].snapshot
    assert project.source_file is not None
    assert project.source_file.native_session is not None
    original_key = legacy_preparation_key(
        project, project.source_file.native_session, LEGACY_RULES_REVISIONS[0]
    )
    legacy = snapshot.model_copy(
        update={
            "schema_version": "autocad-prepared-geometry/1",
            "key": original_key,
            "geometry_basis": None,
        }
    )
    router.discard(project.id)
    for path in store.directory.glob("*.json"):
        path.unlink()
    store.files.save(original_key, legacy.model_dump(mode="json", by_alias=True))
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=project.layers[2].id,
                kind=LayerKind.RESTRICTED,
                confirmed=True,
            )
        ],
    )
    assert not saved.map_ready
    assert saved.plan is not None
    assert saved.plan.validation_basis is None
    assert router.prepared_state(saved) != "available"
    assert {path.name for path in store.directory.glob("*.json")} == {
        f"{original_key}.json"
    }
    live_engine.assert_not_called()


def test_native_none_confirmation_requires_new_physical_preparation(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, project, router, store, live_engine = native_service(
        setup, tmp_path, monkeypatch
    )
    initial_key = router._prepared[project.id].snapshot.key
    (store.directory / f"{initial_key}.json").unlink()
    project.layers[2].mapping_confirmed = None
    assert project.plan is not None
    project.plan.validation_basis = None
    project = service.repository.save(project)
    # A historical native None was omitted by native participation policy.
    # Build that exact physical fixture, rather than relabelling a True snapshot.
    before = prepare_snapshot(setup[0], project)
    router.discard(project.id)
    store.save(before)
    original_bytes = (store.directory / f"{before.key}.json").read_bytes()
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=project.layers[2].id,
                kind=LayerKind.UTILITY,
                confirmed=True,
            )
        ],
    )
    assert saved.source_file is not None
    assert saved.source_file.native_session is not None
    assert saved.plan is not None
    assert project.plan is not None
    assert (
        preparation_basis(saved, saved.source_file.native_session).geometry
        != before.key
    )
    assert not saved.map_ready
    assert saved.geometry_version == project.geometry_version + 1
    assert saved.plan.objects == project.plan.objects
    assert saved.plan.issues == project.plan.issues
    assert saved.plan.validation_basis is None
    assert router.prepared_state(saved) != "available"
    assert (store.directory / f"{before.key}.json").read_bytes() == original_bytes
    live_engine.assert_not_called()


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_reference", "Updated survey sheet"),
        ("confirmed_by", "Other reviewer"),
    ],
)
def test_evidence_edit_preserves_physical_and_calculation_basis(
    setup: NativeSetup,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: str,
) -> None:
    service, project, router, _, live_engine = native_service(
        setup, tmp_path, monkeypatch
    )
    # A partial metadata write must preserve a required saved territory without
    # requiring the client to submit or normalize its physical mapping again.
    project.layers[0].required = True
    project = service.repository.save(project)
    assert project.plan is not None
    before = project.plan.validation_basis
    assert before is not None
    assert project.layers[2].utility_context is not None
    context = project.layers[2].utility_context.model_copy(update={field: value})
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=project.layers[2].id,
                kind=LayerKind.UTILITY,
                utility_context=context,
            )
        ],
    )
    assert saved.map_ready
    assert saved.geometry_version == project.geometry_version
    assert saved.geometry == project.geometry
    assert saved.plan is not None
    assert saved.plan.validation_basis is not None
    assert saved.plan.version == project.plan.version
    assert saved.plan.validation_basis.geometry_digest == before.geometry_digest
    assert saved.plan.validation_basis.calculation_digest == before.calculation_digest
    assert saved.plan.validation_basis.evidence_digest != before.evidence_digest
    live_engine.assert_not_called()


def test_semantic_edit_rechecks_saved_plan_with_same_physical_projection(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, project, router, _, live_engine = native_service(
        setup, tmp_path, monkeypatch
    )
    assert project.plan is not None
    before = project.plan.validation_basis
    assert before is not None
    prepared = router.load_local(project)
    assert isinstance(prepared, PreparedGeometryEngine)
    physical_snapshot = prepared.snapshot.model_dump(mode="json", by_alias=True)
    assert not any(issue.severity == "error" for issue in project.plan.issues)
    assert project.layers[2].utility_context is not None
    context = UtilityContext.model_validate(
        {
            **project.layers[2].utility_context.model_dump(),
            "network_type": "water",
        }
    )
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=project.layers[2].id,
                kind=LayerKind.UTILITY,
                utility_context=context,
            )
        ],
    )
    assert saved.map_ready
    assert saved.geometry_version == project.geometry_version
    assert saved.geometry == project.geometry
    prepared = router.load_local(saved)
    assert isinstance(prepared, PreparedGeometryEngine)
    assert prepared.snapshot.model_dump(mode="json", by_alias=True) == physical_snapshot
    assert saved.plan is not None
    assert saved.plan.validation_basis is not None
    conflict = next(
        issue for issue in saved.plan.issues if issue.code == "NATIVE_CLEARANCE"
    )
    assert conflict.actual == pytest.approx(
        1.75 - PLACEMENT_CONFIG.technical.cell_certificate_margin_m
    )
    assert conflict.required == 2
    assert conflict.rule_id == "sp42-9.6-network-water"
    assert saved.plan.validation_basis.geometry_digest == before.geometry_digest
    assert saved.plan.validation_basis.calculation_digest != before.calculation_digest
    assert saved.plan.version == project.plan.version
    live_engine.assert_not_called()


def test_missing_prepared_basis_preserves_plan_as_historical(
    setup: NativeSetup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, project, router, store, live_engine = native_service(
        setup, tmp_path, monkeypatch
    )
    for path in store.directory.glob("*.json"):
        path.unlink()
    assert project.layers[2].utility_context is not None
    context = UtilityContext.model_validate(
        {
            **project.layers[2].utility_context.model_dump(),
            "network_type": "water",
        }
    )
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=project.layers[2].id,
                kind=LayerKind.UTILITY,
                utility_context=context,
            )
        ],
    )
    assert saved.map_ready and saved.geometry == project.geometry
    assert saved.plan is not None
    assert project.plan is not None
    assert saved.plan.objects == project.plan.objects
    assert saved.plan.issues == project.plan.issues
    assert saved.plan.validation_basis is None
    assert saved.geometry_version == project.geometry_version
    live_engine.assert_not_called()


def test_metadata_mapping_does_not_deepcopy_physical_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = mapped_project()
    repository = SqliteProjectRepository(":memory:")
    monkeypatch.setattr(repository, "get", lambda *args, **kwargs: project)
    monkeypatch.setattr(repository, "save", lambda current: current)

    def forbidden(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("Physical geometry must remain shared")

    monkeypatch.setattr(GeometrySnapshot, "__deepcopy__", forbidden)
    service = ImportApplication(
        repository=repository,
        dxf_reader=ImportReader(),
        history=HistoryReset([]),
        invalidate_spatial=lambda project_id: None,
        now=lambda: datetime.now(UTC),
    )
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id="utility",
                kind=LayerKind.UTILITY,
                utility_context=confirmed_context().model_copy(
                    update={"confirmed_by": "Other"}
                ),
            )
        ],
    )
    assert saved.geometry is project.geometry
    assert saved.source_geometry is project.source_geometry
    assert project.layers[1].utility_context is not None
    assert project.layers[1].utility_context.confirmed_by == "Reviewer"


@pytest.mark.parametrize("factory", [ShapelyGeometryEngine, RuleBasedPlanValidator])
def test_compatibility_checker_uses_new_context_without_mutating_saved_map(
    factory: type[ShapelyGeometryEngine] | type[RuleBasedPlanValidator],
) -> None:
    project = mapped_project()
    context = UtilityContext.model_validate(
        {
            **confirmed_context().model_dump(),
            "geometry_reference": "outer_surface",
        }
    )
    project.layers[1].utility_context = context
    assert project.geometry is not None
    feature = project.geometry.feature_collection["features"][1]
    feature["geometry"] = mapping(box(10, 0, 11, 100))
    feature["properties"]["utility_context"] = context.model_dump(mode="json")
    original = project.geometry.model_dump_json()
    engine = factory()
    gas = engine._position_checker(project)
    assert gas.check(12.75, 50, 0.5, "tree") is None
    changed = project.model_copy(
        update={"layers": [layer.model_copy(deep=True) for layer in project.layers]}
    )
    changed.layers[1].utility_context = UtilityContext.model_validate(
        {
            **context.model_dump(),
            "network_type": "water",
        }
    )
    water = engine._position_checker(changed)
    assert water is not gas
    violation = water.check(12.75, 50, 0.5, "tree")
    assert violation is not None and violation.code == "NETWORK_CLEARANCE_FAILED"
    assert changed.geometry is project.geometry
    assert project.geometry.model_dump_json() == original
