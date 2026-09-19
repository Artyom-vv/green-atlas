"""Real import/mapping/preview/release contracts for explicit axis dimensions."""

import json
from io import StringIO
from unittest.mock import Mock

import ezdxf
import pytest
from shapely.geometry import Point
from test_utility_axis import (
    axis_project,
    binding_for,
    prepared,
    save_bindings,
    source_axes,
)
from test_utility_context import import_service

from app.application import ProjectApplication
from app.dxf_import.adapters import EzdxfReader
from app.exporting.application import ExportApplication
from app.exporting.contracts import RegulatoryReleaseBasis, ReleaseCreateRequest
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.domain import PositionChecker
from app.geometry.network_trace import network_rule_entries
from app.history.adapters import InMemoryProjectHistory
from app.planning.change_contracts import (
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
)
from app.planning.contracts import PlanObject, PlanObjectCreate
from app.projects.contracts import Project
from app.releases.service import build_release, release_identity
from app.scene.contracts import SceneSnapshot
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation


@pytest.mark.parametrize("entity_type", ["LWPOLYLINE", "POLYLINE"])
@pytest.mark.parametrize("curved", [False, True])
def test_reader_proves_only_straight_original_polylines(entity_type, curved):
    document = ezdxf.new("R2010")
    document.units = 6
    model = document.modelspace()
    if entity_type == "LWPOLYLINE":
        model.add_lwpolyline(
            [(0, 0, 1 if curved else 0), (10, 0, 0), (10, 10, 0)], format="xyb"
        )
    else:
        entity = model.add_polyline2d([(0, 0), (10, 0), (10, 10)])
        entity.vertices[0].dxf.bulge = 1 if curved else 0
    output = StringIO()
    document.write(output)
    result = EzdxfReader().read("polyline.dxf", output.getvalue().encode())
    properties = result.geometry.feature_collection["features"][0]["properties"]
    assert ("source_axis_provenance" in properties) is not curved


def test_preview_trace_and_apply_reject_stale_axis_size_without_losing_draft():
    service, project, source, _ = axis_project()
    binding = binding_for(project, source_axes(project)[0], 2)
    project = prepared(service, project, [binding])
    app = ProjectApplication(
        repository=service.repository,
        operation_repository=None,
        history=InMemoryProjectHistory(),
        dxf_reader=EzdxfReader(),
        geometry=ShapelyGeometryEngine(),
        geometry_query=None,
        validator=RuleBasedPlanValidator(),
        writer=None,
        candidate_generator=None,
    )

    def draft(x):
        return PlanChangeSetDraft(
            base_plan_version=1,
            source="manual",
            label="Axis fixture",
            operations=[
                PlanObjectAddOperation(object=PlanObjectCreate(kind="shrub", x=x, y=50))
            ],
        )

    rejected = app.preview_change_set(project.id, draft(8.5))
    assert not rejected.can_apply
    entry = next(
        entry
        for entry in rejected.candidate_results[0].rule_trace.entries
        if entry.obstacle_kind == "utility"
    )
    assert entry.status == "failed" and entry.actual_distance_m == pytest.approx(0.5)
    accepted = app.preview_change_set(project.id, draft(7))
    assert accepted.can_apply
    accepted_entry = next(
        entry
        for entry in accepted.candidate_results[0].rule_trace.entries
        if entry.obstacle_kind == "utility"
    )
    assert accepted_entry.status == "passed"
    changed = binding.model_copy(update={"outside_diameter_m": 6})
    modified = save_bindings(service, project, [changed])
    modified.geometry = ShapelyGeometryEngine().calculate(modified)
    service.repository.save(modified, source=source)
    with pytest.raises(ValueError):
        app.apply_change_set(
            project.id,
            PlanChangeSetApplyRequest(
                preview_id=accepted.id, digest=accepted.digest, base_plan_version=1
            ),
        )
    assert service.repository.get(project.id).plan.objects == []


def test_release_roundtrip_preserves_source_binding_surface_trace_and_final_fresh_gate():
    service, project, source, _ = axis_project()
    binding = binding_for(project, source_axes(project)[0], 2)
    project = prepared(service, project, [binding])
    project.plan.objects = [
        PlanObject(
            id="plant",
            kind="shrub",
            x=7,
            y=50,
            radius=0.2,
            species_revision_id="spiraea-japonica@2026-08-28.1",
        )
    ]
    PlanValidation(RuleBasedPlanValidator()).refresh(project, project.plan)
    request = ReleaseCreateRequest(mode="draft")
    scene = SceneSnapshot(
        plan_version=1, horizon_year=0, coordinate_origin=[0, 0], note="Axis fixture"
    )
    package, artifacts = build_release(
        project, request, release_identity(project, request), source, scene, source
    )
    manifest = json.loads(
        artifacts[
            next(item.id for item in package.artifacts if item.kind == "manifest")
        ]
    )
    mapped = next(
        item for item in manifest["layer_mappings"] if item["source_name"] == "utility"
    )
    assert mapped["utility_axis_bindings"] == [binding.model_dump(mode="json")]
    trace = manifest["planting_rule_traces"]["plant"]
    assert trace["basis"]["source_content_sha256"] == manifest["source"]["sha256"]
    evidence = next(
        entry for entry in trace["entries"] if entry["obstacle_kind"] == "utility"
    )["nearest_features"][0]
    assert evidence["actual_distance_m"] == 2
    assert evidence["axis_evidence"]["binding"]["outside_diameter_m"] == 2
    target_service, target, _ = import_service(Project(name="Restored axis"))
    target_service.dxf_reader = EzdxfReader()
    bundle = artifacts[
        next(item.id for item in package.artifacts if item.kind == "bundle")
    ]
    restored = target_service.import_release_bundle(target.id, "release.zip", bundle)
    assert restored.source_file.content_sha256 == project.source_file.content_sha256
    layer = next(layer for layer in restored.layers if layer.source_name == "utility")
    assert layer.utility_axis_bindings == [binding]
    assert (
        network_rule_entries(PositionChecker(restored).networks, Point(7, 50), "shrub")[
            0
        ].status
        == "passed"
    )
    # Final gate recalculates the network proof, even if stored issues say OK.
    repository, writer = Mock(), Mock()
    repository.get.return_value = restored
    repository.get_release.return_value = None
    repository.get_source.return_value = source
    repository.get_source_components.return_value = {}
    writer.create.return_value = (None, source)
    export = ExportApplication(
        repository=repository, writer=writer, scene=lambda *_: scene
    )
    final = ReleaseCreateRequest(
        mode="final",
        regulatory_basis=RegulatoryReleaseBasis(
            pp616_status="not_applicable",
            pp616_reference="No removal",
            pp1160_status="not_required",
            pp1160_reference="No permit procedure",
            confirmed_by="Reviewer",
        ),
    )
    restored.plan.issues = []
    export.create_release(restored.id, final)
    layer.utility_axis_bindings = [binding.model_copy(update={"outside_diameter_m": 6})]
    restored.geometry_version += 1
    with pytest.raises(ValueError, match="инженерных сетей"):
        export.create_release(restored.id, final)
