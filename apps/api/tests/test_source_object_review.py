from copy import deepcopy

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_autocad_live_import import live_area_probe, live_bytes
from test_native_live_provider import setup as native_setup

from app.composition import get_application
from app.dxf_import.layers.contracts import Layer
from app.dxf_import.review.contracts import (
    SourceObjectDecision,
    SourceObjectDecisionRequest,
)
from app.dxf_import.review.objects import decide_object, review_objects
from app.main import app
from app.native_query.live.live_inventory import LiveInventory, query_objects
from app.planning.contracts import Plan, PlanObject
from app.shared.failures import DomainFailure, FailureReason

setup = native_setup


def imported():
    client = TestClient(app)
    project_id = client.post("/api/projects", json={"name": "Object decisions"}).json()[
        "id"
    ]
    url = f"/api/projects/{project_id}"
    response = client.post(
        url + "/source-autocad-live",
        data={"autocad_version": "2027.0.1", "target": "macos-arm64"},
        files={
            "file": ("capture.json", live_bytes(live_area_probe()), "application/json")
        },
    )
    assert response.status_code == 200, response.text
    application = get_application()
    project = application.repository.get(project_id)
    project.plan = Plan(objects=[PlanObject(kind="shrub", x=20, y=0, radius=0.5)])
    application.repository.save(project)
    return client, url, project


def test_per_object_choices_preserve_source_and_plants_and_restore_counts():
    client, url, before = imported()
    page = client.get(url + "/source-object-review").json()
    assert page["total"] >= 1
    item = page["items"][0]
    payload = {key: item[key] for key in ("source", "geometry_sha256")}
    payload["source_sha256"] = page["source_sha256"]
    original_layer = next(
        layer for layer in before.layers if layer.source_name == item["layer"]
    )
    original_geometry = deepcopy(before.geometry.feature_collection)
    previous_version = None
    for interpretation, remaining in [
        ("linear", 0),
        ("linear", 0),
        ("reference", 0),
        ("area", 1),
    ]:
        response = client.post(
            url + "/source-object-review/decision",
            json={**payload, "interpretation": interpretation},
        )
        assert response.status_code == 200, response.text
        current = response.json()
        assert current["plan"] == before.plan.model_dump(mode="json")
        assert current["source_file"]["content_sha256"] == page["source_sha256"]
        layer = next(
            layer
            for layer in current["layers"]
            if layer["source_name"] == item["layer"]
        )
        assert (
            layer["unsupported_geometry_types"].get(item["entity_type"], 0) == remaining
        )
        if previous_version is not None and interpretation == "linear":
            assert current["state_version"] == previous_version
        previous_version = current["state_version"]
    assert (
        layer["unsupported_geometry_types"] == original_layer.unsupported_geometry_types
    )
    assert before.geometry.feature_collection == original_geometry
    assert current["source_file"]["object_decisions"] == []


def test_object_decision_rejects_foreign_capture_hash_or_identity():
    client, url, before = imported()
    page = client.get(url + "/source-object-review").json()
    item = page["items"][0]
    payload = {key: item[key] for key in ("source", "geometry_sha256")}
    payload.update(source_sha256=page["source_sha256"], interpretation="reference")
    for bad in (
        {"source_sha256": "f" * 64},
        {"geometry_sha256": "f" * 64},
        {"source": {"handle": "FFFF", "instance_chain": []}},
    ):
        response = client.post(
            url + "/source-object-review/decision", json={**payload, **bad}
        )
        assert response.status_code == 400, response.text
    current = get_application().repository.get(before.id)
    assert current.source_file.object_decisions == []


def test_object_decision_cannot_override_an_accepted_area():
    client, url, before = imported()
    page = client.get(url + "/source-object-review").json()
    item = page["items"][0]
    proposal = before.source_file.native_area_proposals[0]
    response = client.post(
        url + "/source-native-area/decision",
        json={
            "source_sha256": page["source_sha256"],
            "proposal_id": proposal.id,
            "proposal_sha256": proposal.proposal_sha256,
            "decision": "accepted",
        },
    )
    assert response.status_code == 200, response.text
    response = client.post(
        url + "/source-object-review/decision",
        json={
            "source_sha256": page["source_sha256"],
            "source": item["source"],
            "geometry_sha256": item["geometry_sha256"],
            "interpretation": "reference",
        },
    )
    assert response.status_code == 400


