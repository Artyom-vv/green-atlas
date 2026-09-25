"""An injected geometry provider owns validation and release CAD evidence."""

import json
from unittest.mock import Mock, call

import pytest

from app.dxf_import.contracts import SourceFile
from app.exporting.application import ExportApplication
from app.exporting.contracts import (
    ExportArtifact,
    RegulatoryReleaseBasis,
    ReleaseCreateRequest,
)
from app.exporting.ports import DxfWriterPort, ExportProjectRepository
from app.geometry.contracts import GeometrySnapshot
from app.geometry.domain import PositionAdvisory, PositionChecker, PositionViolation
from app.geometry.ports import GeometryEnginePort
from app.geometry.rule_trace import project_rule_traces
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project
from app.regulations.trace_contracts import (
    PlantingRuleTrace,
    RuleSourceFeature,
    RuleTraceBasis,
    RuleTraceEntry,
)
from app.scene.contracts import SceneSnapshot
from app.species.contracts import GrowthEnvelopeForecast
from app.species.rule_context import mature_crown_diameter
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.application import PlanValidation
from app.validation.contracts import PlanValidationBasis, ValidationIssue
from app.validation.networks import release_network_issues

SHRUB = "spiraea-japonica@2026-08-28.1"


def planting(id_="plant", x=0, **changes):
    return PlanObject(
        id=id_,
        kind="shrub",
        x=x,
        y=4,
        radius=0.2,
        species_revision_id=SHRUB,
        **changes,
    )


def project_with(*objects):
    return Project(
        id="project",
        name="Shared provider",
        map_ready=True,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []}
        ),
        geometry_version=3,
        state_version=4,
        plan=Plan(id="plan", objects=list(objects)),
    )


def forecast(radius, year=20):
    return GrowthEnvelopeForecast(
        horizon_year=year,
        radius_min_m=radius,
        radius_max_m=radius,
        confidence="low",
        basis="test forecast",
    )


def advisory(code):
    return PositionAdvisory(
        code=code,
        title=code,
        description=f"Provider: {code}",
        suggested_action="Review provider evidence",
    )


def network_entry(status="failed", code="NETWORK_CLEARANCE_FAILED", **changes):
    return RuleTraceEntry(
        obstacle_kind="utility",
        status=status,
        code=code,
        note=f"Provider: {code}",
        **changes,
    )


def fake_geometry(entries_by_x=None):
    """Port-shaped fake; its measurements do not come from project GeoJSON."""
    entries_by_x = entries_by_x or {}
    geometry = Mock(spec_set=GeometryEnginePort)
    geometry.position_violation.return_value = None
    geometry.placement_advisory_detail.return_value = None
    geometry.future_growth_advisory_detail.return_value = None

    def trace(project, x, y, plant_kind, crown):
        return PlantingRuleTrace(
            basis=RuleTraceBasis(
                project_id=project.id,
                state_version=project.state_version,
                geometry_version=project.geometry_version,
                plan_version=project.plan.version,
                requirement_profile="fake",
                registry_revision="fake",
            ),
            x=x,
            y=y,
            plant_kind=plant_kind,
            mature_crown_diameter_m=crown,
            entries=entries_by_x.get(x, []),
        )

    geometry.position_rule_trace.side_effect = trace
    return geometry


