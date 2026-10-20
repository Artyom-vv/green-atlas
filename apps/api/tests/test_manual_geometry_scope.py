"""Read-only placement and release checks hold one verified geometry resource."""

from typing import Any, cast
from unittest.mock import Mock

import pytest
from test_change_set_geometry_scope import ScopeGeometry, observe_geometry
from test_geometry_preparation import application, geometry_fake, project_with

from app.geometry.contracts import GeometrySnapshot
from app.operations.progress import OperationCancelled
from app.planning.changes.manual_application import ManualPlanningApplication
from app.planning.contracts import PlacementCheckRequest
from app.planning.domain import PlantSpacingIndex
from app.validation.networks import release_network_issues


def manual_setup():
    project = project_with((10, 5))
    project.geometry = GeometrySnapshot(
        feature_collection={"type": "FeatureCollection", "features": []}
    )
    provider = ScopeGeometry(geometry_fake())
    observe_geometry(provider)
    changes = application(project, cast(Mock, provider))
    manual = ManualPlanningApplication(
        changes.repository,
        changes.evaluation,
        changes,
        lambda current: PlantSpacingIndex(current.plan.objects),
        changes.edit_lock,
    )
    return project, provider, manual


def test_manual_check_reuses_one_scope_and_keeps_project_unchanged():
    project, provider, manual = manual_setup()
    before = project.model_dump_json()
    checked = manual.check_placement(
        project.id, PlacementCheckRequest(kind="tree", x=30, y=5, radius=0.3)
    )
    assert checked.allowed
    assert provider.entries == 1 and provider.depth == 0
    assert "geometry:prepare_positions" in provider.events
    assert "geometry:position_rule_trace" in provider.events
    assert "geometry:placement_advisory_detail" in provider.events
    assert provider.events[-2:] == ["scope:verified", "scope:closed"]
    assert project.model_dump_json() == before


@pytest.mark.parametrize("error_type", [ValueError, OperationCancelled])
def test_manual_check_never_returns_success_when_scope_exit_fails(error_type):
    project, provider, manual = manual_setup()
    before = project.model_dump_json()
    error = error_type("Resource changed or request cancelled")
    provider.exit_error = error
    with pytest.raises(error_type) as caught:
        manual.check_placement(
            project.id, PlacementCheckRequest(kind="tree", x=30, y=5, radius=0.3)
        )
    assert caught.value is error
    assert provider.entries == 1 and provider.depth == 0
    assert project.model_dump_json() == before


def test_stale_manual_check_does_not_open_geometry():
    project, provider, manual = manual_setup()
    checked = manual.check_placement(
        project.id,
        PlacementCheckRequest(
            kind="tree", x=30, y=5, base_plan_version=project.plan.version + 1
        ),
    )
    assert not checked.allowed and checked.code == "STALE_PLACEMENT_BASIS"
    assert provider.entries == 0 and provider.events == []


@pytest.mark.parametrize("exit_error", [False, True])
def test_release_network_traces_share_scope_and_refuse_exit_failure(exit_error):
    project = project_with((10, 5), (30, 5))
    before = project.model_dump_json()
    provider = ScopeGeometry(geometry_fake(batch=False))
    trace = provider.geometry.position_rule_trace.side_effect

    def inside(*args: Any, **kwargs: Any):
        provider.inside("network_trace")
        return trace(*args, **kwargs)

    provider.geometry.position_rule_trace.side_effect = inside
    if exit_error:
        provider.exit_error = OperationCancelled()
        with pytest.raises(OperationCancelled):
            release_network_issues(project, geometry=provider)
    else:
        assert release_network_issues(project, geometry=provider) == []
    assert provider.entries == 1 and provider.depth == 0
    assert provider.events.count("network_trace") == 2
    assert project.model_dump_json() == before