def test_known_linear_and_reference_members_do_not_erase_remaining_group():
    inventory = LiveInventory.model_validate(
        {
            "schema": "green-atlas.live-inventory/1",
            "objects": [
                dict(
                    route=r,
                    layer="Здания",
                    entity_type="AcDbLine",
                    context=False,
                    bounds=[0, 0, 10, 10],
                    curve=True,
                    error="",
                )
                for r in ["AA", "BB", "CC", "DD"]
            ],
            "groups": [
                dict(
                    routes=["AA", "BB", "CC", "DD"], error="open native endpoint chain"
                )
            ],
        }
    )
    objects = query_objects(
        inventory,
        {"Здания"},
        linear_routes=frozenset({"AA"}),
        ignored_routes=frozenset({"BB"}),
    )
    remainder = next(item for item in objects if item.routes == ("CC", "DD"))
    assert remainder.error == "open native endpoint chain"
    assert remainder.bounds == (0, 0, 10, 10)
    linear = next(item for item in objects if item.routes == ("AA",))
    assert linear.target(area=True).capability == "curve"
    assert all("BB" not in item.routes for item in objects)


def test_native_queries_and_point_cache_follow_object_decision(setup):
    engine, client, project = setup
    engine.prepare_positions(project, [(5, 0)])
    first = engine._basis_key
    project.source_file.object_decisions = [
        SourceObjectDecision(
            source={"handle": "BB", "instance_chain": []},
            geometry_sha256="a" * 64,
            interpretation="linear",
        )
    ]
    engine.prepare_positions(project, [(5, 0)])
    assert engine._basis_key != first
    assert (
        next(
            target for target in client.calls[-1].targets if target.route == "BB"
        ).capability
        == "curve"
    )
    assert (
        engine.placement_advisory_detail(project, 5, 0, 0.5, "shrub").code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    project.source_file.object_decisions[0].interpretation = "reference"
    engine.prepare_positions(project, [(5, 0)])
    assert all(target.route != "BB" for target in client.calls[-1].targets)


def test_linear_interpretation_keeps_native_clearance_and_reference_is_local(setup):
    engine, client, project = setup
    project.source_file.object_decisions = [
        SourceObjectDecision(
            source={"handle": "BB", "instance_chain": []},
            geometry_sha256="a" * 64,
            interpretation="linear",
        )
    ]
    engine.prepare_positions(project, [(0.1, 0), (23, 0)])
    close = engine.position_violation(project, 0.1, 0, 0.5, "shrub")
    assert close is not None and close.source_feature_ids == ("BB",)
    project.source_file.object_decisions[0].interpretation = "reference"
    engine.prepare_positions(project, [(0.1, 0), (23, 0)])
    assert engine.position_violation(project, 0.1, 0, 0.5, "shrub") is None
    assert engine.position_violation(
        project, 23, 0, 0.5, "shrub"
    ).source_feature_ids == ("CC",)


def mixed_source_project():
    """Проверенный захват с разнородными объектами 0 и двумя экземплярами одного handle."""
    _, _, project = imported()
    graph = (project.source_geometry or project.geometry).model_copy(deep=True)
    project.layers.append(
        Layer(
            id="mixed-zero",
            source_name="0",
            suggested_kind="ignore",
            mapped_kind=None,
            category="mixed_source",
            mapping_confirmed=False,
            mapping_review_required=True,
            object_count=5,
            color="#808080",
            entity_types={"LINE": 2, "HATCH": 1, "INSERT": 2},
            projected_geometry_types={"LINE": 2, "HATCH": 1},
            context_entity_types={"INSERT": 1},
            unsupported_geometry_types={"INSERT": 1},
            geometry_complete=False,
        )
    )
    for parent, handle, geometry, entity_type, context in [
        (
            "CA",
            "A1",
            {"type": "LineString", "coordinates": [[20, 0], [25, 0]]},
            "LINE",
            False,
        ),
        (
            "CB",
            "A1",
            {"type": "LineString", "coordinates": [[30, 0], [35, 0]]},
            "LINE",
            False,
        ),
        (
            "CA",
            "A2",
            {"type": "Polygon", "coordinates": [[[20, 0], [25, 0], [25, 5], [20, 0]]]},
            "HATCH",
            False,
        ),
        ("CA", "A3", {"type": "Point", "coordinates": [22, 1]}, "INSERT", True),
        ("CA", "A4", {"type": "Point", "coordinates": [24, 1]}, "INSERT", False),
    ]:
        graph.feature_collection["features"].append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "source_layer": "0",
                    "source_handle": handle,
                    "source_instance_chain": [parent],
                    "entity_type": entity_type,
                    "source_geometry_content_sha256": "a" * 64,
                    "source_closed_path": entity_type == "HATCH",
                    "source_native_geometry": entity_type == "HATCH",
                    "source_context_only": context,
                },
            }
        )
    project.source_geometry = graph
    return project


