"""Integration seams with a deterministic measurement stub, not CAD acceptance."""

from types import SimpleNamespace

import pytest
from test_native_live_client import session

from app.composition import create_runtime
from app.dxf_import.contracts import NativeAreaProposalReview, SourceFile
from app.dxf_import.layer_contracts import Layer, LayerKind
from app.geometry.contracts import GeometrySnapshot
from app.geometry.utility_contracts import UtilityContext
from app.native_query.contracts import NativeObjectMeasurement, NativePointMeasurement
from app.native_query.live_inventory import LiveInventory, query_objects
from app.native_query.live_provider import LiveNativeGeometryEngine
from app.planning.change_contracts import PlanChangeSetApplyRequest, PlanChangeSetDraft
from app.planning.contracts import PlacementCheckRequest, Plan
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project


def rectangle(x0, y0, x1, y1):
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def source_row(route, layer, bounds, **extra):
    return dict(
        route=route,
        layer=layer,
        bounds=bounds,
        entity_type="AcDbPolyline",
        curve=True,
        context=False,
        error="",
        **extra,
    )


class StubNative:
    """Explicit test oracle: building x=[0,10], road x=[20,25], site [-100,100]."""

    stale = False

    def __init__(self):
        self.calls = []

    def inspect(self, session):
        if self.stale:
            raise ValueError("Чертёж изменён")

    def measure(self, session, query):
        self.inspect(session)
        self.calls.append(query)
        objects = []
        for target in query.targets:
            answers = []
            for index, (x, y, z) in enumerate(query.points):
                lo, hi = {"AA": (-100, 100), "BB": (0, 10), "CC": (20, 25)}[
                    target.route
                ]
                inside = lo <= x <= hi
                distance = min(abs(x - lo), abs(x - hi))
                answers.append(
                    NativePointMeasurement(
                        point_index=index,
                        status=0,
                        membership=("occupied" if inside else "outside")
                        if target.capability in {"area", "closed_area"}
                        else "unknown",
                        distance_units=distance,
                        error="",
                    )
                )
            objects.append(
                NativeObjectMeasurement(
                    route=target.route,
                    additional_routes=target.additional_routes,
                    entity_type="AcDbPolyline",
                    layer={"AA": "Граница", "BB": "Здания", "CC": "Дорога"}[
                        target.route
                    ],
                    capability="area" if target.capability == "closed_area" else target.capability,
                    interior_known=target.capability in {"area", "closed_area"},
                    preparation_error="",
                    prepare_ms=1,
                    answers=tuple(answers),
                )
            )
        return SimpleNamespace(objects=tuple(objects))


@pytest.fixture
def setup(tmp_path):
    current = session(tmp_path)
    inventory = LiveInventory.model_validate(
        {
            "schema": "green-atlas.live-inventory/1",
            "objects": [
                source_row("AA", "Граница", [-100, -100, 100, 100]),
                source_row("BB", "Здания", [0, -10, 10, 10]),
                source_row("CC", "Дорога", [20, -10, 25, 10]),
            ],
            "groups": [],
        }
    )
    client = StubNative()
    engine = LiveNativeGeometryEngine(current, client, inventory=inventory)
    project = Project(
        id="native-demo",
        name="Same capture",
        source_file=SourceFile(
            name="source.dxf",
            content_sha256=current.snapshot_sha256,
            size=1,
            imported_at="now",
            dxf_version="live",
            units="м",
            entity_count=3,
            accept_partial_geometry=True,
        ),
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []},
            calculation_scope="available_data",
        ),
        plan=Plan(),
        layers=[
            Layer(
                id=str(i),
                source_name=name,
                object_count=1,
                color="#333333",
                suggested_kind=role,
                mapped_kind=role,
                mapping_confirmed=True,
            )
            for i, (name, role) in enumerate(
                [("Граница", "site_border"), ("Здания", "building"), ("Дорога", "road")]
            )
        ],
        planting_zones=[
            PlantingZoneAssignment(
                id="work", label="Участок", geometry=rectangle(-90, -80, 90, 80)
            )
        ],
    )
    return engine, client, project


def test_configured_review_horizon_matches_manual_and_area_checks(setup):
    from app.native_query.domain_cells import Cell, certify_cell

    engine, _, project = setup
    project.layers[1].mapping_confirmed = False
    engine.prepare_positions(project, [(11, 0), (13, 0)], reach_m=5)
    assert engine.placement_advisory_detail(project, 11, 0, .5).code == "NATIVE_LOCAL_UNKNOWN"
    assert engine.placement_advisory_detail(project, 13, 0, .5).code == "SOURCE_GEOMETRY_PARTIAL"
    for x, state in ((11, "unknown"), (13, "available")):
        verdict = certify_cell(Cell(x, 0, 0), engine._cache[(x, 0)][1],
                               engine._layers, set(), 1, "shrub", .5, 0, 0, 5)
        assert verdict.state == state


