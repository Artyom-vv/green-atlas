"""Zone-only writes reuse a proven plan without rechecking unrelated CAD points."""

import os
from copy import deepcopy
from unittest.mock import Mock

import pytest
from application_factory import recompose_application
from shapely.geometry import box, mapping
from test_native_point_check_projection import prepared_runtime as prepared_runtime
from test_native_point_check_projection import setup as setup
from test_planting_zone_changes import draft, seeded

from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.checking.domain import PositionChecker, PositionViolation
from app.history.application import history_basis
from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
from app.native_query.preparation.prepared_snapshot import PreparedGeometryUnavailable
from app.planning.contracts import PlanObject
from app.planting_zones.change_contracts import ZoneChangeResult, ZoneSnapshot
from app.planting_zones.domain import attach_planting_zone_features
from app.projects.concurrency import (
    ProjectVersionConflict,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason
from app.species.contracts import GrowthEnvelopeForecast
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.contracts import CalculationInputBasis, ValidationIssue


def _full_result(project: Project) -> tuple[list, list]:
    reference = project.model_copy(deep=True)
    assert reference.plan is not None
    issues = RuleBasedPlanValidator().validate_plan(reference, reference.plan)
    return issues, reference.plan.objects


@pytest.mark.parametrize("operation", ["rename", "create", "resize", "delete"])
def test_current_zone_only_write_does_not_recheck_unaffected_plantings(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    app, project = seeded()
    assert project.plan is not None
    original_plan = project.plan.model_copy(deep=True)
    assert app.validation.is_current(project, project.plan)
    if operation == "rename":
        zones = draft(project)
    elif operation == "resize":
        zones = draft(project, geometry=mapping(box(410, 10, 490, 90)))
    else:
        zones = draft(project, operation)
    full = Mock(wraps=app.validator.validate_plan)
    monkeypatch.setattr(app.validator, "validate_plan", full)
    checks = []
    check = PositionChecker.check

    def checked(self: PositionChecker, *args, **kwargs):
        checks.append((args, kwargs))
        return check(self, *args, **kwargs)

    monkeypatch.setattr(PositionChecker, "check", checked)
    saved = app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    full.assert_not_called()
    assert checks == []
    assert saved.plan is not None
    assert saved.plan.objects == original_plan.objects
    assert saved.plan.issues == original_plan.issues
    assert saved.plan.id == original_plan.id
    assert saved.plan.version == original_plan.version
    assert saved.state_version == project.state_version + 1
    assert saved.geometry_version == project.geometry_version + 1
    assert app.validation.is_current(saved, saved.plan)


@pytest.mark.parametrize(
    "changed_input", ["missing_basis", "objects", "geometry_version", "rules"]
)
def test_zone_write_falls_back_when_saved_plan_check_is_stale(
    monkeypatch: pytest.MonkeyPatch, changed_input: str
) -> None:
    app, project = seeded()
    assert project.plan is not None
    if changed_input == "missing_basis":
        project.plan.validation_basis = None
    elif changed_input == "objects":
        project.plan.objects[0].x += 10
    elif changed_input == "geometry_version":
        project.geometry_version += 1
    else:
        monkeypatch.setattr(app.validator, "revision", "changed-rule-revision")
    project = app.repository.save(project)
    assert not app.validation.is_current(project, project.plan)
    full = Mock(wraps=app.validator.validate_plan)
    monkeypatch.setattr(app.validator, "validate_plan", full)
    saved = app.zones.save_planting_zones(
        project.id, draft(project), preserve_plan=True
    )
    full.assert_called_once()
    assert saved.plan is not None
    assert app.validation.is_current(saved, saved.plan)


@pytest.mark.parametrize(
    "digest", ["geometry_digest", "calculation_digest", "evidence_digest"]
)
def test_zone_rename_does_not_reuse_check_from_changed_source_or_policy(
    monkeypatch: pytest.MonkeyPatch, digest: str
) -> None:
    app, project = seeded()
    assert project.plan is not None
    before = CalculationInputBasis(
        geometry_digest="a" * 64,
        calculation_digest="b" * 64,
        evidence_digest="c" * 64,
    )
    basis = [before]
    app.validation.basis_reader = lambda _: basis[0]
    app.validation.refresh(project, project.plan)
    project = app.repository.save(project)
    basis[0] = before.model_copy(update={digest: "d" * 64})
    assert not app.validation.is_current(project, project.plan)
    full = Mock(wraps=app.validator.validate_plan)
    monkeypatch.setattr(app.validator, "validate_plan", full)
    saved = app.zones.save_planting_zones(
        project.id, draft(project), preserve_plan=True
    )
    full.assert_called_once()
    assert saved.plan is not None
    assert app.validation.is_current(saved, saved.plan)


def test_expanding_working_area_refreshes_membership_status_and_matches_full_check() -> (
    None
):
    app, project = seeded()
    assert project.plan is not None
    project.plan.objects = [
        PlanObject(id="outside-work-area", kind="shrub", x=250, y=50, radius=0.5)
    ]
    app.validation.refresh(project, project.plan)
    assert {issue.code for issue in project.plan.issues} == {
        "PLANTING_ZONE",
        "SPECIES_UNASSIGNED",
        "NO_NETWORK_FEATURES",
    }
    assert project.plan.objects[0].status == "error"
    project = app.repository.save(project)
    original_objects = [
        item.model_dump(exclude={"status"}) for item in project.plan.objects
    ]
    zones = draft(project, "create", geometry=mapping(box(220, 20, 280, 80)))
    saved = app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    assert saved.plan is not None
    assert {issue.code for issue in saved.plan.issues} == {
        "SPECIES_UNASSIGNED",
        "NO_NETWORK_FEATURES",
    }
    assert saved.plan.objects[0].status == "warning"
    assert [
        item.model_dump(exclude={"status"}) for item in saved.plan.objects
    ] == original_objects
    issues, objects = _full_result(saved)
    assert saved.plan.issues == issues
    assert saved.plan.objects == objects
    assert app.validation.is_current(saved, saved.plan)


@pytest.mark.parametrize("operation", ["resize", "delete"])
def test_shrinking_or_deleting_covering_area_refreshes_unassigned_planting_membership(
    operation: str,
) -> None:
    app, project = seeded()
    assert project.plan is not None
    project.plan.objects = [
        PlanObject(id="unassigned-in-east", kind="shrub", x=450, y=50, radius=0.5)
    ]
    app.validation.refresh(project, project.plan)
    project = app.repository.save(project)
    assert not any(issue.code == "PLANTING_ZONE" for issue in project.plan.issues)
    zones = (
        draft(project, "delete")
        if operation == "delete"
        else draft(project, geometry=mapping(box(400, 0, 430, 100)))
    )
    saved = app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    assert saved.plan is not None
    assert any(issue.code == "PLANTING_ZONE" for issue in saved.plan.issues)
    assert saved.plan.objects[0].status == "error"
    issues, objects = _full_result(saved)
    assert saved.plan.issues == issues
    assert saved.plan.objects == objects


def test_adding_area_unmasks_obstacle_instead_of_dropping_previous_membership_error() -> (
    None
):
    app, project = seeded()
    assert project.geometry is not None and project.plan is not None
    project.geometry.feature_collection["features"].append(
        {
            "type": "Feature",
            "id": "water-under-new-area",
            "properties": {"kind": "water"},
            "geometry": mapping(box(245, 45, 255, 55)),
        }
    )
    project.geometry_version += 1
    project.plan.objects = [
        PlanObject(id="covered-obstacle", kind="shrub", x=250, y=50, radius=0.5)
    ]
    app.validation.refresh(project, project.plan)
    assert any(issue.code == "PLANTING_ZONE" for issue in project.plan.issues)
    project = app.repository.save(project)
    saved = app.zones.save_planting_zones(
        project.id,
        draft(project, "create", geometry=mapping(box(220, 20, 280, 80))),
        preserve_plan=True,
    )
    assert saved.plan is not None
    assert not any(issue.code == "PLANTING_ZONE" for issue in saved.plan.issues)
    assert any(issue.code == "WATER_OVERLAP" for issue in saved.plan.issues)
    issues, objects = _full_result(saved)
    assert saved.plan.issues == issues
    assert saved.plan.objects == objects
    assert saved.plan.objects[0].status == "error"


def test_zone_reuse_keeps_new_area_across_undo_redo_without_false_current_basis() -> (
    None
):
    app, project = seeded()
    assert project.plan is not None
    before = history_basis(project)
    project.plan.objects[0].x += 10
    app.validation.refresh(project, project.plan, increment_version=True)
    moved = app.history_application.commit(project, before, "Move planting", "moved")
    zones = draft(moved, "create")
    saved = app.zones.save_planting_zones(moved.id, zones, preserve_plan=True)
    assert saved.plan is not None and moved.plan is not None
    assert app.validation.is_current(saved, saved.plan)
    undone = app.undo_plan_change(saved.id)
    assert undone.planting_zones == zones
    assert undone.plan is not None and before.plan is not None
    assert undone.plan.objects == before.plan.objects
    assert undone.plan.version > saved.plan.version
    assert undone.state_version == saved.state_version + 1
    if app.validation.is_current(undone, undone.plan):
        assert undone.plan.issues == _full_result(undone)[0]
    redone = app.redo_plan_change(saved.id)
    assert redone.planting_zones == zones
    assert redone.plan is not None
    assert redone.plan.objects == moved.plan.objects
    assert redone.plan.version > undone.plan.version
    assert redone.state_version == undone.state_version + 1
    if app.validation.is_current(redone, redone.plan):
        assert redone.plan.issues == _full_result(redone)[0]


def test_failed_zone_compare_and_swap_never_persists_a_future_validation_basis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = seeded()
    before = deepcopy(project.plan)
    original = app.repository.save

    def concurrent_write(candidate: Project, **kwargs):
        token = set_expected_project_version(None)
        try:
            concurrent = app.get(project.id)
            concurrent.name = "Winning concurrent write"
            original(concurrent)
        finally:
            reset_expected_project_version(token)
        return original(candidate, **kwargs)

    monkeypatch.setattr(app.repository, "save", concurrent_write)
    with pytest.raises(ProjectVersionConflict):
        app.zones.save_planting_zones(
            project.id, draft(project, "create"), preserve_plan=True
        )
    current = app.get(project.id)
    assert current.name == "Winning concurrent write"
    assert current.planting_zones == project.planting_zones
    assert current.plan == before
    assert current.plan is not None
    assert app.validation.is_current(current, current.plan)


def test_expanding_area_rechecks_growth_envelope_even_when_center_was_already_allowed() -> (
    None
):
    app, project = seeded()
    assert project.plan is not None
    forecast = GrowthEnvelopeForecast(
        horizon_year=0,
        radius_min_m=3,
        radius_max_m=3,
        confidence="high",
        basis="Test forecast",
    )
    project.plan.objects = [
        PlanObject(
            id="future-crosses-edge",
            kind="tree",
            x=198,
            y=50,
            radius=0.5,
            planting_zone_id="west",
            species_revision_id="tilia-cordata@2026-08-28.1",
            canopy_forecast=[forecast],
            root_forecast=[forecast],
        )
    ]
    app.validation.refresh(project, project.plan)
    assert any(issue.code == "GROWTH_PLANTING_ZONE" for issue in project.plan.issues)
    assert not any(issue.code == "PLANTING_ZONE" for issue in project.plan.issues)
    project = app.repository.save(project)
    saved = app.zones.save_planting_zones(
        project.id,
        draft(project, "create", geometry=mapping(box(0, 0, 300, 200))),
        preserve_plan=True,
    )
    assert saved.plan is not None
    assert not any(issue.code == "GROWTH_PLANTING_ZONE" for issue in saved.plan.issues)
    issues, objects = _full_result(saved)
    assert saved.plan.issues == issues
    assert saved.plan.objects == objects


def test_prepared_native_zone_addition_does_not_recheck_cad_points(
    prepared_runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime, router, _, created, _, forbidden = prepared_runtime
    app = runtime.application
    project = app.repository.get(created.id)
    assert project.plan is not None
    app.validation.refresh(project, project.plan)
    project = app.repository.save(project)
    before = project.plan.model_copy(deep=True)
    full = Mock(wraps=app.validator.validate_plan)
    monkeypatch.setattr(app.validator, "validate_plan", full)
    query = Mock(wraps=router.position_violation)
    trace = Mock(wraps=router.position_rule_trace)
    monkeypatch.setattr(router, "position_violation", query)
    monkeypatch.setattr(router, "position_rule_trace", trace)
    zones = draft(project, "create", geometry=mapping(box(35, 30, 80, 70)))
    saved = app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    full.assert_not_called()
    query.assert_not_called()
    trace.assert_not_called()
    forbidden.assert_not_called()
    assert saved.plan is not None
    assert saved.plan.objects == before.objects
    assert saved.plan.issues == before.issues
    assert app.validation.is_current(saved, saved.plan)


@pytest.mark.parametrize("resource_kind", ["source", "binary"])
def test_zone_reuse_still_rejects_tampered_prepared_files_without_a_write(
    prepared_runtime, resource_kind: str
) -> None:
    runtime, router, store, created, _, forbidden = prepared_runtime
    app = runtime.application
    project = app.repository.get(created.id)
    assert project.plan is not None
    app.validation.refresh(project, project.plan)
    project = app.repository.save(project)
    assert app.validation.is_current(project, project.plan)
    engine = router.load_local(project)
    path = engine.snapshot.source if resource_kind == "source" else engine.snapshot.path
    original = path.read_bytes()
    stat = path.stat()
    path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    before = app.repository.get(project.id).model_dump_json()
    zones = [
        zone.model_copy(update={"label": "Rename only"})
        for zone in project.planting_zones
    ]
    with pytest.raises(PreparedGeometryUnavailable):
        app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    assert app.repository.get(project.id).model_dump_json() == before
    forbidden.assert_not_called()


def test_validator_without_explicit_zone_reuse_capability_falls_back_to_full_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = seeded()
    real = app.validator

    class CompatibleValidator:
        revision = real.revision

        def validate_plan(self, current, plan):
            return real.validate_plan(current, plan)

        def validate_growth_scenario(self, current, plan, year):
            return real.validate_growth_scenario(current, plan, year)

        def discard(self, project_id):
            real.discard(project_id)

    validator = CompatibleValidator()
    app = recompose_application(app, validator=validator)
    assert project.plan is not None
    assert app.validation.is_current(project, project.plan)
    full = Mock(wraps=validator.validate_plan)
    monkeypatch.setattr(validator, "validate_plan", full)
    saved = app.zones.save_planting_zones(
        project.id, draft(project), preserve_plan=True
    )
    full.assert_called_once()
    assert saved.plan is not None
    assert app.validation.is_current(saved, saved.plan)


def test_zone_reuse_does_not_accept_wrong_mutation_receipt_or_save_future_basis() -> (
    None
):
    app, project = seeded()
    assert project.plan is not None
    zones = draft(project, "create")
    receipt = ZoneChangeResult(
        preview_id="mismatched-zone-save",
        digest="a" * 64,
        project_id=project.id,
        operation="create",
        target_zone_id="new-area",
        base_state_version=project.state_version + 1,
        state_version=project.state_version + 2,
        geometry_version=project.geometry_version + 1,
        plan_version=project.plan.version,
        after_zones=tuple(
            ZoneSnapshot.model_validate(zone.model_dump()) for zone in zones
        ),
    )
    before = project.model_dump_json()
    history = app.history.state(project.id)
    with pytest.raises(DomainFailure) as caught:
        app.zones.save_planting_zones(
            project.id,
            zones,
            preserve_plan=True,
            mutation_receipt=receipt.model_dump(mode="json"),
        )
    assert caught.value.reason is FailureReason.ZONE_CHANGE_SAVE_RECEIPT_MISMATCH
    current = app.get(project.id)
    assert current.model_dump_json() == before
    assert current.plan is not None
    assert app.validation.is_current(current, current.plan)
    assert app.history.state(project.id) == history
    assert (
        app.zones.get_zone_change_receipt(
            project.id, receipt.preview_id, receipt.digest, receipt.base_state_version
        )
        is None
    )


def test_unknown_shapely_subclass_rechecks_its_custom_zone_rule_even_with_current_basis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ZoneSensitiveGeometry(ShapelyGeometryEngine):
        def position_violation(self, current, x, y, radius, plant_kind="tree"):
            if any(
                zone.label == "Custom blocked area" for zone in current.planting_zones
            ):
                return PositionViolation(
                    code="CUSTOM_ZONE",
                    title="Custom zone rule",
                    description="This provider applies a rule to the working zone label.",
                    rule_id="custom_zone",
                    actual=0,
                    required=1,
                    suggested_action="Review working zone",
                )
            return super().position_violation(current, x, y, radius, plant_kind)

    app, project = seeded()
    validator = RuleBasedPlanValidator(geometry=ZoneSensitiveGeometry())
    app = recompose_application(app, validator=validator)
    assert project.plan is not None
    app.validation.refresh(project, project.plan)
    project = app.repository.save(project)
    assert app.validation.is_current(project, project.plan)
    assert not any(issue.code == "CUSTOM_ZONE" for issue in project.plan.issues)
    full = Mock(wraps=validator.validate_plan)
    monkeypatch.setattr(validator, "validate_plan", full)
    saved = app.zones.save_planting_zones(
        project.id, draft(project, label="Custom blocked area"), preserve_plan=True
    )
    full.assert_called_once()
    assert saved.plan is not None
    assert any(issue.code == "CUSTOM_ZONE" for issue in saved.plan.issues)
    assert app.validation.is_current(saved, saved.plan)


def test_unknown_rule_validator_subclass_rechecks_its_custom_rule_even_with_current_basis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CustomRuleValidator(RuleBasedPlanValidator):
        def validate_plan(self, current, plan):
            issues = super().validate_plan(current, plan)
            if any(
                zone.label == "Custom rule applies" for zone in current.planting_zones
            ):
                issues.append(
                    ValidationIssue(
                        severity="warning",
                        code="CUSTOM_RULE",
                        title="Custom validator rule",
                        description="This validator adds a zone-dependent rule.",
                    )
                )
            return issues

    app, project = seeded()
    validator = CustomRuleValidator()
    app = recompose_application(app, validator=validator)
    assert project.plan is not None
    app.validation.refresh(project, project.plan)
    project = app.repository.save(project)
    assert app.validation.is_current(project, project.plan)
    assert not any(issue.code == "CUSTOM_RULE" for issue in project.plan.issues)
    full = Mock(wraps=validator.validate_plan)
    monkeypatch.setattr(validator, "validate_plan", full)
    saved = app.zones.save_planting_zones(
        project.id, draft(project, label="Custom rule applies"), preserve_plan=True
    )
    full.assert_called_once()
    assert saved.plan is not None
    assert any(issue.code == "CUSTOM_RULE" for issue in saved.plan.issues)
    assert app.validation.is_current(saved, saved.plan)


def test_removing_old_duplicate_zone_id_cannot_hide_lost_planting_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = seeded()
    assert project.plan is not None
    project.planting_zones.append(
        project.planting_zones[0].model_copy(
            update={
                "geometry": deepcopy(project.planting_zones[1].geometry),
                "label": "Old duplicate west",
            },
            deep=True,
        )
    )
    project.geometry_version += 1
    attach_planting_zone_features(project)
    app.validation.refresh(project, project.plan)
    project = app.repository.save(project)
    assert not any(issue.code == "PLANTING_ZONE" for issue in project.plan.issues)
    zones = [zone.model_copy(deep=True) for zone in project.planting_zones[1:]]
    assert len({zone.id for zone in zones}) == len(zones)
    # The old and new mappings collapse to the same IDs, but the old first west
    # contour contained the planting and is absent from the submitted population.
    assert {zone.id: zone.geometry for zone in project.planting_zones} == {
        zone.id: zone.geometry for zone in zones
    }
    full = Mock(wraps=app.validator.validate_plan)
    monkeypatch.setattr(app.validator, "validate_plan", full)
    saved = app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    full.assert_called_once()
    assert saved.plan is not None
    assert any(issue.code == "PLANTING_ZONE" for issue in saved.plan.issues)
    issues, objects = _full_result(saved)
    assert saved.plan.issues == issues
    assert saved.plan.objects == objects


def test_unknown_prepared_engine_subclass_cannot_inherit_zone_independence(
    prepared_runtime,
) -> None:
    runtime, router, _, created, _, _ = prepared_runtime
    project = runtime.application.repository.get(created.id)
    concrete = router.load_local(project)
    assert type(concrete) is PreparedGeometryEngine
    assert concrete.validation_independent_of_planting_zones(project)

    class CustomPreparedGeometryEngine(PreparedGeometryEngine):
        def position_violation(self, current, x, y, radius, plant_kind="tree"):
            return super().position_violation(current, x, y, radius, plant_kind)

    subclass = CustomPreparedGeometryEngine(concrete.snapshot, project)
    assert not subclass.validation_independent_of_planting_zones(project)
