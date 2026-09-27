from copy import deepcopy
from types import SimpleNamespace

import pytest
from test_native_live_provider import setup as native_setup
from test_source_object_review import imported

from app.composition import get_application
from app.dxf_import.area_group_review import (
    accept_group,
    remove_group,
    validate_members,
)
from app.dxf_import.object_context import object_context
from app.dxf_import.object_review import review_objects
from app.dxf_import.object_review_contracts import (
    SourceAreaGroupCheck,
    SourceAreaGroupRequest,
)
from app.native_query.live_inventory import LiveInventory
from app.native_query.reviewed_groups import verify_group

setup = native_setup


def group_fixture():
    client, url, project = imported()
    graph = (project.source_geometry or project.geometry).model_copy(deep=True)
    template = next(
        f
        for f in graph.feature_collection["features"]
        if f["geometry"]["type"] == "LineString"
    )
    points = [(20, 0), (25, 0), (25, 5), (20, 5), (20, 0)]
    members = []
    for i in range(4):
        f = deepcopy(template)
        handle = f"A{i}"
        f["properties"].update(
            source_handle=handle,
            source_instance_chain=[],
            entity_type="LINE",
            source_closed_path=False,
            source_geometry_content_sha256=str(i) * 64,
        )
        f["geometry"] = {
            "type": "LineString",
            "coordinates": [points[i], points[i + 1]],
        }
        graph.feature_collection["features"].append(f)
        members.append(
            {
                "source": {"handle": handle, "instance_chain": []},
                "geometry_sha256": str(i) * 64,
            }
        )
    project.source_geometry = graph
    layer = next(
        layer
        for layer in project.layers
        if layer.source_name == template["properties"]["source_layer"]
    )
    layer.unsupported_geometry_types["LINE"] = 4
    layer.geometry_complete = False
    project = get_application().repository.save(project)
    request = SourceAreaGroupRequest(
        source_sha256=project.source_file.content_sha256,
        kind="building",
        members=members,
    )
    return client, url, project, request


def test_context_has_neighbors_identity_and_area_not_just_selected_line():
    client, url, project, request = group_fixture()
    result = object_context(project, "A0")
    assert {"A0", "A1", "A2", "A3"} <= {o.route for o in result.objects}
    assert all(o.layer and o.source.handle for o in result.objects)
    assert next(o for o in result.objects if o.route == "A0").can_join
    assert any(o.native_area and not o.can_join for o in result.objects)
    assert result.extent[0] < 20 and result.extent[2] > 25
    response = client.get(url + "/source-object-context", params={"route": "A0"})
    assert response.status_code == 200, response.text
    assert response.json()["source_sha256"] == project.source_file.content_sha256
    assert (
        client.get(url + "/source-object-context", params={"route": "FFFF"}).status_code
        == 400
    )


def test_context_deduplicates_area_and_source_curve():
    _, _, project, _ = group_fixture()
    duplicate = deepcopy(
        next(
            f
            for f in project.source_geometry.feature_collection["features"]
            if f["properties"].get("source_handle") == "A0"
        )
    )
    duplicate["geometry"] = {
        "type": "Polygon",
        "coordinates": [[[20, 0], [25, 0], [25, 5], [20, 0]]],
    }
    project.source_geometry.feature_collection["features"].append(duplicate)
    result = object_context(project, "A0")
    assert len([o for o in result.objects if o.route == "A0"]) == 1
    assert next(o for o in result.objects if o.route == "A0").native_area


def test_group_decision_reversible_and_preserves_plantings_source_geometry():
    _, _, project, request = group_fixture()
    original = deepcopy(project.source_geometry.feature_collection)
    accepted = accept_group(project, request)
    assert len(accepted.source_file.area_groups) == 1
    assert accepted.source_file.content_sha256 == project.source_file.content_sha256
    assert accepted.plan == project.plan
    assert accepted.source_geometry.feature_collection == original
    assert not {"A0", "A1", "A2", "A3"} & {
        item.route for item in review_objects(accepted).items
    }
    assert accepted.geometry is None and not accepted.map_ready
    restored = remove_group(accepted, accepted.source_file.area_groups[0].id)
    assert restored.layers == project.layers
    assert {"A0", "A1", "A2", "A3"} <= {
        item.route for item in review_objects(restored).items
    }


