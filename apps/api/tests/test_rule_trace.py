"""Trace measures the input model; it never turns missing evidence into a permit."""

import json
from io import BytesIO
from zipfile import ZipFile

import pytest
from shapely.geometry import Point, box, mapping
from test_placement_allocation import application

from app.contracts import GeometrySnapshot, Plan, PlanObject, Project
from app.dxf_import.contracts import SourceFile
from app.exporting.contracts import ReleaseCreateRequest
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.domain import PositionChecker
from app.geometry.rule_trace import position_rule_trace
from app.geometry.utility_contracts import UtilityContext
from app.planning.change_contracts import (
    PlanChangeSetDraft,
    PlanObjectAddOperation,
    PlanObjectDeleteOperation,
    PlanObjectUpdateOperation,
)
from app.planning.contracts import (
    PlacementCheckRequest,
    PlanObjectCreate,
    PlanObjectUpdate,
)
from app.planning.trace import operation_rule_trace
from app.regulations.profiles import LCT_REQUIREMENT_PROFILE, requirement_profile
from app.releases.service import build_release, release_identity
from app.scene.contracts import SceneSnapshot


def feature(kind, geometry, feature_id="source", **properties):
    return {
        "type": "Feature",
        "id": feature_id,
        "properties": {"kind": kind, "source_layer": kind, **properties},
        "geometry": mapping(geometry),
    }


def project_with(*features):
    return Project(
        name="Trace evidence",
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
        geometry_version=7,
        state_version=9,
        map_ready=True,
        plan=Plan(),
    )


def entries(project, x=0, y=0):
    trace = position_rule_trace(PositionChecker(project), project, x, y, "tree")
    return {entry.obstacle_kind: entry for entry in trace.entries}


def test_exact_nearest_is_global_and_missing_is_not_zero_or_passed():
    project = project_with(
        feature("building", box(1000, -5, 1010, 5), "far"),
        feature("road", box(-10, -1, -1, 1), "near"),
    )
    result = entries(project)
    assert result["building"].status == "passed"
    assert result["building"].actual_distance_m == 1000
    assert result["building"].required_distance_m == 5
    assert result["road"].status == "failed"
    assert result["road"].actual_distance_m == 1
    assert result["road"].nearest_features[0].feature_id == "near"
    assert result["utility"].status == "not_checked"
    assert result["utility"].code == "NO_NETWORK_FEATURES"
    assert result["utility"].actual_distance_m is None
    assert result["utility"].required_distance_m is None
    empty = entries(project_with())
    assert empty["building"].status == "no_matching_obstacle"
    assert empty["building"].actual_distance_m is None


def test_nearest_ties_retain_original_order_and_touching_is_real_zero():
    project = project_with(
        feature("road", Point(2, 0), "east"),
        feature("road", Point(-2, 0), "west"),
        feature("building", box(-1, -1, 1, 1), "overlap"),
    )
    result = entries(project)
    assert [item.feature_id for item in result["road"].nearest_features] == [
        "east",
        "west",
    ]
    assert [item.source_index for item in result["road"].nearest_features] == [0, 1]
    assert result["road"].status == "passed"
    assert result["building"].actual_distance_m == 0
    assert result["building"].status == "failed"


@pytest.mark.parametrize("ready", [False, True])
def test_missing_geometry_never_reports_measured_distance(ready):
    project = Project(name="Unprepared", map_ready=ready)
    result = entries(project)
    assert all(entry.status == "not_checked" for entry in result.values())
    assert all(entry.actual_distance_m is None for entry in result.values())
    assert all(entry.code == "GEOMETRY_NOT_READY" for entry in result.values())


def test_confirmed_network_context_does_not_invent_an_universal_setback():
    context = UtilityContext(
        network_type="gas",
        geometry_reference="outer_surface",
        installation="underground",
        review_status="confirmed",
        source_reference="Survey sheet 1",
        confirmed_by="Survey review",
    )
    result = entries(
        project_with(
            feature(
                "utility", Point(0, 0), utility_context=context.model_dump(mode="json")
            )
        )
    )["utility"]
    assert result.code == "NETWORK_SOURCE_UNCONFIRMED"
    assert result.status == "not_checked"
    assert result.actual_distance_m == 0
    assert result.required_distance_m is None
    assert result.nearest_features[0].utility_context == context
    corrupted = entries(
        project_with(
            feature(
                "utility", Point(0, 0), utility_context={"review_status": "confirmed"}
            )
        )
    )["utility"]
    assert corrupted.code == "NETWORK_CONTEXT_UNKNOWN"
    assert corrupted.nearest_features[0].utility_context is None


