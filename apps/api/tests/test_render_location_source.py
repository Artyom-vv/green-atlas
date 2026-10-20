"""Explicit apply pins one reviewed street dataset for both render consumers."""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from shapely.affinity import translate
from shapely.geometry import box, mapping, shape
from test_location_application import (
    apply_request,
    auto_alignment,
    polygon_geo,
    resolved,
)
from test_location_application import fixture as fixture
from test_render_environment import context, packet_for
from test_render_preparations import prepared_service as prepared_service

from app.location.alignment import transformed_buildings
from app.rendering import building_heights, environment, location_source
from app.rendering.georeference import render_frame
from app.rendering.location_source import RenderLocationSourceError, location_buildings
from app.scene.contracts import SceneSnapshot


def canonical(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def building(identifier, geometry, **tags):
    return {
        "type": "Feature",
        "id": identifier,
        "geometry": mapping(polygon_geo(geometry)),
        "properties": {"tags": {"building": "yes", **tags}},
    }


def prepared_location(fixture, monkeypatch, *, extent_right=None):
    service, project, providers, _ = fixture
    corridor = polygon_geo(box(-25, -25, 165, 75).union(box(-25, -25, 125, 125)))
    monkeypatch.setattr(
        providers,
        "corridor",
        lambda *_: {
            "corridor": mapping(corridor),
            "bbox": list(corridor.bounds),
            "area_m2": 26000,
            "provenance": {"scope": "explicit_map_section"},
        },
    )
    providers.building_rows[0]["properties"]["tags"].pop("height")
    providers.building_rows += [
        building("way/crossing", box(155, 25, 175, 45)),
        building("way/outside-corridor", box(140, 90, 150, 100), height="99"),
    ]
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    assert alignment.status == "candidate", alignment.message
    if extent_right is not None:
        extent = list(alignment.transform.valid_extent_cad_xy)
        extent[2] = extent_right
        transform = alignment.transform.model_copy(update={"valid_extent_cad_xy": tuple(extent)})
        alignment = alignment.model_copy(update={
            "id": uuid4().hex,
            "transform": transform,
            "preview_buildings": transformed_buildings(draft.map_buildings, transform),
        })
        service._store(project.id, "alignments", alignment.id, alignment)
    service.apply(project.id, apply_request(draft, alignment))
    packet = packet_for(
        [
            context(
                row["id"],
                "building",
                translate(shape(row["geometry"]), -1000, -2000),
            )
            for row in project.geometry.feature_collection["features"]
            if row["properties"]["kind"] == "building"
        ]
    )
    packet["scene"].update(
        project_id=project.id,
        source_sha256=draft.source_sha256,
        geometry_version=draft.geometry_version,
        coordinate_origin=[1000, 2000],
    )

    def no_network(*_):
        pytest.fail("An applied street snapshot must never issue another OSM request")

    monkeypatch.setattr(building_heights, "fetch_buildings", no_network)
    monkeypatch.setattr(environment, "fetch_buildings", no_network)
    path = service.directory / "georeferences" / f"{project.id}.json"
    draft_path = (
        service.directory / "locations" / project.id / "drafts" / f"{draft.id}.json"
    )
    return packet, path, draft_path, draft


def test_apply_pins_selected_corridor_for_heights_and_whole_neighbours_offline(
    fixture, monkeypatch
):
    service, project, providers, _ = fixture
    packet, path, draft_path, draft = prepared_location(fixture, monkeypatch)
    original = deepcopy(packet)
    reference = json.loads(path.read_bytes())["location"]["snapshot"]
    assert reference["draft_id"] == draft.id
    assert reference["sha256"] == json.loads(draft_path.read_bytes())["sha256"]
    # Subsequent address searches do not replace the applied immutable dataset.
    providers.building_rows = []
    assert resolved(fixture).map_buildings["features"] == []
    result = environment.prepare_environment(
        building_heights.prepare_buildings(packet, service.directory), service.directory
    )
    heights = result["building_heights"]
    neighbours = result["environment"]["map_buildings"]
    assert heights["osm"]["provenance"] == neighbours["provenance"]
    assert heights["osm"]["provenance"]["location_selection"]["draft_id"] == draft.id
    assert (
        heights["osm"]["provenance"]["location_selection"][
            "outside_corridor_building_count"
        ]
        == 1
    )
    evidence = {row["feature_id"]: row for row in heights["features"]}
    assert evidence["house-1"]["height_source"] == "render_class_proxy"
    assert evidence["house-1"]["height_status"] == "estimated"
    assert evidence["house-2"]["height_source"] == "osm_height"
    assert evidence["house-2"]["height_m"] == 12
    assert len(neighbours["features"]) == 1
    neighbour = neighbours["features"][0]
    assert neighbour["osm_id"] == "way/crossing"
    assert shape(neighbour["geometry"]).area == pytest.approx(400)
    assert shape(neighbour["geometry"]).bounds == pytest.approx(
        (155, 25, 175, 45), abs=0.002
    )
    assert "height" not in neighbour["osm_tags"]
    assert neighbour["height_source"] == "render_class_proxy"
    assert neighbour["height_status"] == "estimated"
    assert packet == original
    assert project.geometry_version == 4


def test_selected_dataset_loader_reads_immutable_evidence_once_and_shares_arrays(
    fixture, monkeypatch
):
    service, _, _, _ = fixture
    packet, _, draft_path, _ = prepared_location(fixture, monkeypatch)
    snapshot = SceneSnapshot.model_validate(packet["scene"])
    frame = render_frame(snapshot, service.directory)
    opened, accepted = [], []
    original_open = Path.open
    original_qualifier = location_source.applied_location_evidence

    def tracked_open(path, *args, **kwargs):
        opened.append(path)
        return original_open(path, *args, **kwargs)

    def tracked_qualifier(*args):
        evidence = original_qualifier(*args)
        accepted.append(evidence)
        return evidence

    monkeypatch.setattr(Path, "open", tracked_open)
    monkeypatch.setattr(location_source, "applied_location_evidence", tracked_qualifier)
    result = location_buildings(snapshot, service.directory, frame)
    assert opened.count(draft_path) == 1
    assert sum(path.parent.name == "alignments" for path in opened) == 1
    assert result["features"] is accepted[0].draft.map_buildings["features"]


@pytest.mark.parametrize(
    "consumer", [building_heights.prepare_buildings, environment.prepare_environment]
)
@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "tampered",
        "rehash",
        "wrong_project",
        "wrong_source",
        "wrong_revision",
        "unsafe_id",
        "missing_reference",
        "wrong_corridor",
        "malformed_geometry",
    ],
)
def test_declared_snapshot_failure_aborts_both_consumers_without_network(
    fixture, monkeypatch, consumer, damage
):
    service, _, _, _ = fixture
    packet, path, draft_path, _ = prepared_location(fixture, monkeypatch)
    config = json.loads(path.read_bytes())
    document = json.loads(draft_path.read_bytes())
    if damage == "missing":
        draft_path.unlink()
    elif damage == "unsafe_id":
        config["location"]["snapshot"]["draft_id"] = "../escape"
    elif damage == "missing_reference":
        del config["location"]["snapshot"]
    else:
        payload = document["payload"]
        if damage == "wrong_project":
            document["project_id"] = "another-project"
        elif damage == "wrong_source":
            payload["source_sha256"] = "b" * 64
        elif damage == "wrong_revision":
            payload["geometry_version"] += 1
        elif damage == "wrong_corridor":
            payload["search_geometry"] = mapping(polygon_geo(box(500, 500, 600, 600)))
        elif damage == "malformed_geometry":
            payload["map_buildings"]["features"][0]["geometry"]["coordinates"] = [
                [[37, 55], [37.1, 55.1]]
            ]
        else:
            payload["map_buildings"]["features"][0]["properties"]["tags"]["height"] = (
                "999"
            )
        if damage != "tampered":
            document["sha256"] = hashlib.sha256(canonical(payload)).hexdigest()
        if damage not in {"tampered", "rehash"}:
            config["location"]["snapshot"]["sha256"] = document["sha256"]
        draft_path.write_bytes(canonical(document))
    path.write_bytes(canonical(config))
    with pytest.raises(RenderLocationSourceError, match="снимок выбранной улицы"):
        consumer(packet, service.directory)


