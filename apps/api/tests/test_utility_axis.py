"""Source-bound straight-axis interpretation and analytic surface distances."""

from copy import deepcopy
from hashlib import sha256
from io import StringIO

import ezdxf
import pytest
from pydantic import ValidationError
from shapely.geometry import Point, box
from test_utility_context import import_service

from app.dxf_import.adapters import EzdxfReader
from app.dxf_import.axis_provenance import straight_axis_provenance
from app.dxf_import.layer_contracts import LayerMapping
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.axis_bindings import validate_axis_bindings
from app.geometry.axis_contracts import UtilityAxisBinding
from app.geometry.axis_geometry import geometry_fingerprint
from app.geometry.domain import PositionChecker
from app.geometry.network_trace import network_rule_entries
from app.geometry.rule_trace import position_rule_trace
from app.geometry.utility_contracts import UtilityContext
from app.planning.contracts import Plan, PlanObject
from app.projects.contracts import Project
from app.validation.networks import release_network_issues


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_axis_cannot_acquire_proof_or_interrupt_reader_filter(coordinate):
    document = ezdxf.new("R2010")
    line = document.modelspace().add_line((coordinate, 0), (10, 0))
    geometry = {
        "type": "LineString",
        "coordinates": [[coordinate, 0], [10, 0]],
    }

    assert straight_axis_provenance(line, geometry) == {}
    with pytest.raises(ValueError):
        geometry_fingerprint(geometry)


def dxf_source(*, x_positions=(10,), curved=False, block=False):
    document = ezdxf.new("R2010")
    document.units = 6
    document.layers.new("site_border")
    document.layers.new("utility")
    model = document.modelspace()
    model.add_lwpolyline(
        [(0, 0), (100, 0), (100, 100), (0, 100)],
        close=True,
        dxfattribs={"layer": "site_border"},
    )
    for x in x_positions:
        if curved:
            model.add_lwpolyline(
                [(x, 0, 1), (x, 100, 0)], format="xyb", dxfattribs={"layer": "utility"}
            )
        elif block:
            definition = document.blocks.new(f"network-{x}")
            definition.add_line((x, 0), (x, 100))
            model.add_blockref(definition.name, (0, 0), dxfattribs={"layer": "utility"})
        else:
            model.add_line((x, 0), (x, 100), dxfattribs={"layer": "utility"})
    output = StringIO()
    document.write(output)
    return output.getvalue().encode()


def axis_project(**source_options):
    source = dxf_source(**source_options)
    service, target, invalidated = import_service(Project(name="Axis source"))
    service.dxf_reader = EzdxfReader()
    project = service.import_dxf(target.id, "source.dxf", source)
    invalidated.clear()
    return service, project, source, invalidated


def source_axes(project):
    return [
        feature
        for feature in project.source_geometry.feature_collection["features"]
        if feature["properties"]["source_layer"] == "utility"
    ]


def binding_for(project, feature, diameter=2):
    properties = feature["properties"]
    return UtilityAxisBinding(
        id=f"binding-{feature['id']}",
        source={
            "content_sha256": project.source_file.content_sha256,
            "feature_id": feature["id"],
            "source_handle": properties["source_handle"],
            "insert_chain": [],
            "geometry_sha256": properties["source_axis_provenance"]["geometry_sha256"],
        },
        context=UtilityContext(
            network_type="heat",
            geometry_reference="axis",
            installation="underground",
            review_status="confirmed",
            source_reference="Synthetic survey drawing 1",
            confirmed_by="Fixture reviewer",
        ),
        outside_diameter_m=diameter,
        surface_reference="outer_surface",
        outside_size_reference="Synthetic measured outer insulation diameter; not DN",
        envelope_model="circular_sweep",
        endpoint_model="round_full_source_extent",
        extent_reference="Entire synthetic source entity, including round end sweeps",
    )


def save_bindings(service, project, bindings):
    return service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=layer.id,
                kind="utility" if layer.source_name == "utility" else "site_border",
                utility_axis_bindings=bindings
                if layer.source_name == "utility"
                else [],
            )
            for layer in project.layers
            if layer.source_name in {"utility", "site_border"}
        ],
    )


def prepared(service, project, bindings):
    project = save_bindings(service, project, bindings)
    project.geometry = ShapelyGeometryEngine().calculate(project)
    project.map_ready = True
    project.plan = Plan()
    return service.repository.save(project)


def test_import_uses_content_hash_and_no_implicit_dimension_or_end_models():
    _, project, source, _ = axis_project()
    assert project.source_file.content_sha256 == sha256(source).hexdigest()
    binding = binding_for(project, source_axes(project)[0])
    payload = binding.model_dump(mode="json")
    for required in (
        "outside_diameter_m",
        "envelope_model",
        "endpoint_model",
        "extent_reference",
    ):
        missing = dict(payload)
        del missing[required]
        with pytest.raises(ValidationError):
            UtilityAxisBinding.model_validate(missing)
    del payload["source"]["insert_chain"]
    with pytest.raises(ValidationError):
        UtilityAxisBinding.model_validate(payload)


