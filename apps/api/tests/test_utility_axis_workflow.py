"""Real import/mapping/preview/release contracts for explicit axis dimensions."""

import json
from io import StringIO
from typing import Literal, cast
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

from app.application import ProjectApplication
from app.dxf_import.contracts import SourceFile
from app.dxf_import.legacy.reader import EzdxfReader
from app.exporting.application import ExportApplication
from app.exporting.contracts import RegulatoryReleaseBasis, ReleaseCreateRequest
from app.exporting.ports import DxfWriterPort
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.checking.domain import PositionChecker
from app.geometry.networks.network_trace import network_rule_entries
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.operations.ports import OperationRepository
from app.planning.changes.contracts import (
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
)
from app.planning.contracts import Plan, PlanObject, PlanObjectCreate
from app.planning.ports import CandidateGeneratorPort
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project
from app.regulations.trace_contracts import PlantingRuleTrace
from app.releases.service import build_release, release_identity
from app.scene.contracts import SceneSnapshot
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason
from app.species.catalog import get_species, growth_forecasts
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation


@pytest.mark.parametrize("entity_type", ["LWPOLYLINE", "POLYLINE"])
@pytest.mark.parametrize("curved", [False, True])
def test_reader_proves_only_straight_original_polylines(
    entity_type: Literal["LWPOLYLINE", "POLYLINE"], curved: bool
) -> None:
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


def test_preview_trace_and_apply_reject_stale_axis_size_without_losing_draft() -> None:
    service, project, source, _ = axis_project()
    binding = binding_for(project, source_axes(project)[0], 2)
    project = prepared(service, project, [binding])
    assert isinstance(service.repository, SqliteProjectRepository)
    app = ProjectApplication(
        repository=service.repository,
        operation_repository=cast(OperationRepository, None),
        history=SqliteProjectHistory(service.repository),
        dxf_reader=EzdxfReader(),
        geometry=ShapelyGeometryEngine(),
        geometry_query=IndexedGeometryQuery(),
        validator=RuleBasedPlanValidator(),
        writer=cast(DxfWriterPort, None),
        candidate_generator=cast(CandidateGeneratorPort, None),
    )

    def draft(x: float) -> PlanChangeSetDraft:
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
        for entry in cast(
            PlantingRuleTrace, rejected.candidate_results[0].rule_trace
        ).entries
        if entry.obstacle_kind == "utility"
    )
    assert entry.status == "failed" and entry.actual_distance_m == pytest.approx(0.5)
    accepted = app.preview_change_set(project.id, draft(7))
    assert accepted.can_apply
    accepted_entry = next(
        entry
        for entry in cast(
            PlantingRuleTrace, accepted.candidate_results[0].rule_trace
        ).entries
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
    retained = service.repository.get(project.id)
    assert retained.plan is not None
    assert retained.plan.objects == []


def test_release_and_saved_project_preserve_source_binding_surface_trace_and_final_fresh_gate() -> (
    None
):
    service, project, source, _ = axis_project()
    binding = binding_for(project, source_axes(project)[0], 2)
    project = prepared(service, project, [binding])
    cast(Plan, project.plan).objects = [
        PlanObject(
            id="plant",
            kind="shrub",
            x=7,
            y=50,
            radius=0.2,
            species_revision_id="spiraea-japonica@2026-08-28.1",
        )
    ]
    plant = cast(Plan, project.plan).objects[0]
    assert plant.species_revision_id is not None
    plant.canopy_forecast, plant.root_forecast = growth_forecasts(
        get_species(plant.species_revision_id), "standard"
    )
    validation = PlanValidation(RuleBasedPlanValidator())
    validation.refresh(project, cast(Plan, project.plan))
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
    restored = Project.model_validate_json(project.model_dump_json())
    assert (
        cast(SourceFile, restored.source_file).content_sha256
        == cast(SourceFile, project.source_file).content_sha256
    )
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
        repository=repository,
        writer=writer,
        scene=lambda *_: scene,
        validation=validation,
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
    cast(Plan, restored.plan).issues = []
    export.create_release(restored.id, final)
    layer.utility_axis_bindings = [binding.model_copy(update={"outside_diameter_m": 6})]
    restored.geometry_version += 1
    with pytest.raises(DomainFailure) as failure:
        export.create_release(restored.id, final)
    assert failure.value.reason is FailureReason.RELEASE_FINAL_UNAVAILABLE
    assert any(
        check["code"] == "network_checks"
        for check in failure.value.params["release_checks"]
    )
    assert "инженерных сетей" in public_failure_message(failure.value)
