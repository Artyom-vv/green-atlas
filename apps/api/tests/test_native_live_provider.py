"""Integration seams with a deterministic measurement stub, not CAD acceptance."""

from itertools import count
from types import SimpleNamespace

import pytest
from test_native_live_client import session

from app.composition import create_runtime
from app.dxf_import.contracts import NativeAreaProposalReview, SourceFile
from app.dxf_import.layers.contracts import Layer, LayerKind
from app.geometry.contracts import GeometrySnapshot
from app.geometry.networks.utility_contracts import UtilityContext
from app.native_query.contracts import NativeObjectMeasurement, NativePointMeasurement
from app.native_query.live.live_inventory import LiveInventory, query_objects
from app.native_query.measurement.live_provider import LiveNativeGeometryEngine
from app.operations.progress import OperationCancelled
from app.planning.changes.contracts import PlanChangeSetApplyRequest, PlanChangeSetDraft
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
                    capability="area"
                    if target.capability == "closed_area"
                    else target.capability,
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
    from app.native_query.search.domain_cells import Cell, certify_cell

    engine, _, project = setup
    project.layers[1].mapping_confirmed = False
    engine.prepare_positions(project, [(11, 0), (13, 0)], reach_m=5)
    assert (
        engine.placement_advisory_detail(project, 11, 0, 0.5).code
        == "NATIVE_LOCAL_UNKNOWN"
    )
    assert (
        engine.placement_advisory_detail(project, 13, 0, 0.5).code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    for x, state in ((11, "unknown"), (13, "available")):
        verdict = certify_cell(
            Cell(x, 0, 0),
            engine._cache[(x, 0)][1],
            engine._layers,
            set(),
            1,
            "shrub",
            0.5,
            0,
            0,
            5,
        )
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
        return next(
            target.capability
            for target in client.calls[-1].targets
            if target.route == "BB"
        )

    assert building_capability() == "area"
    review = NativeAreaProposalReview(
        id="area-proposal/BB",
        source={"handle": "BB", "instance_chain": []},
        layer="Здания",
        source_path_content_sha256="a" * 64,
        proposal_sha256="b" * 64,
        closure_gap_m=0.01,
        area_m2=200,
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
    assert engine.position_violation(project, 26, 0, 0.5, "shrub").required == 2
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
    assert engine.position_violation(project, 26, 0, 0.5, kind).required == 2
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


def test_mixed_zero_role_survives_cold_snapshot_and_preserves_unknown_neighbor(
    setup: tuple[LiveNativeGeometryEngine, StubNative, Project],
    tmp_path,
) -> None:
    from unittest.mock import Mock

    from test_hybrid_search import populate

    from app.dxf_import.layers.categories import LayerCategory
    from app.dxf_import.review.contracts import SourceObjectDecision
    from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
    from app.native_query.preparation.prepared_snapshot import (
        PreparedStore,
        prepare_snapshot,
    )

    live, client, project = setup
    populate(project)
    assert project.source_file is not None and project.geometry is not None
    zero = project.layers[1]
    zero.source_name = "0"
    zero.category = LayerCategory.MIXED_SOURCE
    zero.mapped_kind, zero.mapping_confirmed, zero.object_count = None, False, 2
    project.geometry.feature_collection["features"][1]["properties"].update(
        source_layer="0",
        entity_type="HATCH",
    )
    inventory = live.inventory.objects
    live.inventory = live.inventory.model_copy(
        update={
            "objects": (
                inventory[0],
                inventory[1].model_copy(
                    update={"layer": "0", "curve": False, "entity_type": "AcDbHatch"}
                ),
                inventory[2],
                inventory[1].model_copy(
                    update={
                        "route": "DD",
                        "layer": "0",
                        "curve": False,
                        "entity_type": "AcDbBlockReference",
                        "bounds": (50, -1, 52, 1),
                        "error": "unreadable native INSERT",
                    }
                ),
            )
        }
    )
    project.source_file.object_decisions = [
        SourceObjectDecision(
            source={"handle": "BB", "instance_chain": []},
            geometry_sha256="a" * 64,
            interpretation="area",
            kind="building",
        )
    ]
    original = project.model_dump()
    snapshot = prepare_snapshot(live, project)
    store = PreparedStore(tmp_path / "object-roles")
    store.save(snapshot)
    stored = store.load(snapshot.key)
    assert stored is not None
    local = PreparedGeometryEngine(stored, project)
    client.inspect = Mock(
        side_effect=AssertionError("Локальная проверка не обращается в CAD")
    )
    occupied = local.position_violation(project, 5, 0, 0.5, "shrub")
    assert occupied is not None and occupied.code == "NATIVE_OCCUPIED"
    assert occupied.source_layer == "0" and occupied.source_feature_ids == ("BB",)
    unknown = local.placement_advisory_detail(project, 51, 0, 0.5, "shrub")
    assert unknown is not None and unknown.code == "NATIVE_LOCAL_UNKNOWN"
    assert unknown.source_layer == "0" and unknown.source_feature_ids == ("DD",)
    coverage = local.source_coverage(project)
    assert coverage.prepared_key == snapshot.key
    assert set(coverage.layers) == {layer.source_name for layer in project.layers}
    assert (
        coverage.layers["0"].physical_count == 1
        and coverage.layers["0"].unresolved_count == 1
    )
    assert coverage.layers["0"].unresolved[0].routes == ["DD"]
    evidence = local.explain_position(project, 5, 0, 0.5, "shrub")
    assert any(
        cause.source_layer == "0" and cause.source_feature_ids == ["BB"]
        for cause in evidence.causes
    )
    assert project.model_dump() == original
    client.inspect.assert_not_called()
    reference = project.source_file.object_decisions[0].model_copy(
        update={"interpretation": "reference", "kind": None}
    )
    project.source_file.object_decisions = [reference]
    client.inspect = Mock()
    contextual = PreparedGeometryEngine(prepare_snapshot(live, project), project)
    assert not any(item.routes == ("BB",) for item in contextual._objects)
    assert contextual.source_coverage(project).layers["0"].context_count == 1
    assert any(item.routes == ("DD",) for item in contextual._objects)


@pytest.mark.parametrize(
    "recipe,readable,used,expected_use",
    [
        (False, True, False, "unresolved"),
        (True, False, False, "unresolved"),
        (True, True, False, "context"),
        (True, True, True, "topology"),
    ],
)
def test_boundary_participation_requires_recipe_and_actual_native_evidence(
    setup: tuple[LiveNativeGeometryEngine, StubNative, Project],
    recipe: bool,
    readable: bool,
    used: bool,
    expected_use: str,
) -> None:
    from test_hybrid_search import feature, populate

    from app.dxf_import.layers.categories import LayerCategory
    from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
    from app.native_query.preparation.face_contracts import TopologyRecipe
    from app.native_query.preparation.prepared_snapshot import prepare_snapshot

    live, _, project = setup
    populate(project)
    assert project.source_file is not None and project.geometry is not None
    name = "Граница растительности и грунта"
    project.layers.append(
        Layer(
            id="boundary-support",
            source_name=name,
            suggested_kind=LayerKind.IGNORE,
            mapped_kind=LayerKind.IGNORE,
            mapping_confirmed=False,
            category=LayerCategory.SURFACE_BOUNDARY,
            color="#777777",
            object_count=1,
        )
    )
    live.inventory = live.inventory.model_copy(
        update={
            "objects": (
                *live.inventory.objects,
                live.inventory.objects[1].model_copy(
                    update={
                        "route": "DD",
                        "layer": name,
                        "bounds": (-80, -80, 80, 80),
                        "error": "" if readable else "unreadable native curve",
                    }
                ),
            )
        }
    )
    project.geometry.feature_collection["features"].append(
        feature(
            "DD",
            name,
            {"type": "LineString", "coordinates": [[-80, -80], [80, 80]]},
        )
    )
    if recipe:
        project.source_file.preparation_recipe = TopologyRecipe(
            source_sha256=project.source_file.content_sha256,
            companion_layers=(name,),
        )
    if used:
        data = live.inventory.model_dump(by_alias=True)
        data.update(
            face_policy="native-planar-faces/2",
            companion_layers=(name,),
            faces=[
                {
                    "id": 1,
                    "anchor": "BB",
                    "layer": "Здания",
                    "routes": ["BB", "DD"],
                    "physical_routes": ["BB"],
                    "area": 200,
                    "bounds": [0, -10, 10, 10],
                    "display": [
                        [0, -10, 0],
                        [10, -10, 0],
                        [10, 10, 0],
                        [0, 10, 0],
                        [0, -10, 0],
                    ],
                    "sampling_tolerance_units": 0,
                }
            ],
        )
        live.inventory = LiveInventory.model_validate(data)
    snapshot = prepare_snapshot(live, project)
    record = next(
        record for record in snapshot.records if record.item.routes == ("DD",)
    )
    assert record.participation is not None and record.participation.use == expected_use
    local = PreparedGeometryEngine(snapshot, project)
    assert any(item.routes == ("DD",) for item in local._source_objects)
    assert any(item.routes == ("DD",) for item in local._objects) == (
        expected_use == "unresolved"
    )
    advice = local.placement_advisory_detail(project, 50, 0, 0.5, "shrub")
    assert advice is not None and advice.code == (
        "NATIVE_LOCAL_UNKNOWN"
        if expected_use == "unresolved"
        else "SOURCE_GEOMETRY_PARTIAL"
    )
    coverage = local.source_coverage(project).layers[name]
    assert coverage.topology_count == int(used)
    assert coverage.context_count == int(expected_use == "context")
    assert coverage.unresolved_count == int(expected_use == "unresolved")
    assert coverage.physical_count == 0
    assert (
        not project.layers[-1].mapping_confirmed
        and project.layers[-1].mapped_kind == "ignore"
    )


@pytest.mark.parametrize("flag", ["prepared_topology", "prepared_boundary"])
def test_boundary_use_proof_requires_current_prepared_key(flag: str) -> None:
    from pydantic import ValidationError

    from app.native_query.policy.source_participation import classify_source_use

    args = {flag: True}
    with pytest.raises(ValidationError):
        classify_source_use(
            None,
            source_sha256="a" * 64,
            prepared_key=None,
            source_layer="Граница улицы",
            **args,
        )
    use = classify_source_use(
        None,
        source_sha256="a" * 64,
        prepared_key="b" * 64,
        source_layer="Граница улицы",
        **args,
    )
    assert use.current("a" * 64, "b" * 64)
    assert not use.current("c" * 64, "b" * 64)
    assert not use.current("a" * 64, "c" * 64)


def test_object_role_materializer_never_declares_open_line_a_closed_area() -> None:
    from app.dxf_import.review.contracts import SourceObjectDecision
    from app.native_query.live.live_inventory import QueryObject
    from app.native_query.policy.source_participation import (
        classify_source_use,
        materialize_source_roles,
        source_object_key,
    )

    source = Layer(
        id="zero",
        source_name="0",
        suggested_kind="ignore",
        mapped_kind=None,
        mapping_confirmed=False,
        object_count=2,
        color="#333333",
    )
    item = QueryObject(
        routes=("CA/A1",), layer="0", curve=True, error="", bounds=(0, 0, 10, 0)
    )
    decision = SourceObjectDecision(
        source={"handle": "A1", "instance_chain": ["CA"]},
        geometry_sha256="a" * 64,
        interpretation="linear",
        kind="building",
    )
    use = classify_source_use(
        source, source_sha256="a" * 64, prepared_key="b" * 64, decision=decision
    )
    layers = {"0": source}
    linear = item.model_copy(update={"reviewed_linear": True})
    sources, calculation = materialize_source_roles(
        (linear,), layers, {source_object_key(linear): use}
    )
    assert sources == calculation and calculation[0].routes == ("CA/A1",)
    assert calculation[0].target(area=True).capability == "curve"
    assert layers[calculation[0].layer].mapped_kind == "building"
    assert layers[calculation[0].layer].source_name == "0"
    assert source.mapped_kind is None and not source.mapping_confirmed


def test_site_border_keeps_manual_and_automatic_checks_when_used_as_companion(
    setup: tuple[LiveNativeGeometryEngine, StubNative, Project],
    tmp_path,
) -> None:
    from shapely.geometry import Point, shape
    from test_hybrid_search import populate

    from app.native_query.measurement.prepared_provider import PreparedGeometryEngine
    from app.native_query.policy.source_participation import SourceUse
    from app.native_query.preparation.face_contracts import TopologyRecipe
    from app.native_query.preparation.prepared_snapshot import (
        PreparedStore,
        prepare_snapshot,
    )

    live, _, project = setup
    populate(project)
    assert project.source_file is not None
    project.source_file.preparation_recipe = TopologyRecipe(
        source_sha256=project.source_file.content_sha256,
        companion_layers=("Граница",),
    )
    snapshot = prepare_snapshot(live, project)
    site = next(record for record in snapshot.records if record.item.routes == ("AA",))
    assert (
        site.participation is not None and site.participation.use == SourceUse.PHYSICAL
    )
    assert site.participation.kind == LayerKind.SITE_BORDER
    store = PreparedStore(tmp_path / "site-companion")
    store.save(snapshot)
    stored = store.load(snapshot.key)
    assert stored is not None
    local = PreparedGeometryEngine(stored, project)
    rejected = local.position_violation(project, 105, 0, 0.5, "shrub")
    assert rejected is not None and rejected.code == "NATIVE_OUTSIDE_SITE"
    domain = local.automatic_safe_geometry(
        project, rectangle(-110, -5, 110, 5), 0.5, "shrub"
    )
    assert shape(domain).covers(Point(50, 0))
    assert not shape(domain).covers(Point(105, 0))


@pytest.mark.parametrize("individual_decision", [False, True])
def test_source_participation_shares_full_values_without_losing_routes_or_proof(
    setup: tuple[LiveNativeGeometryEngine, StubNative, Project],
    individual_decision: bool,
) -> None:
    """Общий DTO экономит память, а отдельное решение сохраняет своё основание."""
    from app.dxf_import.review.contracts import SourceObjectDecision
    from app.native_query.policy.source_participation import (
        SourceUseProof,
        source_object_key,
    )
    from app.native_query.preparation.preparation_identity import preparation_key

    live, client, project = setup
    assert project.source_file is not None
    data = live.inventory.model_dump(mode="json", by_alias=True)
    data["objects"].append(source_row("DD", "Здания", [50, -10, 60, 10]))
    live.inventory = LiveInventory.model_validate(data)
    project.layers[1].object_count = 2
    if individual_decision:
        project.source_file.object_decisions = [
            SourceObjectDecision(
                source={"handle": "DD", "instance_chain": []},
                geometry_sha256="a" * 64,
                interpretation="area",
                kind="building",
            )
        ]
    before = project.model_dump(mode="json")
    live.assert_current(project)
    items = {item.routes: item for item in live._source_objects}
    assert set(items) == {("AA",), ("BB",), ("CC",), ("DD",)}
    assert set(live._source_participations) == {
        source_object_key(item) for item in items.values()
    }
    key = preparation_key(project, live.session, live.linear_layers)
    first = live.source_participation(items[("BB",)], key)
    second = live.source_participation(items[("DD",)], key)
    assert first.use == second.use == "physical"
    assert first.kind == second.kind == LayerKind.BUILDING
    assert first.source_layer == second.source_layer == "Здания"
    assert first.current(live.session.snapshot_sha256, key)
    assert second.current(live.session.snapshot_sha256, key)
    if individual_decision:
        assert second is not first
        assert first.proof == SourceUseProof.CONFIRMED_LAYER
        assert second.proof == SourceUseProof.OBJECT_DECISION
    else:
        assert second is first
    road = live.source_participation(items[("CC",)], key)
    assert road is not first and road.kind == LayerKind.ROAD
    assert project.model_dump(mode="json") == before
    assert not client.calls


def test_source_participation_pool_does_not_bypass_invalid_role(setup) -> None:
    """Недопустимая роль не превращается в ранее сохранённое свидетельство."""
    from pydantic import ValidationError

    live, _, project = setup
    project.layers[1].mapped_kind = "unknown_role"
    with pytest.warns(UserWarning, match="Pydantic serializer warnings"):
        with pytest.raises(ValidationError):
            live.assert_current(project)
    assert live._source_participations == {}
    assert live._basis_key is None


def test_basis_progress_reports_real_scans_and_roles_once(setup, monkeypatch):
    import app.operations.progress as operation_progress

    live, _, project = setup
    clock = count()
    monkeypatch.setattr(operation_progress, "monotonic", lambda: next(clock))
    events = []
    live.assert_current(project, progress=events.append)
    stages = [value.stage for value in events]
    assert stages.index("Восстанавливаем области чертежа") < stages.index(
        "Собираем объекты чертежа"
    )
    scan = [value for value in events if value.stage == "Проверяем объекты захвата"]
    roles = [value for value in events if value.stage == "Проверяем роли объектов"]
    assert [value.processed for value in scan] == list(range(4))
    assert all(value.total == len(live.inventory.objects) for value in scan)
    assert [value.processed for value in roles] == list(range(4))
    assert all(value.total == len(live._source_objects) for value in roles)
    applied = [value for value in events if value.stage == "Применяем роли объектов"]
    assert [value.processed for value in applied] == list(range(4))
    assert all(value.total == len(live._source_objects) for value in applied)
    assert events[-1].stage == "Применяем роли объектов"
    before = tuple(live._source_objects)
    events.clear()
    live.assert_current(project, progress=events.append)
    assert not events
    assert live._source_objects == before


def test_cancelled_role_progress_does_not_publish_partial_basis(setup, monkeypatch):
    import app.operations.progress as operation_progress

    live, _, project = setup
    clock = count()
    monkeypatch.setattr(operation_progress, "monotonic", lambda: next(clock))
    original = project.model_dump(mode="json")

    def stop(value):
        if value.stage == "Проверяем роли объектов" and value.processed == 1:
            raise OperationCancelled("Остановлено пользователем")

    with pytest.raises(OperationCancelled):
        live.assert_current(project, progress=stop)
    assert live._basis_key is None
    assert not live._source_objects
    assert not live._source_participations
    assert project.model_dump(mode="json") == original
    live.assert_current(project)
    assert len(live._source_objects) == 3


def test_source_preparation_defers_spatial_index_and_new_roles_invalidate_it(
    setup, monkeypatch
):
    import app.native_query.measurement.live_provider as provider

    live, client, project = setup
    built = []
    real_index = provider.NativeBoundsIndex

    def build(objects, layers):
        result = real_index(objects, layers)
        built.append(result)
        return result

    monkeypatch.setattr(provider, "NativeBoundsIndex", build)
    live.assert_current(project)
    assert not built
    assert live._bounds_index_cache is None

    live.prepare_positions(project, [(11.0, 0.0)])
    assert len(built) == 1
    assert {target.route for call in client.calls for target in call.targets} == {
        "AA",
        "BB",
    }
    live.prepare_positions(project, [(12.0, 0.0)])
    assert len(built) == 1

    project.layers[1].mapped_kind = LayerKind.IGNORE
    live.assert_current(project)
    assert live._bounds_index_cache is None
    assert len(built) == 1
    client.calls.clear()
    live.prepare_positions(project, [(12.0, 0.0)])
    assert len(built) == 2
    assert all(target.route != "BB" for call in client.calls for target in call.targets)
