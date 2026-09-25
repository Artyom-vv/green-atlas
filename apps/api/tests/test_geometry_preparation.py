"""Shared consumers batch raw coordinates, without changing placement rules."""

from datetime import UTC, datetime
from threading import RLock
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.geometry.domain import PositionChecker, PositionViolation
from app.geometry.ports import GeometryEnginePort, GeometryPreparationPort
from app.geometry.preparation import prepare_positions
from app.planning.change_contracts import PlanChangeSetApplyRequest, PlanChangeSetDraft
from app.planning.changes import ChangeSetApplication
from app.planning.contracts import Plan, PlanObject
from app.planning.domain import PlanVersionConflict
from app.planning.evaluation import PlanEvaluation
from app.projects.contracts import Project
from app.regulations.trace_contracts import PlantingRuleTrace, RuleTraceBasis
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation


class BatchGeometryPort(GeometryEnginePort, GeometryPreparationPort):
    pass


def project_with(*positions):
    return Project(
        id="shared", name="Batch preparation", state_version=4, geometry_version=3,
        plan=Plan(objects=[
            PlanObject(id=str(index), kind="shrub", x=x, y=y, radius=0.2)
            for index, (x, y) in enumerate(positions)
        ]),
    )


def geometry_fake(*, batch=True):
    geometry = Mock(spec_set=BatchGeometryPort if batch else GeometryEnginePort)
    prepared = set()

    def key(project, x, y):
        return id(project), project.state_version, project.geometry_version, x, y

    def prepare(project, points):
        prepared.update(key(project, x, y) for x, y in points)

    def query(project, x, y, *args):
        if batch:
            assert key(project, x, y) in prepared, "Query before batch preparation"

    def trace(project, x, y, kind, crown):
        query(project, x, y)
        return PlantingRuleTrace(
            basis=RuleTraceBasis(
                project_id=project.id, state_version=project.state_version,
                geometry_version=project.geometry_version, plan_version=project.plan.version,
                requirement_profile="fake", registry_revision="fake",
            ), x=x, y=y, plant_kind=kind, mature_crown_diameter_m=crown, entries=[],
        )

    if batch:
        geometry.prepare_positions.side_effect = prepare
    for name in (
        "position_violation", "validate_position", "placement_advisory",
        "placement_advisory_detail", "future_growth_advisory", "future_growth_advisory_detail",
    ):
        getattr(geometry, name).side_effect = query
    geometry.position_rule_trace.side_effect = trace
    return geometry


@pytest.fixture(autouse=True)
def no_legacy_checker(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No PositionChecker for the injected provider")
    monkeypatch.setattr(PositionChecker, "__init__", forbidden)


def application(project, geometry):
    repository = Mock()
    repository.get.side_effect = lambda *args, **kwargs: project.model_copy(deep=True)
    history = Mock()
    history.receipt.return_value = None
    history_application = Mock()

    def commit(updated, *args, **kwargs):
        updated.state_version += 1
        return updated

    history_application.commit.side_effect = commit
    return ChangeSetApplication(
        repository=repository, evaluation=PlanEvaluation(geometry, lambda: str(uuid4())),
        validation=PlanValidation(RuleBasedPlanValidator(geometry=geometry)),
        history_application=history_application, history=history,
        edit_lock=RLock(), invalidate_spacing=Mock(),
        now=lambda: datetime.now(UTC), new_id=lambda: str(uuid4()),
    )


def draft(project, operations):
    return PlanChangeSetDraft(
        base_plan_version=project.plan.version, source="system", label="Batch",
        operations=operations,
    )


def addition(x, y=5, kind="tree", radius=0.3):
    return {"type": "add", "object": {"kind": kind, "x": x, "y": y, "radius": radius}}


def update(id_, **changes):
    return {"type": "update", "object_id": id_, "changes": changes}


def request(preview):
    return PlanChangeSetApplyRequest(
        preview_id=preview.id, digest=preview.digest, base_plan_version=preview.base_plan_version,
    )


@pytest.mark.parametrize("batch", [False, True])
def test_helper_is_optional_and_passes_the_exact_snapshot_and_list_once(batch):
    geometry = geometry_fake(batch=batch)
    project = project_with()
    points = [(1.0, 2.0), (1.0, 2.0)]
    prepare_positions(geometry, project, points)
    if batch:
        geometry.prepare_positions.assert_called_once_with(project, points)
        assert geometry.prepare_positions.call_args.args[0] is project
        assert geometry.prepare_positions.call_args.args[1] is points
    else:
        assert geometry.mock_calls == []
        assert not hasattr(GeometryEnginePort, "prepare_positions")


def test_falsey_provider_still_prepares_empty_batch():
    class Provider:
        prepare_positions = Mock()

        def __bool__(self):
            return False

    provider, project = Provider(), project_with()
    prepare_positions(provider, project, [])
    provider.prepare_positions.assert_called_once_with(project, [])


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, KeyError])
@pytest.mark.parametrize("consumer", ["helper", "preview", "validator"])
def test_preparation_failure_propagates_before_any_queries_or_mutations(error_type, consumer):
    geometry, project = geometry_fake(), project_with((10, 5))
    error = error_type("native batch failed")
    geometry.prepare_positions.side_effect = error
    changes = application(project, geometry)
    before = project.model_dump_json()
    with pytest.raises(error_type) as caught:
        if consumer == "helper":
            prepare_positions(geometry, project, [(10, 5)])
        elif consumer == "validator":
            changes.validation.refresh(project, project.plan)
        else:
            changes.preview_on_snapshot(project, draft(project, [addition(30)]))
    assert caught.value is error
    assert [call[0] for call in geometry.mock_calls] == ["prepare_positions"]
    assert project.model_dump_json() == before
    assert not changes._previews
    changes.history_application.commit.assert_not_called()