def test_manual_bulk_same_building_road_and_clear_points(setup):
    from test_hybrid_search import populate
    engine, client, project = setup
    populate(project)
    points = [(5.0, 0.0), (23.0, 0.0), (50.0, 0.0)]
    engine.prepare_positions(project, points)
    assert engine.position_violation(
        project, 5, 0, 0.5, "shrub"
    ).source_feature_ids == ("BB",)
    assert engine.position_violation(
        project, 23, 0, 0.5, "shrub"
    ).source_feature_ids == ("CC",)
    assert engine.position_violation(project, 50, 0, 0.5, "shrub") is None
    assert (
        engine.placement_advisory_detail(project, 50, 0, 0.5).code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    assert len(client.calls) == 1
    domain = rectangle(-90, -80, 90, 80)
    from shapely.geometry import Point, shape
    safe = engine.automatic_safe_geometry(project, domain, 0.5, "shrub")
    assert shape(safe).covers(Point(50, 0))
    assert not shape(safe).covers(Point(5, 0))
    assert not shape(safe).covers(Point(23, 0))


def test_reviewed_closure_switches_native_request_and_invalidates_cached_answers(setup):
    engine, client, project = setup
    point = [(5.0, 0.0)]
    engine.prepare_positions(project, point)

    def building_capability():
        return next(target.capability for target in client.calls[-1].targets if target.route == "BB")

    assert building_capability() == "area"
    review = NativeAreaProposalReview(
        id="area-proposal/BB", source={"handle": "BB", "instance_chain": []},
        layer="Здания", source_path_content_sha256="a" * 64,
        proposal_sha256="b" * 64, closure_gap_m=0.01, area_m2=200,
        decision="accepted",
    )
    project.source_file.native_area_proposals.append(review)
    engine.prepare_positions(project, point)
    assert len(client.calls) == 2
    assert building_capability() == "closed_area"
    review.decision = "rejected"
    engine.prepare_positions(project, point)
    assert len(client.calls) == 3
    assert building_capability() == "area"


def test_shrub_and_tree_use_different_existing_clearance_rules(setup):
    engine, _, project = setup
    assert engine.position_violation(project, 12, 0, 0.5, "tree").required == 5
    assert engine.position_violation(project, 12, 0, 0.5, "shrub") is None


def confirmed_utility(project, network_type):
    layer = project.layers[2]
    layer.mapped_kind = LayerKind.UTILITY
    layer.utility_context = UtilityContext(
        network_type=network_type,
        geometry_reference="outer_surface",
        installation="underground",
        review_status="confirmed",
        source_reference="explicit test fixture, not a drawing inference",
        confirmed_by="test",
    )


def test_missing_shrub_rule_does_not_invalidate_confirmed_tree_rule(setup):
    engine, _, project = setup
    confirmed_utility(project, "water")
    # At 3 m both pass: the tree uses its table row, the shrub the explicit
    # 2 m project assumption. Neither invents missing source characteristics.
    assert engine.position_violation(project, 28, 0, 0.5, "tree") is None
    assert (
        engine.placement_advisory_detail(project, 28, 0, 0.5, "tree").code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    shrub = engine.placement_advisory_detail(project, 28, 0, 0.5, "shrub")
    assert shrub.code == "SOURCE_GEOMETRY_PARTIAL"
    assert engine.position_violation(project, 26, 0, .5, "shrub").required == 2
    assert engine.future_growth_advisory_detail(project, 28, 0, 1, 1, "tree") is None
    assert engine.future_growth_advisory_detail(project, 28, 0, 1, 1, "shrub") is None


def test_confirmed_cable_preserves_different_tree_and_shrub_setbacks(setup):
    engine, _, project = setup
    confirmed_utility(project, "power_cable")
    violation = engine.position_violation(project, 26, 0, 0.5, "tree")
    assert violation.actual == 1 and violation.required == 2
    assert engine.position_violation(project, 26, 0, 0.5, "shrub") is None
    assert (
        engine.placement_advisory_detail(project, 26, 0, 0.5, "shrub").code
        == "SOURCE_GEOMETRY_PARTIAL"
    )


@pytest.mark.parametrize("kind", ["tree", "shrub"])
def test_unconfirmed_network_is_measured_with_project_distance(setup, kind):
    engine, _, project = setup
    project.layers[2].mapped_kind = LayerKind.UTILITY
    advisory = engine.placement_advisory_detail(project, 28, 0, 0.5, kind)
    assert advisory.code == "SOURCE_GEOMETRY_PARTIAL"
    assert engine.position_violation(project, 26, 0, .5, kind).required == 2
    assert project.layers[2].utility_context is None


@pytest.mark.parametrize("kind, allowed", [("tree", True), ("shrub", True)])
def test_service_bulk_preview_checks_only_the_requested_plant_kind(
    tmp_path, setup, kind, allowed
):
    engine, _, project = setup
    confirmed_utility(project, "water")
    runtime = create_runtime(tmp_path / "project.sqlite3", geometry_engine=engine)
    try:
        runtime.project_repository.create(project)
        draft = PlanChangeSetDraft(
            base_plan_version=1,
            source="pattern",
            label="Посадка",
            operations=[
                {
                    "type": "add",
                    "object": {"kind": kind, "x": 28, "y": 0, "radius": 0.5},
                }
            ],
        )
        preview = runtime.application.changes.preview_on_snapshot(project, draft)
        assert preview.can_apply is allowed
        # Rejected draft objects can remain visible in additions for inspection;
        # can_apply and the candidate verdict, not visibility, authorize saving.
        assert preview.candidate_results[0].code == (
            "SOURCE_GEOMETRY_PARTIAL" if allowed else "NATIVE_LOCAL_UNKNOWN"
        )
        if not allowed:
            with pytest.raises(ValueError):
                runtime.application.changes.apply_change_set(
                    project.id,
                    PlanChangeSetApplyRequest(
                        preview_id=preview.id,
                        digest=preview.digest,
                        base_plan_version=1,
                    ),
                )
        # This is a preview, not a silent change to the saved planting plan.
        assert runtime.application.get(project.id).plan.objects == []
    finally:
        runtime.close()


def test_spatial_packets_keep_the_same_native_answers_as_individual_queries(setup):
    engine, client, project = setup
    points = [(float(x), 0.0) for x in range(-60, 65, 5)]
    engine.prepare_positions(project, points)
    actual = [
        (
            engine.position_violation(project, x, y, 0.5, "shrub"),
            engine.placement_advisory_detail(project, x, y, 0.5),
        )
        for x, y in points
    ]
    packet_measurements = sum(len(q.points) * len(q.targets) for q in client.calls)
    assert packet_measurements < len(points) * 3
    engine._cache.clear()
    for index, (x, y) in enumerate(points):
        expected = (
            engine.position_violation(project, x, y, 0.5, "shrub"),
            engine.placement_advisory_detail(project, x, y, 0.5),
        )
        assert expected == actual[index]


def test_failed_spatial_packet_does_not_publish_partial_points(setup, monkeypatch):
    engine, client, project = setup
    measure = client.measure
    count = 0

    def fail_second(session, query):
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("native packet failed")
        return measure(session, query)

    monkeypatch.setattr(client, "measure", fail_second)
    with pytest.raises(ValueError, match="native packet failed"):
        engine.prepare_positions(project, [(float(x), 0.0) for x in range(-60, 65, 5)])
    assert not engine._cache


def test_source_warning_is_not_duplicated_as_growth_conflict(setup):
    engine, _, project = setup
    assert (
        engine.placement_advisory_detail(project, 50, 0, 0.5).code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    assert engine.future_growth_advisory_detail(project, 50, 0, 1, 1) is None
    assert (
        engine.future_growth_advisory_detail(project, 12, 0, 3, 1).code
        == "GROWTH_NATIVE_CLEARANCE"
    )


def test_reviewed_curb_uses_native_distance_not_invented_interior(setup):
    engine, client, project = setup
    linear = LiveNativeGeometryEngine(
        engine.session,
        client,
        inventory=engine.inventory,
        linear_layers=frozenset({"Дорога"}),
    )
    # Broad extents include the point, but a curb is not an occupied surface.
    assert linear.position_violation(project, 23, 0, 0.5, "shrub") is None
    assert (
        linear.placement_advisory_detail(project, 23, 0, 0.5).code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    assert (
        linear.position_violation(project, 24.5, 0, 0.5, "shrub").code
        == "NATIVE_CLEARANCE"
    )
    assert any(
        t.route == "CC" and t.capability == "curve"
        for q in client.calls
        for t in q.targets
    )
    # Cannot apply the same override to a broken building.
    invalid = LiveNativeGeometryEngine(
        engine.session,
        client,
        inventory=engine.inventory,
        linear_layers=frozenset({"Здания"}),
    )
    with pytest.raises(ValueError, match="Назначение линейного"):
        invalid.prepare_positions(project, [(5.0, 0.0)])


def test_no_display_fallback_on_wrong_capture_or_stale_session(setup):
    engine, client, project = setup
    engine.prepare_positions(project, [(50.0, 0.0)])
    client.stale = True
    with pytest.raises(ValueError, match="изменён"):
        engine.prepare_positions(project, [(50.0, 0.0)])
    assert not engine._cache
    project.source_file.content_sha256 = "d" * 64
    with pytest.raises(ValueError, match="разным захватам"):
        engine.calculate(project)


def test_failed_display_object_still_in_native_inventory(setup):
    engine, _, project = setup
    project.layers[1].geometry_complete = False
    project.layers[1].unreadable_geometry_count = 1
    assert (
        engine.position_violation(project, 5, 0, 0.5, "shrub").code == "NATIVE_OCCUPIED"
    )


def test_distant_unknown_does_not_block_local_work_but_near_unknown_does(setup):
    engine, _, project = setup
    project.layers[1].mapping_confirmed = False
    assert (
        engine.placement_advisory_detail(project, 5, 0, 0.5).code
        == "NATIVE_LOCAL_UNKNOWN"
    )
    assert (
        engine.placement_advisory_detail(project, 50, 0, 0.5).code
        == "SOURCE_GEOMETRY_PARTIAL"
    )


def test_remote_broken_site_does_not_hide_known_outside_verdict(setup):
    engine, _, project = setup
    unknown = source_row("DD", "Граница", [300, -10, 310, 10])
    unknown["error"] = "broken native contour"
    engine.inventory = LiveInventory.model_validate(
        {
            "schema": "green-atlas.live-inventory/1",
            "objects": [
                *(item.model_dump() for item in engine.inventory.objects),
                unknown,
            ],
            "groups": [],
        }
    )
    assert engine.position_violation(project, 200, 0, 0.5).code == "NATIVE_OUTSIDE_SITE"
    # The same broad phase never certifies a point inside an unknown envelope.
    assert engine.position_violation(project, 305, 0, 0.5) is None
    advice = engine.placement_advisory_detail(project, 305, 0, 0.5)
    assert advice.code == "NATIVE_LOCAL_UNKNOWN"
    assert advice.source_feature_ids == ("DD",)
    assert engine.position_violation(project, 50, 0, 0.5) is None


def test_broad_phase_keeps_group_interior_far_from_individual_walls():
    inventory = LiveInventory.model_validate(
        {
            "schema": "green-atlas.live-inventory/1",
            "objects": [
                source_row("A/1", "walls", [0, 0, 100, 0]),
                source_row("A/2", "walls", [0, 0, 0, 100]),
                source_row("A/3", "walls", [100, 0, 100, 100]),
                source_row("A/4", "walls", [0, 100, 100, 100]),
            ],
            "groups": [{"routes": ["A/1", "A/2", "A/3", "A/4"], "error": ""}],
        }
    )
    items = query_objects(inventory)
    assert len(items) == 1 and items[0].distance_to_bounds(50, 50) == 0
    assert items[0].target(area=True).additional_routes == ("A/2", "A/3", "A/4")
    assert (
        len(query_objects(inventory, set())) == 4
    )  # Utility curves are not grouped into surfaces.


def test_existing_service_preview_save_reopen_and_stale_apply(tmp_path, setup):
    engine, client, project = setup
    runtime = create_runtime(tmp_path / "project.sqlite3", geometry_engine=engine)
    try:
        runtime.project_repository.create(project)
        app = runtime.application
        manual = app.manual.check_placement(
            project.id, PlacementCheckRequest(kind="shrub", x=50, y=0, radius=0.5)
        )
        assert manual.allowed and manual.code == "SOURCE_GEOMETRY_PARTIAL"
        draft = PlanChangeSetDraft(
            base_plan_version=1,
            source="pattern",
            label="Посадка",
            operations=[
                {
                    "type": "add",
                    "object": {"kind": "shrub", "x": 50, "y": 0, "radius": 0.5},
                },
                {
                    "type": "add",
                    "object": {"kind": "shrub", "x": 60, "y": 0, "radius": 0.5},
                },
            ],
        )
        preview = app.changes.preview_on_snapshot(app.get(project.id), draft)
        assert preview.can_apply and len(preview.additions) == 2
        request = PlanChangeSetApplyRequest(
            preview_id=preview.id, digest=preview.digest, base_plan_version=1
        )
        client.stale = True
        with pytest.raises(ValueError, match="изменён"):
            app.changes.apply_change_set(project.id, request)
        assert app.get(project.id).plan.objects == []
        client.stale = False
        saved = app.changes.apply_change_set(project.id, request)
        assert len(saved.plan.objects) == 2
    finally:
        runtime.close()
    reloaded = create_runtime(tmp_path / "project.sqlite3", geometry_engine=engine)
    try:
        assert [
            (item.x, item.y)
            for item in reloaded.application.get(project.id).plan.objects
        ] == [(50, 0), (60, 0)]
    finally:
        reloaded.close()
