"""Reason provenance and read-only probes; analytic oracle, not CAD acceptance."""

from types import SimpleNamespace

import pytest
from test_native_live_provider import setup as native_setup

from app.native_query.contracts import NativeObjectMeasurement, NativePointMeasurement
from app.native_query.group_measurement import measure_group_curves
from app.native_query.live_inventory import QueryObject, query_objects
from app.native_query.live_rules import MeasuredObstacle
from app.native_query.position_evidence import failure_reason

setup = native_setup


def test_existing_placement_endpoint_can_explain_rejection_without_plan_writes(
    setup, tmp_path
):
    from app.composition import create_runtime
    from app.planning.contracts import PlacementCheckRequest

    engine, _, project = setup
    runtime = create_runtime(tmp_path / "project.sqlite3", geometry_engine=engine)
    try:
        runtime.project_repository.create(project)
        before = runtime.application.get(project.id).model_dump()
        result = runtime.application.manual.check_placement(
            project.id,
            PlacementCheckRequest(
                kind="shrub",
                x=5,
                y=0,
                radius=0.5,
                explain_geometry=True,
            ),
        )
        assert result.status == "blocked"
        assert result.geometry_evidence.state == "excluded"
        assert result.geometry_evidence.causes[0].source_feature_ids == ["BB"]
        assert runtime.application.get(project.id).model_dump() == before
        stale = runtime.application.manual.check_placement(
            project.id,
            PlacementCheckRequest(
                kind="shrub",
                x=5,
                y=0,
                explain_geometry=True,
                state_version=999,
            ),
        )
        assert stale.geometry_evidence is None
    finally:
        runtime.close()


def test_explains_all_nearby_causes_and_does_not_mutate_project(setup):
    engine, _, project = setup
    before = project.model_dump()
    # Between building and road: crown intersects both; preserve both causes
    result = engine.explain_position(project, 15, 0, 0.5, "tree", 6, 0)
    assert result.state == "excluded"
    assert {tuple(c.source_feature_ids) for c in result.causes} == {("BB",), ("CC",)}
    assert all(
        c.measured_distance_m == 5 and c.required_distance_m == 6 for c in result.causes
    )
    assert all(c.query_sent and c.stage == "clearance" for c in result.causes)
    assert project.model_dump() == before


def test_missing_confirmation_is_not_claimed_to_be_an_autocad_error(setup):
    engine, client, project = setup
    project.layers[1].mapping_confirmed = False
    result = engine.explain_position(project, 8, 0, 0.5, "shrub")
    cause = next(c for c in result.causes if c.source_layer == "Здания")
    assert cause.code == "layer_unconfirmed" and cause.stage == "mapping"
    assert not cause.query_sent
    assert all(t.route != "BB" for q in client.calls for t in q.targets)


def test_clear_point_and_stale_identity(setup):
    engine, client, project = setup
    result = engine.explain_position(project, 50, 0, 0.5, "shrub")
    assert result.state == "available" and not result.causes
    client.stale = True
    with pytest.raises(ValueError, match="изменён"):
        engine.explain_position(project, 50, 0, 0.5, "shrub")


@pytest.mark.parametrize(
    "error,expected",
    [
        ("native_extents_invalid", "inventory_object_error"),
        ("", "measurement_missing"),
    ],
)
def test_inventory_and_transport_failures_are_distinct(setup, error, expected):
    _, _, project = setup
    row = MeasuredObstacle(
        QueryObject(
            routes=("BB",),
            layer="Здания",
            bounds=(0, 0, 10, 10),
            curve=True,
            error=error,
        ),
        None,
        None,
    )
    assert failure_reason(row, project.layers[1])[0] == expected


def test_group_limit_is_explicit_and_does_not_construct_an_invalid_curve_target(setup):
    _, _, project = setup
    row = MeasuredObstacle(
        QueryObject(
            routes=tuple(f"{i:X}" for i in range(257)),
            layer="Здания",
            bounds=(0, 0, 10, 10),
            curve=True,
            error="",
        ),
        None,
        None,
    )
    assert failure_reason(row, project.layers[1])[0] == "query_group_limit"
    row = MeasuredObstacle(
        row.item.model_copy(update={"routes": ("AA", "BB")}), None, None
    )
    assert failure_reason(row, project.layers[1])[0] == "measurement_missing"


def test_area_failure_keeps_curve_measurements_but_never_certifies_interior(setup):
    engine, client, project = setup
    items = [
        i.model_copy(update={"layer": "Здания"}) if i.route == "CC" else i
        for i in engine.inventory.objects
    ]
    inventory = engine.inventory.model_copy(update={"objects": tuple(items)})
    from app.native_query.live_inventory import InventoryGroup

    inventory = inventory.model_copy(
        update={
            "groups": (
                InventoryGroup(routes=("BB", "CC"), error="open native endpoint chain"),
            )
        }
    )
    objects = query_objects(inventory, {"Здания"})
    group = next(i for i in objects if len(i.routes) == 2)
    assert group.curve_routes == ("BB", "CC")
    measurement = measure_group_curves(client, engine.session, group, ((29, 0, 0),))
    assert not measurement.interior_known
    assert measurement.answers[0].distance_units == 4
    assert measurement.answers[0].membership == "unknown"
    assert all(t.capability == "curve" for q in client.calls for t in q.targets)
    assert measurement.preparation_error == "open native endpoint chain"
    # Outside the unresolved interior envelope, the measured 4 m is usable.
    engine.inventory = inventory
    engine._basis_key = None
    outside = engine.explain_position(project, 29, 0, 0.5, "shrub")
    assert outside.state == "available"
    inside = engine.explain_position(project, 15, 0, 0.5, "shrub")
    assert inside.state == "unknown"
    assert any(c.code == "object_interior" for c in inside.causes)


def test_one_failed_member_does_not_invent_distance_for_the_group(setup):
    engine, _, _ = setup
    group = QueryObject(
        routes=("AA", "BB"),
        curve_routes=("AA", "BB"),
        layer="x",
        bounds=(0, 0, 1, 1),
        curve=True,
        error="open native endpoint chain",
    )

    def measure(session, query):
        return SimpleNamespace(
            objects=tuple(
                NativeObjectMeasurement(
                    route=t.route,
                    entity_type="Line",
                    layer="x",
                    capability="curve",
                    interior_known=False,
                    preparation_error="",
                    prepare_ms=0,
                    answers=(
                        NativePointMeasurement(
                            point_index=0,
                            status=0 if t.route == "AA" else 1,
                            membership="unknown",
                            distance_units=9 if t.route == "AA" else None,
                            error="" if t.route == "AA" else "failed",
                        ),
                    ),
                )
                for t in query.targets
            )
        )

    result = measure_group_curves(
        SimpleNamespace(measure=measure), engine.session, group, ((2, 2, 0),)
    )
    assert result.answers[0].distance_units is None
    assert result.answers[0].status != 0


def test_member_error_never_enters_readable_group_path(setup):
    engine, _, _ = setup
    from app.native_query.live_inventory import InventoryGroup

    objects = tuple(
        i.model_copy(update={"error": "unreadable", "layer": "Здания"})
        if i.route == "CC"
        else i
        for i in engine.inventory.objects
    )
    inventory = engine.inventory.model_copy(
        update={
            "objects": objects,
            "groups": (
                InventoryGroup(routes=("BB", "CC"), error="open native endpoint chain"),
            ),
        }
    )
    assert not next(
        i for i in query_objects(inventory) if len(i.routes) == 2
    ).curve_routes