def test_preview_warms_all_additions_partial_updates_and_radius_only_changes():
    project = project_with((10, 5), (40, 5), (70, 5), (100, 5))
    geometry = geometry_fake()
    changes = application(project, geometry)
    operations = [
        addition(130), addition(160, kind="shrub", radius=0.8),
        update("0", x=0), update("1", y=0), update("2", radius=0.4),
        {"type": "delete", "object_id": "3"}, update("missing", x=42),
    ]
    before = project.model_dump_json()
    preview = changes.preview_on_snapshot(project, draft(project, operations), cache_preview=False)
    geometry.prepare_positions.assert_called_once_with(
        project, [(130, 5), (160, 5), (0, 5), (40, 0), (70, 5)],
    )
    assert geometry.mock_calls[0][0] == "prepare_positions"
    assert len(preview.additions) == 2 and len(preview.updates) == 3
    assert preview.candidate_results[-1].status == "blocked"
    assert project.model_dump_json() == before
    assert not changes._previews


@pytest.mark.parametrize("reject_first", [False, True])
def test_repeated_partial_updates_cover_both_accepted_and_rejected_previous_moves(reject_first):
    project, geometry = project_with((10, 5)), geometry_fake()
    check = geometry.validate_position.side_effect

    def validate(project, x, y, *args):
        check(project, x, y, *args)
        if reject_first and (x, y) == (20, 5):
            raise ValueError("First move rejected")

    geometry.validate_position.side_effect = validate
    changes = application(project, geometry)
    preview = changes.preview_on_snapshot(
        project, draft(project, [update("0", x=20), update("0", y=30)]), cache_preview=False,
    )
    geometry.prepare_positions.assert_called_once_with(project, [(20, 5), (10, 30), (20, 30)])
    assert (preview.updates[-1].x, preview.updates[-1].y) == ((10 if reject_first else 20), 30)


@pytest.mark.parametrize("batch", [False, True])
def test_final_preview_validation_and_apply_share_prepared_snapshot(batch):
    project, geometry = project_with((10, 5)), geometry_fake(batch=batch)
    changes = application(project, geometry)
    preview = changes.preview_on_snapshot(project, draft(project, [addition(30)]))
    assert preview.can_apply
    if batch:
        # One candidate batch, then one batch for the complete resulting plan.
        assert [call.args[1] for call in geometry.prepare_positions.call_args_list] == [
            [(30, 5)], [(10, 5), (30, 5)],
        ]
        assert all(call.args[0] is project for call in geometry.prepare_positions.call_args_list)
    calls_before_apply = len(geometry.mock_calls)
    result = changes.apply_change_set(project.id, request(preview), snapshot=project)
    assert len(result.plan.objects) == 2
    assert result.plan.version == preview.base_plan_version + 1
    assert len(geometry.mock_calls) == calls_before_apply
    changes.repository.get.assert_called_with(project.id, lightweight=True)
    changes.history_application.commit.assert_called_once()