@pytest.mark.parametrize(
    "fault", ["capture", "hash", "duplicate", "layer", "parent", "covered"]
)
def test_group_rejects_unsafe_identity_or_topology_context(fault):
    _, _, project, request = group_fixture()
    if fault == "capture":
        request.source_sha256 = "f" * 64
    if fault == "hash":
        request.members[0].geometry_sha256 = "f" * 64
    if fault == "duplicate":
        request.members[1] = request.members[0]
    if fault == "layer":
        next(
            f
            for f in project.source_geometry.feature_collection["features"]
            if f["properties"].get("source_handle") == "A0"
        )["properties"]["source_layer"] = "Другой слой"
    if fault == "parent":
        request.members[0].source.instance_chain = ["CC"]
    if fault == "covered":
        project = accept_group(project, request)
    with pytest.raises(ValueError):
        validate_members(project, request)


def test_http_rechecks_native_at_commit_and_preserves_project_on_failure(monkeypatch):
    client, url, project, request = group_fixture()
    calls = []

    def check(_project, _request):
        calls.append(_request)
        return SourceAreaGroupCheck(valid=len(calls) == 1, reason="Разрыв контура")

    monkeypatch.setattr(
        get_application().geometry, "review_area_group", check, raising=False
    )
    assert client.post(
        url + "/source-area-groups/check", json=request.model_dump()
    ).json()["valid"]
    response = client.post(url + "/source-area-groups", json=request.model_dump())
    assert response.status_code == 400, response.text
    assert len(calls) == 2
    assert get_application().repository.get(project.id).source_file.area_groups == []
    monkeypatch.setattr(
        get_application().geometry,
        "review_area_group",
        lambda *_: SourceAreaGroupCheck(valid=True, reason="Проверено"),
    )
    response = client.post(url + "/source-area-groups", json=request.model_dump())
    assert response.status_code == 200, response.text
    group = response.json()["source_file"]["area_groups"][0]
    assert response.json()["plan"] == project.plan.model_dump(mode="json")
    response = client.delete(url + "/source-area-groups/" + group["id"])
    assert response.status_code == 200, response.text
    assert response.json()["source_file"]["area_groups"] == []


def test_group_native_target_replaces_only_chosen_members(setup):
    engine, client, project = setup
    request = SourceAreaGroupRequest(
        source_sha256=project.source_file.content_sha256,
        kind="building",
        members=[
            {
                "source": {"handle": handle, "instance_chain": []},
                "geometry_sha256": "a" * 64,
            }
            for handle in ["BB", "DD"]
        ],
    )
    dd = engine.inventory.objects[1].model_copy(update={"route": "DD"})
    engine.inventory = LiveInventory.model_validate(
        {
            "schema": "green-atlas.live-inventory/1",
            "objects": (*engine.inventory.objects, dd),
            "groups": [],
        }
    )
    result = verify_group(engine, project, request)
    assert result.valid and client.calls[-1].targets[0].additional_routes == ("DD",)
    from app.dxf_import.object_review_contracts import SourceAreaGroup

    project.source_file.area_groups = [
        SourceAreaGroup(**request.model_dump(), id="group")
    ]
    engine.prepare_positions(project, [(5, 0), (23, 0)])
    targets = client.calls[-1].targets
    assert next(t for t in targets if t.route == "BB").additional_routes == ("DD",)
    assert any(t.route == "CC" for t in targets)
    assert engine.position_violation(project, 5, 0, 0.5, "shrub") is not None
    assert engine.position_violation(project, 23, 0, 0.5, "shrub") is not None


def test_read_issues_separate_unreadable_objects_from_layer_assignment():
    client, url, project = imported()
    response = client.get(url + "/source-read-issues")
    assert response.status_code == 200, response.text
    assert response.json()["source_sha256"] == project.source_file.content_sha256


def test_read_issue_russian_causes_use_native_class_names(monkeypatch):
    from app.cad_bridge.contracts import SourceIdentity
    from app.dxf_import.read_issues import source_read_issues

    _, _, project = imported()
    records = [
        SimpleNamespace(
            identity=SourceIdentity(handle="A1", instance_chain=[]),
            layer="Тротуар",
            entity_type="AcDbHatch",
            unresolved_reference=None,
            reason="no single associative boundary; getRegionArea_returned_null",
            method="",
        ),
        SimpleNamespace(
            identity=SourceIdentity(handle="A2", instance_chain=[]),
            layer="Граница улицы",
            entity_type="AcDbPolyline",
            unresolved_reference=None,
            reason="closed AcDbPolyline does not form a valid closed path",
            method="",
        ),
    ]
    monkeypatch.setattr(
        "app.dxf_import.read_issues.area_evidence",
        lambda *_args, **_kwargs: SimpleNamespace(unresolved=records),
    )
    result = source_read_issues(project, b"fixture")
    assert result.items[0].entity_type == "HATCH"
    assert "AutoCAD не вернул область" in result.items[0].reason
    assert result.items[1].entity_type == "LWPOLYLINE"
    assert "не прошёл проверку" in result.items[1].reason