def test_selected_whole_footprint_outside_frame_support_is_still_excluded(
    fixture, monkeypatch
):
    service, _, _, _ = fixture
    packet, _, _, _ = prepared_location(fixture, monkeypatch, extent_right=1165)
    result = environment.prepare_environment(
        building_heights.prepare_buildings(packet, service.directory), service.directory
    )
    assert result["environment"]["map_buildings"]["features"] == []
    assert result["environment"]["map_buildings"]["outside_transform_extent"] == [
        "way/crossing"
    ]
    assert result["building_heights"]["osm"]["outside_transform_extent"] == [
        "way/crossing"
    ]


def test_legacy_frame_without_location_selection_uses_existing_provider_route(
    fixture, monkeypatch
):
    service, _, _, _ = fixture
    packet, path, _, _ = prepared_location(fixture, monkeypatch)
    config = json.loads(path.read_bytes())
    del config["location"]
    path.write_bytes(canonical(config))
    snapshot = SceneSnapshot.model_validate(packet["scene"])
    frame = render_frame(snapshot, service.directory)
    assert location_buildings(snapshot, service.directory, frame) is None
    calls = []

    def legacy(bounds, cache):
        calls.append(bounds)
        return {"features": [], "provenance": {"legacy": True}}

    monkeypatch.setattr(building_heights, "fetch_buildings", legacy)
    monkeypatch.setattr(environment, "fetch_buildings", legacy)
    environment.prepare_environment(
        building_heights.prepare_buildings(packet, service.directory), service.directory
    )
    assert len(calls) == 2


def test_missing_applied_snapshot_cannot_publish_preparation_and_keeps_old_scene(
    fixture, prepared_service, monkeypatch
):
    location, project, _, _ = fixture
    renderer, _, request = prepared_service
    location.directory = renderer.directory
    renderer.scene.repository.get.return_value = project
    scene = renderer.scene.from_project(project, request.horizon_year)
    request = request.model_copy(update={"scene_sha256": scene.content_sha256})
    _, _, draft_path, _ = prepared_location(fixture, monkeypatch)
    previous = renderer.prepare(project.id, request)
    previous_path = renderer._preparation_root(previous.id)
    before = (previous_path / "render-scene.json").read_bytes()
    draft_path.unlink()
    next_request = request.model_copy(update={"request_id": uuid4()})
    with pytest.raises(RenderLocationSourceError, match="снимок выбранной улицы"):
        renderer.prepare(project.id, next_request)
    assert not renderer._preparation_root(next_request.request_id).exists()
    assert (
        not renderer._preparation_root(next_request.request_id)
        .with_name(f"{next_request.request_id}.pending")
        .exists()
    )
    assert (previous_path / "render-scene.json").read_bytes() == before
    assert renderer.get_preparation(project.id, previous.id) == previous