@pytest.mark.parametrize(
    "field,value",
    [
        ("content_sha256", "0" * 64),
        ("geometry_sha256", "0" * 64),
        ("source_handle", "NO-HANDLE"),
        ("feature_id", "missing-feature"),
        ("insert_chain", ["unresolved-parent"]),
    ],
)
def test_wrong_source_address_is_rejected_atomically(field, value):
    service, project, _, invalidated = axis_project()
    binding = binding_for(project, source_axes(project)[0])
    binding.source = binding.source.model_copy(update={field: value})
    before = service.repository.get(project.id).model_dump()
    with pytest.raises(ValueError, match="Ось"):
        save_bindings(service, project, [binding])
    assert service.repository.get(project.id).model_dump() == before
    assert invalidated == []


@pytest.mark.parametrize(
    "mode", ["legacy_hash", "readonly", "different_bytes", "forged_cache", "duplicate"]
)
def test_legacy_preview_cached_geometry_and_repeated_binding_cannot_confirm(mode):
    service, project, source, _ = axis_project()
    binding = binding_for(project, source_axes(project)[0])
    bindings = [binding]
    if mode == "legacy_hash":
        project.source_file.content_sha256 = None
    elif mode == "readonly":
        project.import_status.editability = "read_only"
    elif mode == "different_bytes":
        source += b"\n"
    elif mode == "forged_cache":
        feature = source_axes(project)[0]
        feature["id"] = "fake-cached-feature"
        binding.source.feature_id = feature["id"]
    else:
        bindings.append(binding.model_copy(update={"id": "another-binding"}))
    service.repository.save(project, source=source)
    with pytest.raises(ValueError):
        save_bindings(service, project, bindings)


@pytest.mark.parametrize("options", [{"curved": True}, {"block": True}])
def test_curves_and_unresolved_block_components_have_no_exact_axis_proof(options):
    _, project, _, _ = axis_project(**options)
    assert source_axes(project)
    assert all(
        "source_axis_provenance" not in feature["properties"]
        for feature in source_axes(project)
    )


def test_analytic_distance_threshold_interior_endpoints_and_trace_basis():
    service, project, source, _ = axis_project()
    binding = binding_for(project, source_axes(project)[0])
    project = prepared(service, project, [binding])
    checker = PositionChecker(project)
    cases = [(Point(8, 50), 1.0), (Point(9.5, 50), 0.0), (Point(7, -4), 4.0)]
    for center, distance in cases:
        entry = network_rule_entries(checker.networks, center, "shrub")[0]
        assert entry.actual_distance_m == pytest.approx(distance)
        assert entry.nearest_features[0].axis_evidence.binding == binding
        assert entry.nearest_features[0].axis_evidence.axis_distance_m == pytest.approx(
            distance + 1 if distance else 0.5
        )
    assert checker.networks.first_violation(Point(8, 50), "shrub") is None
    assert checker.networks.first_violation(Point(8.001, 50), "shrub") is not None
    trace = position_rule_trace(checker, project, 8, 50, "shrub")
    assert trace.basis.source_content_sha256 == sha256(source).hexdigest()
    assert trace.basis.geometry_version == project.geometry_version
    safe = checker.hard_safe_area(box(0, 1, 20, 90), 0.1, "shrub")
    assert not safe.contains(Point(8.5, 50))


def test_farther_wider_axis_and_all_zero_distance_ties_are_preserved():
    service, project, _, _ = axis_project(x_positions=(5, 6, 7))
    axes = source_axes(project)
    bindings = [
        binding_for(project, axes[0], 2),
        binding_for(project, axes[1], 10),
        binding_for(project, axes[2], 10),
    ]
    project = prepared(service, project, bindings)
    checker = PositionChecker(project)
    entries = network_rule_entries(checker.networks, Point(4.5, 50), "shrub")
    assert len(entries) == 2
    large = next(
        entry
        for entry in entries
        if entry.nearest_features[0].axis_evidence.binding.outside_diameter_m == 10
    )
    assert large.actual_distance_m == 0
    assert len(large.nearest_features) == 2
    # At x=0 the nearest centerline (x=5,r=1) passes; x=6,r=5 is exactly
    # on the shrub threshold. Moving 1 mm exposes the farther wider failure.
    assert checker.networks.first_violation(Point(0, 50), "shrub") is None
    violation = checker.networks.first_violation(Point(0.001, 50), "shrub")
    assert violation is not None and violation[1] == pytest.approx(0.999)