def test_cached_checker_trace_uses_callers_current_revision():
    project = project_with(feature("building", Point(10, 0)))
    engine = ShapelyGeometryEngine()
    before = engine.position_rule_trace(project, 0, 0)
    after = engine.position_rule_trace(
        project.model_copy(update={"state_version": 10}), 0, 0
    )
    assert before.basis.state_version == 9
    assert after.basis.state_version == 10
    assert before.basis.geometry_version == after.basis.geometry_version == 7
    assert after.basis.requirement_profile == LCT_REQUIREMENT_PROFILE
    assert after.current_compliance == "not_established"


def test_accepted_and_rejected_preview_share_same_input_basis():
    app, project = application()
    project.map_ready = True
    project = app.repository.save(project)
    draft = PlanChangeSetDraft(
        base_plan_version=1,
        label="Two positions",
        operations=[
            PlanObjectAddOperation(object=PlanObjectCreate(kind="tree", x=30, y=30)),
            PlanObjectAddOperation(object=PlanObjectCreate(kind="tree", x=900, y=900)),
        ],
    )
    preview = app.preview_change_set(project.id, draft)
    accepted, rejected = preview.candidate_results
    assert accepted.status == "allowed"
    assert rejected.status == "blocked"
    assert accepted.rule_trace.basis == rejected.rule_trace.basis
    assert accepted.rule_trace.basis.state_version == project.state_version
    assert (accepted.rule_trace.x, rejected.rule_trace.x) == (30, 900)
    assert app.get(project.id).plan.objects == []


def test_update_trace_uses_requested_position_and_delete_has_no_position():
    project = project_with()
    project.plan.objects = [PlanObject(id="tree", kind="tree", x=1, y=2, radius=1)]
    engine = ShapelyGeometryEngine()
    operation = PlanObjectUpdateOperation(
        object_id="tree", changes=PlanObjectUpdate(x=9)
    )
    trace = operation_rule_trace(engine, project, operation)
    assert (trace.x, trace.y) == (9, 2)
    assert (
        operation_rule_trace(
            engine, project, PlanObjectDeleteOperation(object_id="tree")
        )
        is None
    )


def test_manual_check_shares_trace_and_refuses_stale_basis_before_computing():
    app, project = application()
    project.map_ready = True
    project = app.repository.save(project)
    request = PlacementCheckRequest(kind="tree", x=30, y=30)
    result = app.check_placement(project.id, request)
    assert result.allowed
    assert result.rule_trace.basis.geometry_version == result.geometry_version
    assert result.rule_trace.basis.state_version == result.state_version
    stale = app.check_placement(
        project.id, request.model_copy(update={"state_version": 1})
    )
    assert stale.code == "STALE_PLACEMENT_BASIS"
    assert stale.rule_trace is None


def test_manifest_trace_is_derived_without_duplicating_plan_or_zip_entries():
    project = project_with(feature("building", Point(10, 0)))
    project.plan.objects = [PlanObject(id="tree", kind="tree", x=0, y=0, radius=1)]
    project.source_file = SourceFile(
        name="source.dxf",
        size=3,
        imported_at="2026-09-15T00:00:00Z",
        dxf_version="AC1027",
        units="m",
        entity_count=1,
    )
    request = ReleaseCreateRequest(mode="draft")
    scene = SceneSnapshot(
        plan_version=1, horizon_year=0, coordinate_origin=[0, 0], note="Synthetic"
    )
    package, artifacts = build_release(
        project, request, release_identity(project, request), b"dxf", scene, b"dxf"
    )
    files = {item.kind: artifacts[item.id] for item in package.artifacts}
    manifest = json.loads(files["manifest"])
    with ZipFile(BytesIO(files["bundle"])) as archive:
        assert len(archive.namelist()) == 6
        assert archive.read("source/source.dxf") == b"dxf"
    trace = manifest["planting_rule_traces"]["tree"]
    assert trace["basis"]["geometry_version"] == manifest["project"]["geometry_version"]
    assert trace["basis"]["plan_version"] == manifest["plan"]["version"]
    assert trace["basis"]["state_version"] == project.state_version
    assert (
        trace["basis"]["requirement_profile"]
        == manifest["regulatory_requirement_profile"]["id"]
    )
    assert "rule_trace" not in manifest["plan"]["objects"][0]
    assert (
        trace["entries"][0]["rule_id"]
        in manifest["regulatory_registry"]["applied_rule_ids"]
    )
    profile = requirement_profile()
    assert profile.current_compliance == "not_established"
    assert [item.document_code for item in profile.references[:2]] == [
        "СП 42.13330.2016",
        "СП 42.13330.2026",
    ]