@pytest.fixture
def forbid_legacy(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Shared validation must not use PositionChecker/GeoJSON")

    # Patching the class catches every imported alias, including providers.
    monkeypatch.setattr(PositionChecker, "__init__", forbidden)
    monkeypatch.setattr(RuleBasedPlanValidator, "_position_checker", forbidden)
    monkeypatch.setattr("app.validation.networks.network_rule_entries", forbidden)
    monkeypatch.setattr("app.validation.networks.Point", forbidden)
    monkeypatch.setattr("app.geometry.rule_trace.position_rule_trace", forbidden)
    monkeypatch.setattr("app.geometry.rule_trace.network_rule_entries", forbidden)
    monkeypatch.setattr("app.geometry.rule_trace.Point", forbidden)


def test_all_queries_use_the_supplied_provider_and_current_project(forbid_legacy):
    objects = [
        planting("blocked", 0),
        planting("advised", 20),
        planting(
            "growth",
            40,
            canopy_forecast=[forecast(99, 10), forecast(2)],
            root_forecast=[forecast(99, 10), forecast(3)],
        ),
        planting("clear", 60),
        planting("incomplete-growth", 80, canopy_forecast=[forecast(2)]),
    ]
    project = project_with(*objects)
    geometry = fake_geometry()
    violation = PositionViolation(
        code="NATIVE_OCCUPIED",
        title="Occupied",
        description="Provider area",
        rule_id="native-area",
        actual=0,
        required=5,
        suggested_action="Move",
    )
    geometry.position_violation.side_effect = lambda project, x, y, radius, kind: (
        violation if x == 0 else None
    )
    geometry.placement_advisory_detail.side_effect = (
        lambda project, x, y, radius, kind: (
            advisory("SOURCE_GEOMETRY_PARTIAL") if x == 20 else None
        )
    )
    geometry.future_growth_advisory_detail.return_value = advisory("NATIVE_GROWTH")
    validator = RuleBasedPlanValidator(geometry=geometry)

    findings = validator.validate_plan(project, project.plan)

    assert {i.code for i in findings} == {
        "NATIVE_OCCUPIED",
        "SOURCE_GEOMETRY_PARTIAL",
        "NATIVE_GROWTH",
    }
    assert [o.status for o in objects] == [
        "error",
        "warning",
        "warning",
        "valid",
        "valid",
    ]
    assert {i.code: i.rule_id for i in findings} == {
        "NATIVE_OCCUPIED": "native-area",
        "SOURCE_GEOMETRY_PARTIAL": "source_review",
        "NATIVE_GROWTH": "growth_forecast",
    }
    blocked = next(i for i in findings if i.code == violation.code)
    assert (blocked.actual, blocked.required, blocked.description) == (
        0,
        5,
        "Provider area",
    )
    assert geometry.position_violation.call_args_list == [
        call(project, o.x, o.y, o.radius, o.kind) for o in objects
    ]
    assert geometry.placement_advisory_detail.call_args_list == [
        call(project, o.x, o.y, o.radius, o.kind) for o in objects[1:]
    ]
    assert geometry.position_rule_trace.call_args_list == [
        call(project, o.x, o.y, o.kind, mature_crown_diameter(SHRUB)) for o in objects
    ]
    geometry.future_growth_advisory_detail.assert_called_once_with(
        project, 40, 4, 2, 3, "shrub"
    )
    assert {c[0] for c in geometry.mock_calls} == {
        "position_violation",
        "placement_advisory_detail",
        "position_rule_trace",
        "future_growth_advisory_detail",
    }
    assert all(c.args[0] is project for c in geometry.mock_calls)
    assert not validator._position_checkers

    # Reloaded copies, even at the same geometry version, reach the provider.
    reloaded = project.model_copy(deep=True)
    geometry.position_violation.side_effect = None
    geometry.placement_advisory_detail.side_effect = None
    geometry.future_growth_advisory_detail.return_value = None
    geometry.reset_mock()
    assert validator.validate_plan(reloaded, reloaded.plan) == []
    assert all(o.status == "valid" for o in reloaded.plan.objects)
    assert all(c.args[0] is reloaded for c in geometry.mock_calls)


def test_network_trace_preserves_unknown_and_failed_without_remeasurement(
    forbid_legacy,
):
    project = project_with(
        planting("unknown"), planting("failed", 20), planting("clear", 40)
    )
    entries = {
        0: [network_entry("not_checked", "NETWORK_SOURCE_INCOMPLETE")],
        20: [
            network_entry(
                rule_id="power", actual_distance_m=0.123456, required_distance_m=0.7
            ),
            network_entry(
                "not_checked", "NETWORK_CONTEXT_UNKNOWN", actual_distance_m=7.25
            ),
            network_entry("passed", "NETWORK_CLEARANCE_PASSED", rule_id="heat"),
            network_entry("no_matching_obstacle", "NO_MATCHING_SOURCE"),
            RuleTraceEntry(
                obstacle_kind="building",
                status="failed",
                code="BASE_ROW_DISTANCE_FAILED",
                note="Not a network",
            ),
        ],
        40: [network_entry("passed", "NETWORK_CLEARANCE_PASSED")],
    }
    findings = RuleBasedPlanValidator(geometry=fake_geometry(entries)).validate_plan(
        project, project.plan
    )

    assert len(findings) == 3
    unknown = next(i for i in findings if i.object_id == "unknown")
    assert (
        unknown.severity == "warning"
        and unknown.actual is None
        and unknown.unit is None
    )
    failed = next(i for i in findings if i.severity == "error")
    assert (
        failed.object_id,
        failed.rule_id,
        failed.actual,
        failed.required,
        failed.unit,
    ) == (
        "failed",
        "power",
        0.123456,
        0.7,
        "м",
    )
    assert failed.description == entries[20][0].note
    assert [o.status for o in project.plan.objects] == ["warning", "error", "valid"]
    assert len({i.id for i in findings}) == len(findings)


@pytest.mark.parametrize("reverse", [False, True])
def test_network_dedup_keeps_failure_closest_measurement_and_primary_violation(
    forbid_legacy, reverse
):
    project = project_with(planting())
    entries = [
        network_entry(rule_id="primary", actual_distance_m=1.2, required_distance_m=2),
        network_entry(rule_id="other", actual_distance_m=0.8, required_distance_m=2),
        network_entry(rule_id="other", actual_distance_m=0, required_distance_m=2),
        network_entry("not_checked", rule_id="other"),
        network_entry(),
        network_entry(rule_id="network-source-evidence", actual_distance_m=0.4),
    ]
    geometry = fake_geometry({0: list(reversed(entries)) if reverse else entries})
    geometry.position_violation.return_value = PositionViolation(
        code="NETWORK_CLEARANCE_FAILED",
        rule_id="primary",
        actual=1.2,
        required=2,
        title="Primary violation",
        description="Primary measurement",
        suggested_action="Move",
    )
    findings = RuleBasedPlanValidator(geometry=geometry).validate_plan(
        project, project.plan
    )

    assert len(findings) == len({i.id for i in findings}) == 3
    assert {i.rule_id: i.actual for i in findings} == {
        "primary": 1.2,
        "other": 0,
        "network-source-evidence": 0.4,
    }
    assert all(i.severity == "error" for i in findings)
    assert (
        next(i for i in findings if i.rule_id == "primary").title == "Primary violation"
    )
    assert project.plan.objects[0].status == "error"
    geometry.placement_advisory_detail.assert_not_called()


def test_missing_network_summary_and_finding_ids_are_stable(forbid_legacy):
    project = project_with(planting("a"), planting("b", 20))
    entries = {
        x: [network_entry("not_checked", "NO_NETWORK_FEATURES")] for x in (0, 20)
    }
    entries[0].append(
        network_entry(rule_id="power", actual_distance_m=0.6, required_distance_m=0.7)
    )
    validator = RuleBasedPlanValidator(geometry=fake_geometry(entries))
    first = validator.validate_plan(project, project.plan)
    assert len(first) == 2
    summary = next(i for i in first if i.code == "NO_NETWORK_FEATURES")
    failed = next(i for i in first if i.severity == "error")
    assert summary.object_id is None and summary.related_object_ids == []
    assert summary.actual is None and summary.x is None and summary.y is None
    assert "2" in summary.description
    assert [o.status for o in project.plan.objects] == ["error", "warning"]

    project.plan.objects.reverse()
    assert validator.validate_plan(project, project.plan) == first
    entries[0][1].actual_distance_m = 0.5
    changed = validator.validate_plan(project, project.plan)
    updated = next(i for i in changed if i.id == failed.id)
    assert updated.actual == 0.5
    assert {i.id for i in changed} == {i.id for i in first}
    project.plan.objects = [project.plan.objects[1]]
    reduced = validator.validate_plan(project, project.plan)
    assert "1" in next(i for i in reduced if i.id == summary.id).description
    project.plan.objects.clear()
    assert validator.validate_plan(project, project.plan) == []


def test_spacing_and_species_warnings_remain_local_and_keep_their_ids(forbid_legacy):
    objects = [planting("b", 0.1), planting("a", 0)]
    objects[0].species_revision_id = None
    project = project_with(*objects)
    validator = RuleBasedPlanValidator(geometry=fake_geometry())
    first = validator.validate_plan(project, project.plan)
    assert {i.code for i in first} == {"PLANT_SPACING", "SPECIES_UNASSIGNED"}
    spacing = next(i for i in first if i.code == "PLANT_SPACING")
    assert spacing.object_id == "a" and spacing.related_object_ids == ["a", "b"]
    assert spacing.actual == 0.1 and spacing.required > spacing.actual
    assert all(o.status == "error" for o in objects)
    project.plan.objects.reverse()
    assert validator.validate_plan(project, project.plan) == first
    objects[0].x = 0.05
    updated = next(
        i
        for i in validator.validate_plan(project, project.plan)
        if i.code == "PLANT_SPACING"
    )
    assert updated.id == spacing.id and updated.actual == 0.05


@pytest.mark.parametrize(
    "method",
    [
        "position_violation",
        "placement_advisory_detail",
        "position_rule_trace",
        "future_growth_advisory_detail",
    ],
)
def test_provider_errors_propagate_without_fallback(forbid_legacy, method):
    project = project_with(
        planting(canopy_forecast=[forecast(2)], root_forecast=[forecast(3)])
    )
    geometry = fake_geometry()
    error = RuntimeError(f"Provider failure: {method}")
    getattr(geometry, method).side_effect = error
    validator = RuleBasedPlanValidator(geometry=geometry)

    with pytest.raises(RuntimeError) as caught:
        validator.validate_plan(project, project.plan)

    assert caught.value is error
    assert not validator._position_checkers


@pytest.mark.parametrize("options", [{}, {"geometry": None}])
def test_default_validator_keeps_legacy_evidence_and_lru_cache(options):
    project = project_with(planting())
    validator = RuleBasedPlanValidator(2, **options)
    first = validator.validate_plan(project, project.plan)
    assert [i.code for i in first] == ["NO_NETWORK_FEATURES"]
    checker = validator._position_checker(project)
    reloaded = project.model_copy(deep=True)
    assert validator._position_checker(reloaded) is checker
    assert validator.validate_plan(reloaded, reloaded.plan) == first
    reloaded.geometry_version += 1
    assert validator._position_checker(reloaded) is not checker
    second, third = Project(name="Second"), Project(name="Third")
    validator._position_checker(second)
    validator._position_checker(reloaded)
    validator._position_checker(third)
    assert list(validator._position_checkers) == [project.id, third.id]
    validator.discard(project.id)
    assert list(validator._position_checkers) == [third.id]


def test_legacy_and_injected_provider_give_distinct_results(monkeypatch):
    project = project_with(planting())
    assert [
        i.code for i in RuleBasedPlanValidator().validate_plan(project, project.plan)
    ] == ["NO_NETWORK_FEATURES"]

    def forbidden(*args, **kwargs):
        raise AssertionError("The provider must own the query")

    monkeypatch.setattr(PositionChecker, "__init__", forbidden)
    geometry = fake_geometry({0: [network_entry("passed", "NETWORK_CLEARANCE_PASSED")]})
    assert (
        RuleBasedPlanValidator(geometry=geometry).validate_plan(project, project.plan)
        == []
    )
    assert project.plan.objects[0].status == "valid"


def test_project_traces_return_complete_provider_evidence_unchanged(forbid_legacy):
    objects = [planting("shrub"), planting("tree", 20)]
    objects[1].kind = "tree"
    objects[1].species_revision_id = None
    project = project_with(*objects)
    # A provider need not have a display/sampled snapshot to answer a query.
    project.geometry = None
    project.map_ready = False
    original_project = project.model_dump()
    entries = {
        0: [
            RuleTraceEntry(
                obstacle_kind="building",
                status="failed",
                code="NATIVE_OCCUPIED",
                rule_id="native-area",
                actual_distance_m=0,
                required_distance_m=5,
                note="Provider occupied area",
                nearest_features=[
                    RuleSourceFeature(
                        feature_id="xref/block/building",
                        source_layer="Native building",
                        source_index=7,
                        actual_distance_m=0,
                    )
                ],
            ),
            network_entry("not_checked", "NETWORK_SOURCE_INCOMPLETE"),
        ],
        20: [
            network_entry("passed", "NETWORK_CLEARANCE_PASSED", actual_distance_m=9.25)
        ],
    }
    geometry = fake_geometry(entries)
    trace_factory = geometry.position_rule_trace.side_effect
    expected = [
        trace_factory(
            project, o.x, o.y, o.kind, mature_crown_diameter(o.species_revision_id)
        )
        for o in objects
    ]
    expected[0].basis.source_content_sha256 = "a" * 64
    original_traces = [trace.model_dump() for trace in expected]
    geometry.position_rule_trace.side_effect = expected

    traces = project_rule_traces(project, geometry=geometry)

    assert list(traces) == [o.id for o in objects]
    assert all(
        traces[o.id] is trace for o, trace in zip(objects, expected, strict=True)
    )
    assert [trace.model_dump() for trace in traces.values()] == original_traces
    assert geometry.mock_calls == [
        call.position_rule_trace(project, 0, 4, "shrub", mature_crown_diameter(SHRUB)),
        call.position_rule_trace(project, 20, 4, "tree", None),
    ]
    assert all(c.args[0] is project for c in geometry.mock_calls)
    assert project.model_dump() == original_project


def test_release_network_evidence_uses_fresh_provider_trace_and_compacts_missing(
    forbid_legacy,
):
    project = project_with(
        planting("failed"), planting("unknown", 20), planting("clear", 40)
    )
    original_project = project.model_dump()
    entries = {
        0: [
            network_entry(
                rule_id="power", actual_distance_m=0.654321, required_distance_m=0.7
            ),
            network_entry(
                rule_id="power", actual_distance_m=0.123456, required_distance_m=0.7
            ),
            network_entry("not_checked", "NO_NETWORK_FEATURES"),
            RuleTraceEntry(
                obstacle_kind="building",
                status="failed",
                code="NATIVE_OCCUPIED",
                note="Building only",
            ),
        ],
        20: [
            network_entry("not_checked", "NO_NETWORK_FEATURES"),
            network_entry("not_checked", "NETWORK_SOURCE_INCOMPLETE"),
        ],
        40: [network_entry("passed", "NETWORK_CLEARANCE_PASSED")],
    }
    geometry = fake_geometry(entries)
    issues = release_network_issues(project, geometry=geometry)

    assert len(issues) == 3
    failed = next(i for i in issues if i.severity == "error")
    assert (
        failed.object_id,
        failed.rule_id,
        failed.actual,
        failed.required,
        failed.unit,
    ) == (
        "failed",
        "power",
        0.123456,
        0.7,
        "м",
    )
    assert failed.description == entries[0][1].note
    unknown = next(i for i in issues if i.code == "NETWORK_SOURCE_INCOMPLETE")
    assert unknown.object_id == "unknown" and unknown.severity == "warning"
    assert unknown.actual is None and unknown.unit is None
    summary = next(i for i in issues if i.code == "NO_NETWORK_FEATURES")
    assert summary.object_id is None and summary.related_object_ids == []
    assert summary.severity == "warning" and "3" in summary.description
    assert geometry.mock_calls == [
        call.position_rule_trace(
            project, o.x, o.y, o.kind, mature_crown_diameter(SHRUB)
        )
        for o in project.plan.objects
    ]
    assert project.model_dump() == original_project

    # Same project and revisions, different provider evidence: no local cache
    # or persisted plan issue may stand in for the fresh release query.
    for x in entries:
        entries[x] = [network_entry("passed", "NETWORK_CLEARANCE_PASSED")]
    geometry.reset_mock()
    assert release_network_issues(project, geometry=geometry) == []
    assert geometry.position_rule_trace.call_count == len(project.plan.objects)


@pytest.mark.parametrize("query", [release_network_issues, project_rule_traces])
def test_release_queries_propagate_provider_error_after_partial_results(
    forbid_legacy, query
):
    project = project_with(planting("first"), planting("second", 20))
    original_project = project.model_dump()
    geometry = fake_geometry(
        {0: [network_entry("not_checked", "NETWORK_SOURCE_INCOMPLETE")]}
    )
    error = RuntimeError("Native session lost on the second query")
    trace_factory = geometry.position_rule_trace.side_effect

    def fail_second(project, x, y, kind, crown):
        if x == 20:
            raise error
        return trace_factory(project, x, y, kind, crown)

    geometry.position_rule_trace.side_effect = fail_second
    with pytest.raises(RuntimeError) as caught:
        query(project, geometry=geometry)

    assert caught.value is error
    assert geometry.position_rule_trace.call_count == 2
    assert project.model_dump() == original_project


@pytest.mark.parametrize(
    "query,expected", [(release_network_issues, []), (project_rule_traces, {})]
)
@pytest.mark.parametrize("has_plan", [False, True])
def test_release_queries_with_no_objects_do_not_use_geometry(
    forbid_legacy, query, expected, has_plan
):
    project = project_with()
    if not has_plan:
        project.plan = None
    geometry = fake_geometry()
    assert query(project, geometry=geometry) == expected
    assert geometry.mock_calls == []


@pytest.mark.parametrize("options", [{}, {"geometry": None}])
def test_release_queries_keep_legacy_default_evidence(options):
    project = project_with(planting())
    issues = release_network_issues(project, **options)
    assert len(issues) == 1 and issues[0].code == "NO_NETWORK_FEATURES"
    assert issues[0].severity == "warning" and issues[0].object_id is None
    traces = project_rule_traces(project, **options)
    assert list(traces) == ["plant"]
    assert any(
        entry.code == "NO_NETWORK_FEATURES" and entry.status == "not_checked"
        for entry in traces["plant"].entries
    )
    assert traces["plant"].basis.requirement_profile != "fake"


@pytest.mark.parametrize("query", [release_network_issues, project_rule_traces])
def test_release_queries_use_falsey_explicit_provider(forbid_legacy, query):
    class FalseyProvider:
        def __bool__(self):
            return False

        def position_rule_trace(self, *args):
            return geometry.position_rule_trace(*args)

    project = project_with(planting())
    geometry = fake_geometry()
    query(project, geometry=FalseyProvider())
    geometry.position_rule_trace.assert_called_once_with(
        project, 0, 4, "shrub", mature_crown_diameter(SHRUB)
    )


def export_application(project, geometry):
    """Exercise release composition with all storage and CAD writes mocked."""
    project.source_file = SourceFile(
        name="source.dxf",
        size=3,
        imported_at="2026-09-23",
        dxf_version="AC1027",
        units="m",
        entity_count=0,
    )
    repository = Mock(spec_set=ExportProjectRepository)
    repository.get.return_value = project
    repository.get_release.return_value = None
    repository.get_source.return_value = b"dxf"
    repository.get_source_components.return_value = {}
    writer = Mock(spec_set=DxfWriterPort)
    writer.create.return_value = (
        ExportArtifact(filename="plan.dxf", status="ready", size=3, download_url=""),
        b"dxf",
    )
    scene = Mock(
        return_value=SceneSnapshot(
            plan_version=project.plan.version,
            horizon_year=20,
            coordinate_origin=[0, 0],
            note="test scene",
        )
    )
    return ExportApplication(
        repository=repository,
        writer=writer,
        scene=scene,
        geometry=geometry,
    )


def release_request(mode="final"):
    return ReleaseCreateRequest(
        mode=mode,
        regulatory_basis=RegulatoryReleaseBasis(
            pp616_status="not_applicable",
            pp616_reference="No removal",
            pp1160_status="not_required",
            pp1160_reference="No permit procedure",
            confirmed_by="Test reviewer",
        ),
    )


@pytest.mark.parametrize(
    "status,code",
    [
        ("failed", "NETWORK_CLEARANCE_FAILED"),
        ("not_checked", "NETWORK_SOURCE_INCOMPLETE"),
    ],
)
def test_final_release_uses_provider_gate_despite_empty_persisted_issues(
    forbid_legacy, status, code
):
    project = project_with(planting())
    geometry = fake_geometry({0: [network_entry(status, code)]})
    app = export_application(project, geometry)
    assert project.plan.issues == []

    with pytest.raises(ValueError, match="инженерных сетей: 1$"):
        app.create_release(project.id, release_request())

    geometry.position_rule_trace.assert_called_once_with(
        project, 0, 4, "shrub", mature_crown_diameter(SHRUB)
    )
    app.writer.create.assert_not_called()
    app.repository.publish_release.assert_not_called()


@pytest.mark.parametrize("mode,trace_calls", [("draft", 1), ("final", 2)])
def test_release_manifest_contains_the_same_provider_evidence(
    forbid_legacy, mode, trace_calls
):
    project = project_with(planting())
    geometry = fake_geometry(
        {
            0: [
                network_entry(
                    "passed",
                    "NETWORK_CLEARANCE_PASSED",
                    actual_distance_m=9.123456,
                    required_distance_m=0.7,
                    nearest_features=[
                        RuleSourceFeature(
                            feature_id="xref/cable",
                            source_index=8,
                            actual_distance_m=9.123456,
                        )
                    ],
                )
            ]
        }
    )
    app = export_application(project, geometry)
    expected = geometry.position_rule_trace.side_effect(
        project, 0, 4, "shrub", mature_crown_diameter(SHRUB)
    ).model_dump(mode="json")

    package = app.create_release(project.id, release_request(mode))

    assert package.status == ("ready" if mode == "final" else "draft")
    app.repository.publish_release.assert_called_once()
    published_project, release_id, payload, files = (
        app.repository.publish_release.call_args.args
    )
    assert published_project is project and release_id == package.id
    assert json.loads(payload) == package.model_dump(mode="json")
    artifact = next(a for a in package.artifacts if a.kind == "manifest")
    manifest = json.loads(files[artifact.id])
    assert manifest["planting_rule_traces"] == {"plant": expected}
    assert (
        geometry.mock_calls
        == [
            call.position_rule_trace(
                project, 0, 4, "shrub", mature_crown_diameter(SHRUB)
            )
        ]
        * trace_calls
    )


@pytest.mark.parametrize(
    "mode,fail_on_call,writer_calls",
    [
        ("final", 1, 0),  # Network gate, before producing any CAD output.
        ("final", 2, 1),  # Gate passed; manifest trace fails.
        ("draft", 1, 1),  # Drafts still require the provider for their manifest.
    ],
)
def test_release_provider_failure_never_publishes_or_falls_back(
    forbid_legacy, mode, fail_on_call, writer_calls
):
    project = project_with(planting())
    geometry = fake_geometry({0: [network_entry("passed", "NETWORK_CLEARANCE_PASSED")]})
    trace_factory = geometry.position_rule_trace.side_effect
    error = RuntimeError("Provider session unavailable")

    def fail_at_stage(*args):
        if geometry.position_rule_trace.call_count == fail_on_call:
            raise error
        return trace_factory(*args)

    geometry.position_rule_trace.side_effect = fail_at_stage
    app = export_application(project, geometry)
    with pytest.raises(RuntimeError) as caught:
        app.create_release(project.id, release_request(mode))

    assert caught.value is error
    assert geometry.position_rule_trace.call_count == fail_on_call
    assert app.writer.create.call_count == writer_calls
    app.repository.publish_release.assert_not_called()


@pytest.mark.parametrize("provider_fails", [False, True])
def test_persisted_v4_validation_basis_advances_only_after_provider_recheck(
    forbid_legacy, provider_fails
):
    project = project_with(planting())
    project.plan.validation_basis = PlanValidationBasis(
        plan_version=project.plan.version,
        geometry_version=project.geometry_version,
        validator_revision="rule-based-plan-v4-network-source-summary",
        objects_digest="a" * 64,
    )
    project.plan.issues = [
        ValidationIssue(
            code="STALE_EVIDENCE",
            severity="warning",
            title="Old evidence",
            description="Persisted before provider injection",
            object_id="plant",
        )
    ]
    # Simulate persisted data without opening a repository or a database.
    reloaded = Project.model_validate_json(project.model_dump_json())
    original_basis = reloaded.plan.validation_basis.model_copy()
    original_issues = [i.model_copy() for i in reloaded.plan.issues]
    geometry = fake_geometry(
        {0: [network_entry("not_checked", "NETWORK_SOURCE_INCOMPLETE")]}
    )
    validator = RuleBasedPlanValidator(geometry=geometry)
    validation = PlanValidation(validator)
    if provider_fails:
        error = RuntimeError("Cannot validate this capture")
        geometry.position_violation.side_effect = error
        with pytest.raises(RuntimeError) as caught:
            validation.refresh(reloaded, reloaded.plan)
        assert caught.value is error
        assert reloaded.plan.validation_basis == original_basis
        assert reloaded.plan.issues == original_issues
    else:
        validation.refresh(reloaded, reloaded.plan)
        assert reloaded.plan.validation_basis.validator_revision == validator.revision
        assert validator.revision != original_basis.validator_revision
        assert [i.code for i in reloaded.plan.issues] == ["NETWORK_SOURCE_INCOMPLETE"]
        assert reloaded.plan.objects[0].status == "warning"
    geometry.position_violation.assert_called_once_with(reloaded, 0, 4, 0.2, "shrub")