def test_binding_does_not_confirm_other_same_layer_entities_and_stale_binding_limits_release():
    service, project, _, _ = axis_project(x_positions=(10, 50))
    binding = binding_for(project, source_axes(project)[0])
    project = prepared(service, project, [binding])
    entries = network_rule_entries(
        PositionChecker(project).networks, Point(5, 50), "shrub"
    )
    assert {entry.status for entry in entries} == {"passed", "not_checked"}
    project.plan.objects = [PlanObject(id="plant", x=5, y=50, kind="shrub", radius=0.2)]
    assert release_network_issues(project)
    # No stale feature properties or previously saved status can preserve a
    # confirmation after the immutable identity/geometry basis changes.
    project.source_file.content_sha256 = "f" * 64
    assert all(
        entry.status == "not_checked"
        for entry in network_rule_entries(
            PositionChecker(project).networks, Point(5, 50), "shrub"
        )
    )


def test_size_change_invalidates_geometry_and_fresh_release_uses_new_surface():
    service, project, _, invalidated = axis_project()
    binding = binding_for(project, source_axes(project)[0], 2)
    project = prepared(service, project, [binding])
    project.plan.objects = [PlanObject(id="plant", x=7, y=50, kind="shrub", radius=0.2)]
    service.repository.save(project)
    assert release_network_issues(project) == []
    before_version = project.geometry_version
    changed = binding.model_copy(update={"outside_diameter_m": 6})
    project = save_bindings(service, project, [changed])
    assert project.geometry is None
    assert project.geometry_version == before_version + 1
    assert len(project.plan.objects) == 1
    project.geometry = ShapelyGeometryEngine().calculate(project)
    assert any(
        issue.code == "NETWORK_CLEARANCE_FAILED"
        for issue in release_network_issues(project)
    )
    assert invalidated[-1] == project.id


def test_duplicate_or_unused_source_chain_cannot_validate_even_without_mapping_route():
    _, project, _, _ = axis_project()
    axis = source_axes(project)[0]
    binding = binding_for(project, axis)
    layer = next(layer for layer in project.layers if layer.source_name == "utility")
    layer.mapped_kind = "utility"
    layer.utility_axis_bindings = [binding]
    with pytest.raises(ValueError):
        validate_axis_bindings(project, [])
    with pytest.raises(ValueError):
        validate_axis_bindings(project, [axis, deepcopy(axis)])


def test_confirmed_axis_large_footprint_is_not_untyped_but_root_review_remains():
    service, project, _, _ = axis_project()
    binding = binding_for(project, source_axes(project)[0])
    project = prepared(service, project, [binding])
    checker = PositionChecker(project)
    assert checker.networks.first_violation(Point(7.5, 50), "shrub") is None
    assert checker.advisory(7.5, 50, 3) is None
    assert checker.growth_advisory(7.5, 50, 0.5, 3).code == "ROOT_UTILITY_REVIEW"
    project.layers = [
        layer.model_copy(update={"utility_axis_bindings": []})
        for layer in project.layers
    ]
    assert (
        PositionChecker(project).advisory(7.5, 50, 3).code == "UNTYPED_UTILITY_REVIEW"
    )


def test_visibility_only_old_client_preserves_binding_without_rereading_source(
    monkeypatch,
):
    service, project, _, invalidated = axis_project()
    binding = binding_for(project, source_axes(project)[0])
    project = prepared(service, project, [binding])
    before_version = project.geometry_version
    invalidated.clear()
    monkeypatch.setattr(
        service.dxf_reader,
        "read",
        lambda *_: pytest.fail("visibility is not a new interpretation"),
    )
    layer = next(layer for layer in project.layers if layer.source_name == "utility")
    saved = service.save_mappings(
        project.id,
        [
            LayerMapping(
                layer_id=item.id,
                kind=item.mapped_kind,
                visible=False if item.id == layer.id else item.visible,
            )
            for item in project.layers
            if item.mapped_kind is not None
        ],
    )
    assert next(
        layer for layer in saved.layers if layer.source_name == "utility"
    ).utility_axis_bindings == [binding]
    assert saved.geometry_version == before_version
    assert invalidated == []


def test_new_interpretation_rebuilds_from_original_not_stale_cached_geometry():
    service, project, source, _ = axis_project()
    original = deepcopy(source_axes(project)[0])
    binding = binding_for(project, original)
    source_axes(project)[0]["geometry"]["coordinates"][0][0] = 99
    service.repository.save(project, source=source)
    saved = save_bindings(service, project, [binding])
    assert source_axes(saved)[0] == original
    saved.geometry = ShapelyGeometryEngine().calculate(saved)
    assert (
        network_rule_entries(PositionChecker(saved).networks, Point(7, 50), "shrub")[
            0
        ].status
        == "passed"
    )