@pytest.mark.parametrize("version", ["state_version", "geometry_version", "plan"])
def test_apply_rejects_authoritative_changes_without_reusing_prepared_results(version):
    project, geometry = project_with(), geometry_fake()
    changes = application(project, geometry)
    preview = changes.preview_on_snapshot(project, draft(project, [addition(30)]))
    if version == "plan":
        project.plan.version += 1
    else:
        setattr(project, version, getattr(project, version) + 1)
    with pytest.raises(PlanVersionConflict if version == "plan" else ValueError):
        changes.apply_change_set(project.id, request(preview))
    changes.history_application.commit.assert_not_called()
    assert geometry.prepare_positions.call_count == 2


@pytest.mark.parametrize("version", ["state_version", "geometry_version", "plan", "missing_plan"])
def test_apply_rejects_stale_supplied_snapshot(version):
    project, geometry = project_with(), geometry_fake()
    changes = application(project, geometry)
    preview = changes.preview_on_snapshot(project, draft(project, [addition(30)]))
    stale = project.model_copy(deep=True)
    if version == "missing_plan":
        stale.plan = None
    elif version == "plan":
        stale.plan.version += 1
    else:
        setattr(stale, version, getattr(stale, version) + 1)
    with pytest.raises(ValueError, match="Снимок ручной команды устарел"):
        changes.apply_change_set(project.id, request(preview), snapshot=stale)
    changes.history_application.commit.assert_not_called()


def test_preview_rejects_stale_plan_before_preparation():
    project, geometry = project_with(), geometry_fake()
    changes = application(project, geometry)
    operation = draft(project, [addition(30)])
    project.plan.version += 1
    with pytest.raises(PlanVersionConflict):
        changes.preview_on_snapshot(project, operation)
    assert geometry.mock_calls == []


def test_validator_uses_the_supplied_plan_and_fresh_snapshot_on_every_call():
    project, geometry = project_with((10, 5)), geometry_fake()
    validator = RuleBasedPlanValidator(geometry=geometry)
    plan = Plan(objects=[
        PlanObject(kind="tree", x=30, y=5, radius=0.8),
        PlanObject(kind="shrub", x=60, y=5, radius=0.2),
    ])
    for snapshot in (project, project.model_copy(deep=True)):
        snapshot.geometry_version += 1
        validator.validate_plan(snapshot, plan)
        assert geometry.prepare_positions.call_args.args[0] is snapshot
        assert geometry.prepare_positions.call_args.args[1] == [(30, 5), (60, 5)]
    assert geometry.prepare_positions.call_count == 2
    assert not validator._position_checkers


def test_batch_does_not_change_native_rejections_or_local_spacing():
    project, geometry = project_with(), geometry_fake()
    check = geometry.position_violation.side_effect

    def violation(project, x, y, *args):
        check(project, x, y, *args)
        if x == 30:
            return PositionViolation(
                code="NATIVE_OCCUPIED", title="Occupied", description="CAD area",
                rule_id="native", actual=0, required=1, suggested_action="Move",
            )

    geometry.position_violation.side_effect = violation
    preview = application(project, geometry).preview_on_snapshot(
        project, draft(project, [addition(30), addition(60), addition(60)]), cache_preview=False,
    )
    assert [result.code for result in preview.candidate_results] == [
        "NATIVE_OCCUPIED", "POSITION_ACCEPTED", "PLANT_SPACING",
    ]
    geometry.prepare_positions.assert_called_once_with(project, [(30, 5), (60, 5), (60, 5)])


def test_all_5000_candidates_are_passed_to_one_batch_without_consumer_chunking():
    project, geometry = project_with(), geometry_fake()
    preview = application(project, geometry).preview_on_snapshot(
        project, draft(project, [addition(index * 10) for index in range(5000)]),
        cache_preview=False,
    )
    geometry.prepare_positions.assert_called_once_with(
        project, [(index * 10, 5) for index in range(5000)],
    )
    assert len(preview.additions) == 5000
    assert preview.can_apply


def test_empty_plan_still_prepares_once_without_position_queries():
    project, geometry = project_with(), geometry_fake()
    assert RuleBasedPlanValidator(geometry=geometry).validate_plan(project, project.plan) == []
    geometry.prepare_positions.assert_called_once_with(project, [])
    assert len(geometry.mock_calls) == 1
