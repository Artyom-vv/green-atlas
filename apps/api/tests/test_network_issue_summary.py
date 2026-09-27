"""Shared missing data stays visible without duplicating each planting row."""

import json
from unittest.mock import Mock

import pytest
from shapely.geometry import box
from test_network_rules import network_project
from test_placement_allocation import application

from app.dxf_import.contracts import SourceFile
from app.exporting.application import ExportApplication
from app.exporting.contracts import RegulatoryReleaseBasis, ReleaseCreateRequest
from app.geometry.rule_trace import project_rule_traces
from app.history.adapters import InMemoryProjectHistory
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.contracts import Plan, PlanObject
from app.planning.pattern_contracts import FillPatternRequest
from app.releases.service import build_release
from app.scene.contracts import SceneSnapshot
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation
from app.validation.contracts import ValidationIssue
from app.validation.networks import (
    compact_missing_network_issues,
    release_network_issues,
)

SHRUB = "spiraea-japonica@2026-08-28.1"


def planting(index):
    return PlanObject(
        id=f"plant-{index}",
        kind="shrub",
        x=index * 5,
        y=0,
        radius=0.2,
        species_revision_id=SHRUB,
    )


def issue(code, object_id=None):
    return ValidationIssue(
        code=code,
        object_id=object_id,
        severity="warning",
        title=code,
        description=code,
    )


def test_122_plantings_have_one_stable_finding_and_keep_every_trace():
    project = network_project()
    project.plan.objects = [planting(index) for index in range(122)]
    validator = RuleBasedPlanValidator()
    findings = validator.validate_plan(project, project.plan)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.code == "NO_NETWORK_FEATURES"
    assert finding.object_id is None and finding.related_object_ids == []
    assert finding.actual is None and finding.x is None and finding.y is None
    assert "122" in finding.description
    assert all(item.status == "warning" for item in project.plan.objects)
    traces = project_rule_traces(project)
    assert len(traces) == 122
    for trace in traces.values():
        network = next(
            entry for entry in trace.entries if entry.obstacle_kind == "utility"
        )
        assert network.code == "NO_NETWORK_FEATURES"
        assert network.status == "not_checked"
        assert network.actual_distance_m is None
    project.plan.objects.reverse()
    assert validator.validate_plan(project, project.plan) == findings
    project.plan.objects.pop()
    changed = validator.validate_plan(project, project.plan)
    assert changed[0].id == finding.id
    assert "121" in changed[0].description
    project.plan.objects.clear()
    assert validator.validate_plan(project, project.plan) == []


def test_compaction_is_idempotent_for_mixed_batches_and_preserves_other_findings():
    original = [issue("NO_NETWORK_FEATURES", "a"), issue("NO_NETWORK_FEATURES", "b")]
    crown = issue("NETWORK_CROWN_CLEARANCE_UNRESOLVED", "a")
    result = compact_missing_network_issues([*original, crown], object_count=2)
    assert len(result) == 2 and crown in result
    assert [entry.object_id for entry in original] == ["a", "b"]
    assert compact_missing_network_issues(result, object_count=2) == result
    batch = compact_missing_network_issues([*result, *original], object_count=2)
    assert batch == result


def test_crown_and_failed_distance_remain_addressed_after_compaction():
    project = network_project(("heat", box(0, 0, 1, 1), "outer_surface"))
    project.plan.objects = [
        PlanObject(id="close", kind="tree", x=-1.9, y=0.5, radius=0.1),
        PlanObject(id="wide", kind="tree", x=-10, y=0.5, radius=0.1),
    ]
    findings = RuleBasedPlanValidator().validate_plan(project, project.plan)
    failed = next(i for i in findings if i.code == "NETWORK_CLEARANCE_FAILED")
    crown = next(i for i in findings if i.code == "NETWORK_CROWN_CLEARANCE_UNRESOLVED")
    assert (failed.object_id, failed.actual, failed.required) == ("close", 1.9, 2)
    assert crown.object_id == "wide"
    assert [item.status for item in project.plan.objects] == ["error", "warning"]


def test_recheck_removes_summary_only_when_current_network_inputs_support_it():
    project = network_project()
    project.plan.objects = [planting(0), planting(1)]
    validation = PlanValidation(RuleBasedPlanValidator())
    validation.refresh(project, project.plan)
    initial = project.plan.issues[0]
    network = network_project(("heat", box(20, -1, 21, 1), "outer_surface"))
    project.layers, project.geometry = network.layers, network.geometry
    project.geometry_version += 1
    validation.refresh(project, project.plan)
    assert project.plan.issues == []
    assert all(item.status == "valid" for item in project.plan.objects)
    assert release_network_issues(project) == []
    project.layers = []
    project.geometry.feature_collection["features"] = []
    project.geometry_version += 1
    # A stale, empty stored issue list cannot authorize publication.
    fresh = release_network_issues(project)
    assert len(fresh) == 1 and fresh[0].code == initial.code
    validation.refresh(project, project.plan)
    assert project.plan.issues[0].id == initial.id
    assert all(item.status == "warning" for item in project.plan.objects)