def object_request(project, handle="A1", parent="CA", **values):
    return SourceObjectDecisionRequest(
        source={"handle": handle, "instance_chain": [parent]},
        geometry_sha256="a" * 64,
        source_sha256=project.source_file.content_sha256,
        interpretation="area",
        **values,
    )


def test_mixed_zero_queue_preserves_native_areas_and_context_without_whole_role():
    project = mixed_source_project()
    before = project.model_dump(mode="json")
    page = review_objects(project, layer="0")
    assert {item.route for item in page.items} == {"CA/A1", "CB/A1", "CA/A2"}
    assert all(item.kind is None for item in page.items)
    assert (
        next(item for item in page.items if item.route == "CA/A2").endpoint_distance_m
        == 0
    )
    assert project.model_dump(mode="json") == before


def test_zero_object_kind_is_addressed_reversible_and_does_not_close_open_curve():
    project = mixed_source_project()
    before = project.model_dump(mode="json")
    assigned = decide_object(project, object_request(project, kind="building"))
    assert assigned.source_file.object_decisions[0].kind == "building"
    assert assigned.layers == project.layers and assigned.plan == project.plan
    assert assigned.source_geometry == project.source_geometry
    assert assigned.source_file.warnings == project.source_file.warnings
    assert (
        decide_object(assigned, object_request(assigned, kind="building")) is assigned
    )
    assert (
        next(
            item
            for item in review_objects(assigned, layer="0").items
            if item.route == "CA/A1"
        ).kind
        == "building"
    )
    assert (
        next(
            item
            for item in review_objects(assigned, layer="0").items
            if item.route == "CB/A1"
        ).kind
        is None
    )
    changed = decide_object(assigned, object_request(assigned, kind="restricted"))
    assert changed.source_file.object_decisions[0].kind == "restricted"
    restored = decide_object(changed, object_request(changed))
    assert restored.source_file.object_decisions == []
    assert project.model_dump(mode="json") == before
    assert restored.layers[-1].unsupported_geometry_types == {"INSERT": 1}


def test_zero_native_hatch_accepts_addressed_role_without_changing_neighbor_objects():
    project = mixed_source_project()
    assigned = decide_object(project, object_request(project, handle="A2", kind="lawn"))
    assert len(assigned.source_file.object_decisions) == 1
    assert assigned.source_file.object_decisions[0].source.handle == "A2"
    assert assigned.source_file.object_decisions[0].kind == "lawn"
    assert assigned.layers == project.layers
    assert assigned.layers[-1].mapped_kind is None
    assert assigned.layers[-1].mapping_confirmed is False
    assert {"CA/A1", "CB/A1"} <= {
        item.route for item in review_objects(assigned, layer="0").items
    }


def test_native_area_cannot_be_marked_linear_and_reference_remains_reversible() -> None:
    project = mixed_source_project()
    before = project.model_dump()
    request = object_request(project, handle="A2")
    request.interpretation = "linear"
    with pytest.raises(DomainFailure) as failed:
        decide_object(project, request)
    assert failed.value.reason == FailureReason.SOURCE_OBJECT_LINEAR_UNAVAILABLE
    assert project.model_dump() == before
    request.interpretation = "reference"
    contextual = decide_object(project, request)
    assert contextual.source_file.object_decisions[0].interpretation == "reference"
    request.interpretation = "area"
    restored = decide_object(contextual, request)
    assert restored.source_file.object_decisions == []


@pytest.mark.parametrize("kind", ["utility", "site_border", "ignore"])
def test_object_kind_rejects_nonphysical_or_unconfigured_roles(kind):
    with pytest.raises(ValidationError):
        SourceObjectDecision(
            source={"handle": "A1"},
            geometry_sha256="a" * 64,
            interpretation="area",
            kind=kind,
        )


def test_reference_object_cannot_carry_a_physical_override():
    with pytest.raises(ValidationError):
        SourceObjectDecision(
            source={"handle": "A1"},
            geometry_sha256="a" * 64,
            interpretation="reference",
            kind="building",
        )
