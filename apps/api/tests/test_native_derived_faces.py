"""Same face identity for map/manual/domain, with original lines retained."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_native_live_provider import setup as native_setup

from app.native_query.contracts import NativeObjectQuery, NativeTarget
from app.native_query.derived_faces import active_faces, face_targets
from app.native_query.live_inventory import LiveInventory
from app.native_query.protocol import encode_request

setup = native_setup


def with_faces(engine):
    data = engine.inventory.model_dump(by_alias=True)
    data.update(
        face_policy="native-planar-faces/1",
        linear_routes=["BB"],
        faces=[
            {
                "id": 1,
                "anchor": "BB",
                "layer": "Здания",
                "routes": ["BB", "CC"],
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
                "repairs": [
                    {"from": "BB", "to": "CC", "a": [0, -10, 0], "b": [0.01, -10, 0]}
                ],
            }
        ],
    )
    engine.inventory = LiveInventory.model_validate(data)
    return engine.inventory


def test_map_and_queries_share_face_but_keep_original_curve(setup):
    engine, client, project = setup
    with_faces(engine)
    original = {
        "type": "Feature",
        "id": "source-BB",
        "properties": {
            "source_layer": "Здания",
            "source_handle": "BB",
        },
        "geometry": {"type": "LineString", "coordinates": [[0, -10], [10, 10]]},
    }
    project.geometry.feature_collection["features"] = [original]
    display = engine.calculate(project)
    assert len(display.feature_collection["features"]) == 2
    assert display.feature_collection["features"][0]["geometry"] == original["geometry"]
    face = display.feature_collection["features"][1]
    assert face["properties"]["source_native_face_id"] == 1
    assert face["properties"]["kind"] == "building"
    assert face["geometry"]["type"] == "Polygon"
    engine.prepare_positions(project, [(5.0, 0.0), (50.0, 0.0)])
    targets = [t for t in client.calls[-1].targets if t.route == "BB"]
    assert {(t.face_id, t.capability) for t in targets} == {(0, "curve"), (1, "area")}
    assert engine.position_violation(project, 5, 0, 0.5).code == "NATIVE_OCCUPIED"
    assert (
        engine.placement_advisory_detail(project, 50, 0, 0.5).code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    assert len(project.geometry.feature_collection["features"]) == 1


def test_no_faces_for_network_or_fence_or_unconfirmed_role(setup):
    engine, _, project = setup
    inventory = with_faces(engine)
    layers = {layer.source_name: layer for layer in project.layers}
    layer = layers["Здания"]
    assert len(tuple(active_faces(inventory, layers))) == 1
    layer.mapping_confirmed = False
    assert not tuple(active_faces(inventory, layers))
    layer.mapping_confirmed = True
    layer.mapped_kind = "utility"
    assert not tuple(active_faces(inventory, layers))
    layer.mapped_kind = "restricted"
    layer.category = "fence"
    assert not tuple(active_faces(inventory, layers))


def test_object_override_suppresses_face_not_neighbor_layer(setup):
    engine, _, project = setup
    inventory = with_faces(engine)
    layers = {layer.source_name: layer for layer in project.layers}
    assert not tuple(active_faces(inventory, layers, frozenset({"BB"})))
    assert tuple(active_faces(inventory, layers, frozenset({"CC"})))


def test_derived_identity_rejects_wrong_members_and_missing_policy(setup):
    engine, _, _ = setup
    inventory = with_faces(engine)
    data = json.loads(inventory.model_dump_json(by_alias=True))
    data["faces"][0]["physical_routes"] = ["CC"]
    with pytest.raises(ValidationError):
        LiveInventory.model_validate(data)
    data = json.loads(inventory.model_dump_json(by_alias=True))
    data.pop("face_policy")
    with pytest.raises(ValidationError):
        LiveInventory.model_validate(data)


def test_protocol_addresses_two_faces_of_same_original_separately(setup):
    engine, _, _ = setup
    inventory = with_faces(engine)
    target = face_targets(inventory.faces)[0].target(area=True)
    assert target.face_id == 1 and not target.additional_routes
    query = NativeObjectQuery(
        request_id="a" * 32,
        source_sha256="b" * 64,
        units_code=6,
        targets=(target, NativeTarget(route="BB", capability="curve")),
        points=((0, 0, 0),),
    )
    encoded = encode_request(query, Path("/tmp/face-reply.json")).decode()
    assert "BB face_1 0\nBB curve 0" in encoded
    with pytest.raises(ValidationError):
        NativeTarget(route="BB", capability="curve", face_id=1)


def test_open_remainder_is_distance_constraint_not_bbox_interior(setup):
    engine, client, project = setup
    with_faces(engine)
    # Simulate a readable open line for which no area could be constructed.
    engine.inventory = engine.inventory.model_copy(update={"faces": ()})
    engine.prepare_positions(project, [(5.0, 0.0), (0.1, 0.0)])
    assert (
        engine.position_violation(project, 0.1, 0, 0.5, "shrub").code
        == "NATIVE_CLEARANCE"
    )
    assert (
        engine.placement_advisory_detail(project, 5, 0, 0.5, "shrub").code
        == "SOURCE_GEOMETRY_PARTIAL"
    )
    assert any(
        t.route == "BB" and t.capability == "curve" for t in client.calls[-1].targets
    )


def test_repeated_map_calculation_does_not_duplicate_derived_faces(setup):
    engine, _, project = setup
    with_faces(engine)
    project.geometry = engine.calculate(project)
    assert len(engine.calculate(project).feature_collection["features"]) == 1


def test_assembled_source_routes_leave_unresolved_review_even_with_source_snapshot(
    setup,
):
    from app.dxf_import.object_context import area_members
    from app.dxf_import.object_review import unresolved_paths

    engine, _, project = setup
    with_faces(engine)
    project.import_status = project.import_status.model_copy(
        update={"mode": "autocad_live"}
    )
    project.geometry.feature_collection["features"] = [
        {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[0, -10], [10, 10]]},
            "properties": {
                "source_layer": "Здания",
                "source_handle": "BB",
                "source_closed_path": False,
                "source_geometry_content_sha256": "a" * 64,
            },
        }
    ]
    project.source_geometry = project.geometry
    assert len(unresolved_paths(project)) == 1
    project.geometry = engine.calculate(project)
    assert not unresolved_paths(project)
    assert "BB" in area_members(project)
    assert len(project.source_geometry.feature_collection["features"]) == 1


def test_native_face_can_be_revoked_without_dropping_source_curve(setup):
    from app.native_query.face_review import NativeFaceDecision, change_face_decision

    engine, _, project = setup
    with_faces(engine)
    review = engine.native_face_review(project)
    item = review.items[0]
    request = NativeFaceDecision(
        source_sha256=review.source_sha256, key=item.key, rejected=True
    )
    changed = change_face_decision(project, request, review)
    changed.geometry = project.geometry
    engine._basis(changed)
    assert not engine._faces
    assert any(row.routes == ("BB",) and row.native_linear for row in engine._objects)
    review = engine.native_face_review(changed)
    assert review.items[0].status == "rejected"
    restored = change_face_decision(
        changed, request.model_copy(update={"rejected": False}), review
    )
    restored.geometry = project.geometry
    engine._basis(restored)
    assert len(engine._faces) == 1


def test_old_mapping_symbol_names_do_not_generate_filled_faces(setup):
    from app.native_query.derived_faces import area_layers

    engine, _, project = setup
    building = project.layers[1]
    assert building.category is None
    for name in [
        "Подоснова|Фонари",
        "Проект|ИОТ1_замена светильника",
        "Проект|ДВ_Борт_БР100.30.15",
    ]:
        layer = building.model_copy(update={"source_name": name})
        assert not area_layers({name: layer})


def test_prepare_faces_is_cached_by_layer_selection_not_plan_version(setup):
    from app.native_query.face_contracts import FacePreparation

    engine, client, project = setup
    inventory = with_faces(engine)
    engine.inventory = inventory.model_copy(
        update={"face_preparation_available": True, "faces": (), "linear_routes": ()}
    )
    calls = []

    def prepare(session, layers):
        calls.append(layers)
        return FacePreparation(
            session_id=session.session_id,
            request_sha256="e" * 64,
            layers=layers,
            face_policy="native-planar-faces/1",
            faces=inventory.faces,
            linear_routes=inventory.linear_routes,
            face_issues=(),
        )

    client.prepare_faces = prepare
    engine._basis(project)
    project.geometry_version += 1
    engine._basis(project)
    assert len(calls) == 1