def test_batch_preview_apply_recheck_preserve_one_summary_and_limited_draft():
    app, project = application()
    # The allocation fixture covers preview; applying also needs real history.
    app.history_application.history = InMemoryProjectHistory()
    preview = app.preview_pattern(
        project.id,
        FillPatternRequest(
            base_plan_version=1,
            zone_ids=["west"],
            plant_kind="shrub",
            species_revision_id=SHRUB,
            placement_mode="count",
            target_count=122,
        ),
    )
    assert preview.accepted_count == 122 and preview.change_set.can_apply
    assert preview.data_confidence == "limited"
    assert (
        sum(
            reason.startswith("Инженерные сети:")
            for reason in preview.data_confidence_reasons
        )
        == 1
    )
    assert all(
        any(
            entry.code == "NO_NETWORK_FEATURES" and entry.status == "not_checked"
            for entry in candidate.rule_trace.entries
        )
        for candidate in preview.change_set.candidate_results
    )
    change = preview.change_set
    applied = app.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=change.id,
            digest=change.digest,
            base_plan_version=change.base_plan_version,
        ),
    )
    shared = [i for i in applied.plan.issues if i.code == "NO_NETWORK_FEATURES"]
    assert len(shared) == 1 and shared[0].object_id is None
    assert all(item.status == "warning" for item in applied.plan.objects)
    stored = app.get(project.id)
    app.validation.refresh(stored, stored.plan)
    assert [i for i in stored.plan.issues if i.code == "NO_NETWORK_FEATURES"] == shared


def test_final_release_uses_one_fresh_project_reason_even_with_empty_stored_issues():
    project = network_project()
    project.plan.objects = [planting(0), planting(1)]
    project.source_file = SourceFile(
        name="source.dxf",
        size=3,
        imported_at="2026-09-15",
        dxf_version="AC1027",
        units="m",
        entity_count=0,
    )
    repository, writer = Mock(), Mock()
    repository.get.return_value = project
    app = ExportApplication(repository=repository, writer=writer, scene=Mock())
    request = ReleaseCreateRequest(
        mode="final",
        regulatory_basis=RegulatoryReleaseBasis(
            pp616_status="not_applicable",
            pp616_reference="No removal",
            pp1160_status="not_required",
            pp1160_reference="No permit procedure",
            confirmed_by="Reviewer",
        ),
    )
    with pytest.raises(ValueError, match="инженерных сетей: 1$"):
        app.create_release(project.id, request)
    writer.create.assert_not_called()
    repository.publish_release.assert_not_called()


def test_draft_manifest_keeps_summary_object_statuses_and_individual_traces():
    project = network_project()
    project.plan.objects = [planting(0), planting(1)]
    project.source_file = SourceFile(
        name="source.dxf",
        size=3,
        imported_at="2026-09-15",
        dxf_version="AC1027",
        units="m",
        entity_count=0,
    )
    PlanValidation(RuleBasedPlanValidator()).refresh(project, project.plan)
    package, files = build_release(
        project,
        ReleaseCreateRequest(mode="draft"),
        "draft-summary-test",
        b"dxf",
        SceneSnapshot(
            plan_version=1, horizon_year=0, coordinate_origin=[0, 0], note="test"
        ),
        b"dxf",
    )
    artifact = next(item for item in package.artifacts if item.kind == "manifest")
    manifest = json.loads(files[artifact.id])
    assert package.status == "draft"
    assert len(manifest["plan"]["issues"]) == 1
    assert manifest["plan"]["issues"][0]["object_id"] is None
    assert all(item["status"] == "warning" for item in manifest["plan"]["objects"])
    assert len(manifest["planting_rule_traces"]) == 2
    for trace in manifest["planting_rule_traces"].values():
        assert any(
            entry["code"] == "NO_NETWORK_FEATURES" and entry["status"] == "not_checked"
            for entry in trace["entries"]
        )
    assert (
        sum(warning.startswith("Инженерные сети:") for warning in package.warnings) == 1
    )


@pytest.mark.parametrize(
    "code,expected",
    [
        ("NO_NETWORK_FEATURES", "warning"),
        ("UNRELATED_PROJECT_NOTE", "valid"),
    ],
)
def test_legacy_crown_migration_preserves_only_relevant_project_warning(code, expected):
    object_ = planting(0).model_copy(update={"status": "warning"})
    restored = Plan(
        objects=[object_],
        issues=[
            issue("CROWN_SETBACK_REVIEW", object_.id),
            issue(code),
        ],
    )
    assert restored.objects[0].status == expected
    assert all(i.code != "CROWN_SETBACK_REVIEW" for i in restored.issues)
