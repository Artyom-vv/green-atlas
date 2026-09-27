"""Numerical rules require the exact source context and remain edition-scoped."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from shapely.geometry import LineString, Point, Polygon, box, mapping
from test_placement_allocation import application

from app.contracts import GeometrySnapshot, Plan, PlanObject, Project
from app.dxf_import.contracts import SourceFile
from app.dxf_import.layer_contracts import Layer
from app.exporting.application import ExportApplication
from app.exporting.contracts import (
    ExportArtifact,
    RegulatoryReleaseBasis,
    ReleaseCreateRequest,
)
from app.geometry.domain import PositionChecker
from app.geometry.network_trace import network_rule_entries
from app.geometry.rule_trace import position_rule_trace
from app.geometry.utility_contracts import UtilityContext
from app.planning.change_contracts import PlanChangeSetDraft, PlanObjectAddOperation
from app.planning.contracts import PlacementCheckRequest, PlanObjectCreate
from app.planning.pattern_contracts import FillPatternRequest
from app.regulations.network_rules import NETWORK_RULE_PACK
from app.scene.contracts import SceneSnapshot
from app.validation.adapters import RuleBasedPlanValidator


def network_project(*networks):
    layers, features = [], []
    for index, (kind, geometry, reference) in enumerate(networks):
        name = f"network-{index}"
        context = UtilityContext(
            network_type=kind,
            geometry_reference=reference,
            installation="underground",
            review_status="confirmed",
            source_reference=f"Survey sheet {index}",
            confirmed_by="Reviewer",
        )
        layers.append(
            Layer(
                id=name,
                source_name=name,
                suggested_kind="utility",
                mapped_kind="utility",
                object_count=1,
                color="#888888",
                utility_context=context,
            )
        )
        features.append(
            {
                "type": "Feature",
                "id": name,
                "properties": {
                    "kind": "utility",
                    "source_layer": name,
                    "utility_context": context.model_dump(mode="json"),
                },
                "geometry": mapping(geometry),
            }
        )
    return Project(
        name="Reviewed networks",
        layers=layers,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
        geometry_version=3,
        state_version=4,
        plan=Plan(),
    )


def entries(project, kind="tree", crown=5.0, point=None):
    return network_rule_entries(
        PositionChecker(project).networks, point or Point(0, 0), kind, crown
    )


@pytest.mark.parametrize(
    "kind,tree,shrub",
    [
        ("gas", 1.5, None),
        ("sewer", 1.5, None),
        ("heat", 2, 1),
        ("water", 2, None),
        ("drainage", 2, None),
        ("power_cable", 2, 0.7),
        ("communication_cable", 2, 0.7),
    ],
)
@pytest.mark.parametrize("plant_kind", ["tree", "shrub"])
def test_each_source_row_has_exact_threshold_and_dash_is_not_zero(
    kind, tree, shrub, plant_kind
):
    required = tree if plant_kind == "tree" else shrub
    project = network_project((kind, box(required or 1, -2, 4, 2), "outer_surface"))
    result = entries(project, plant_kind)[0]
    if required is None:
        assert result.code == "NETWORK_SHRUB_DISTANCE_UNSPECIFIED"
        assert result.status == "not_checked"
        assert result.required_distance_m is None
        assert PositionChecker(project).check(0, 0, 0.1, plant_kind) is None
        return
    assert result.status == "passed"
    assert result.actual_distance_m == required
    assert result.required_distance_m == required
    assert PositionChecker(project).check(0, 0, 0.1, plant_kind) is None
    assert (
        PositionChecker(project).check(0.01, 0, 0.1, plant_kind).rule_id
        == result.rule_id
    )
    assert entries(project, plant_kind, point=Point(0.01, 0))[0].status == "failed"


def test_stricter_farther_network_is_not_hidden_by_nearest_network():
    project = network_project(
        ("gas", box(1.6, -1, 2, 1), "outer_surface"),
        ("heat", box(-3, -1, -1.8, 1), "channel_wall"),
    )
    result = entries(project)
    assert [(entry.status, entry.actual_distance_m) for entry in result] == [
        ("passed", 1.6),
        ("failed", 1.8),
    ]
    violation = PositionChecker(project).check(0, 0, 0.1)
    assert violation.rule_id.endswith("heat")
    assert violation.source_feature_ids == ("network-1",)
    trace = position_rule_trace(PositionChecker(project), project, 0, 0, "tree", 5)
    assert trace.basis.network_rule_pack == NETWORK_RULE_PACK
    assert trace.current_compliance == "not_established"


@pytest.mark.parametrize("crown", [None, 5.01, 20])
def test_unknown_or_wide_crown_cannot_turn_base_minimum_into_full_pass(crown):
    project = network_project(("heat", box(2, -3, 4, 3), "channel_wall"))
    assert entries(project, crown=crown)[0].code == "NETWORK_CROWN_CLEARANCE_UNRESOLVED"
    assert entries(project, crown=crown, point=Point(0.1, 0))[0].status == "failed"


@pytest.mark.parametrize(
    "geometry,reference,reason",
    [
        (LineString([(3, -3), (3, 3)]), "axis", "NETWORK_AXIS_EXTENT_UNKNOWN"),
        (
            LineString([(3, -3), (3, 3)]),
            "outer_surface",
            "NETWORK_FOOTPRINT_UNCONFIRMED",
        ),
        (Point(3, 0), "outer_surface", "NETWORK_FOOTPRINT_UNCONFIRMED"),
        (box(3, -3, 4, 3), "channel_wall", "NETWORK_REFERENCE_UNSUPPORTED"),
    ],
)
def test_unknown_physical_reference_does_not_receive_a_numeric_buffer(
    geometry, reference, reason
):
    project = network_project(("gas", geometry, reference))
    assert entries(project)[0].code == reason
    assert entries(project)[0].required_distance_m is None
    assert PositionChecker(project).check(3, 0, 0.1) is None


def test_confirmation_must_match_mapped_layer_and_complete_source():
    project = network_project(("heat", box(3, -1, 4, 1), "outer_surface"))
    project.layers[0].utility_context = None
    assert entries(project)[0].code == "NETWORK_SOURCE_UNCONFIRMED"
    project.layers[0].mapped_kind = "ignore"
    assert entries(project)[-1].code == "NETWORK_SOURCE_INCOMPLETE"


def test_visibility_is_not_permission_to_exclude_a_network():
    project = network_project(("heat", box(1, -1, 3, 1), "outer_surface"))
    project.layers[0].visible = False
    assert entries(project)[0].status == "failed"
    assert PositionChecker(project).check(0, 0, 0.1) is not None


def test_holes_and_occupied_interior_measure_the_actual_area():
    geometry = Polygon(
        [(-5, -5), (5, -5), (5, 5), (-5, 5)],
        holes=[[(-3, -3), (-3, 3), (3, 3), (3, -3)]],
    )
    project = network_project(("heat", geometry, "outer_surface"))
    assert entries(project)[0].actual_distance_m == 3
    assert entries(project, point=Point(4, 0))[0].actual_distance_m == 0
    assert entries(project, point=Point(4, 0))[0].status == "failed"


def test_local_safe_area_matches_full_network_exclusion():
    project = network_project(
        ("heat", box(10, -1000, 12, 1000), "channel_wall"),
        ("gas", box(10000, 0, 10001, 1), "outer_surface"),
    )
    checker = PositionChecker(project)
    area = box(0, 0, 100, 100)
    actual = checker.hard_safe_area(area, 0.1, "tree")
    expected = area.buffer(-0.1).difference(box(10, -1000, 12, 1000).buffer(2))
    assert actual.symmetric_difference(expected).area < 1e-7


def test_final_rechecks_evidence_and_draft_stays_editable():
    project = network_project(("heat", box(10, -2, 11, 2), "outer_surface"))
    project.source_file = SourceFile(
        name="source.dxf",
        size=3,
        imported_at="2026-09-15",
        dxf_version="AC1027",
        units="m",
        entity_count=1,
    )
    project.plan.objects = [
        PlanObject(
            kind="shrub",
            x=0,
            y=0,
            radius=0.2,
            species_revision_id="spiraea-japonica@2026-08-28.1",
        )
    ]
    repository = Mock()
    repository.get.return_value = project
    repository.get_release.return_value = None
    repository.get_source.return_value = b"dxf"
    repository.get_source_components.return_value = {}
    writer = Mock()
    writer.create.return_value = (
        ExportArtifact(filename="source.dxf", status="ready", size=3, download_url=""),
        b"dxf",
    )
    app = ExportApplication(
        repository=repository,
        writer=writer,
        scene=lambda *_: SceneSnapshot(
            plan_version=1, horizon_year=0, coordinate_origin=[0, 0], note="test"
        ),
    )
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
    assert app.create_release(project.id, request).status == "ready"
    # Invalidate the actual source while keeping a stale empty issue list.
    project.layers[0].utility_context = None
    project.plan.issues = []
    with pytest.raises(ValueError, match="инженерных сетей"):
        app.create_release(project.id, request)
    assert (
        app.create_release(
            project.id, request.model_copy(update={"mode": "draft"})
        ).status
        == "draft"
    )
    issues = RuleBasedPlanValidator().validate_plan(project, project.plan)
    assert any(
        issue.code == "NETWORK_SOURCE_UNCONFIRMED" and issue.severity == "warning"
        for issue in issues
    )
    assert project.plan.objects[0].status == "warning"


def test_preview_and_apply_use_same_network_rule_and_uncertainty_remains_draft():
    app, project = application()
    network = network_project(("heat", box(31.8, 0, 32.8, 100), "channel_wall"))
    project.layers = network.layers
    project.geometry.feature_collection["features"].extend(
        network.geometry.feature_collection["features"]
    )
    project.geometry_version += 1
    project = app.repository.save(project)
    request = PlacementCheckRequest(kind="tree", x=30, y=30)
    result = app.check_placement(project.id, request)
    assert not result.allowed
    assert result.code == "NETWORK_CLEARANCE_FAILED"
    entry = next(
        item for item in result.rule_trace.entries if item.obstacle_kind == "utility"
    )
    assert entry.rule_id == result.rule_id
    assert entry.status == "failed"
    with pytest.raises(ValueError):
        app.add_object(project.id, PlanObjectCreate(kind="tree", x=30, y=30))
    assert not app.get(project.id).plan.objects
    draft = PlanChangeSetDraft(
        base_plan_version=project.plan.version,
        source="manual",
        label="Draft",
        operations=[
            PlanObjectAddOperation(object=PlanObjectCreate(kind="tree", x=70, y=30))
        ],
    )
    preview = app.preview_change_set(project.id, draft)
    assert preview.can_apply
    assert (
        preview.candidate_results[0].rule_trace.entries[-1].code
        == "NETWORK_CROWN_CLEARANCE_UNRESOLVED"
    )


def test_bulk_uncertainty_is_one_limited_reason_not_a_false_verified_result(
    monkeypatch,
):
    app, project = application()
    network = network_project(("heat", box(10000, 0, 10001, 10), "outer_surface"))
    project.layers = network.layers
    project.geometry.feature_collection["features"].extend(
        network.geometry.feature_collection["features"]
    )
    project.geometry_version += 1
    project = app.repository.save(project)
    monkeypatch.setattr(
        "app.planning.pattern_application.build_data_passport",
        lambda _, **kwargs: SimpleNamespace(gaps=[], mass_placement_status="verified"),
    )
    preview = app.preview_pattern(
        project.id,
        FillPatternRequest(
            base_plan_version=1,
            zone_ids=["west"],
            placement_mode="count",
            target_count=3,
        ),
    )
    assert preview.accepted_count == 3
    assert preview.change_set.can_apply
    assert preview.data_confidence == "limited"
    assert len(preview.data_confidence_reasons) == 1
    assert "увеличение отступа для кроны" in preview.data_confidence_reasons[0]
