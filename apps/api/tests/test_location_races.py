"""Resolve ownership and preview admission on isolated, offline location drafts."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from app.location.application import LocationApplication, LocationError
from app.location.contracts import (
    LocationAlignmentRequest,
    LocationControlPair,
    LocationResolveRequest,
)
from app.rendering.georeference import RenderGeoreferenceError, render_frame
from shapely.geometry import box, mapping, shape
from test_location_application import (
    apply_request,
    auto_alignment,
    geographic,
    polygon_geo,
    resolved,
)
from test_location_application import fixture as fixture


def pointer(service, project):
    return json.loads(
        (service.directory / "locations" / project.id / "current.json").read_bytes()
    )


def pause_at_center(providers, center):
    started, release = Event(), Event()
    original = providers.streets

    def delayed(address, cache, selected_center):
        if selected_center == center:
            started.set()
            if not release.wait(5):
                raise AssertionError("Test did not release the paused provider")
        return original(address, cache, selected_center)

    providers.streets = delayed
    return started, release


def test_late_resolve_is_saved_but_never_replaces_latest_requested_draft(fixture):
    service, project, providers, suggestion = fixture
    started, release = pause_at_center(providers, (37.0, 55.0))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            service.resolve,
            project.id,
            LocationResolveRequest(suggestion=suggestion, center_lon_lat=(37, 55)),
        )
        try:
            assert started.wait(5)
            latest = service.resolve(
                project.id,
                LocationResolveRequest(
                    suggestion=suggestion, center_lon_lat=(37.001, 55)
                ),
            )
            assert pointer(service, project)["draft_id"] == latest.id
        finally:
            release.set()
        earlier = pending.result(timeout=5)
    assert earlier.id != latest.id
    assert (
        service.directory / "locations" / project.id / "drafts" / f"{earlier.id}.json"
    ).exists()
    assert service.get(project.id).draft.id == latest.id


def test_old_alignment_cannot_republish_pointer_during_or_after_new_resolve(fixture):
    service, project, providers, suggestion = fixture
    earlier = resolved(fixture)
    started, release = pause_at_center(providers, (37.001, 55.0))
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(
            service.resolve,
            project.id,
            LocationResolveRequest(suggestion=suggestion, center_lon_lat=(37.001, 55)),
        )
        try:
            assert started.wait(5)
            alignment = auto_alignment(service, project, earlier)
            assert alignment.status == "candidate"
            assert pointer(service, project) == {"draft_id": earlier.id}
        finally:
            release.set()
        latest = pending.result(timeout=5)
    old_alignment = auto_alignment(service, project, earlier)
    assert old_alignment.status == "candidate"
    assert pointer(service, project) == {"draft_id": latest.id}
    assert service.get(project.id).alignment is None
    # Explicit review of immutable older evidence remains applicable for the
    # same source revision; applying it must not make it the latest draft.
    state = service.apply(project.id, apply_request(earlier, old_alignment))
    assert state.applied.alignment_id == old_alignment.id
    assert state.draft.id == latest.id


def test_reopened_application_can_align_the_current_persisted_draft(fixture):
    service, project, providers, _ = fixture
    draft = resolved(fixture)
    reopened = LocationApplication(
        service.repository, service.directory, providers=providers
    )
    alignment = auto_alignment(reopened, project, draft)
    assert pointer(reopened, project) == {
        "draft_id": draft.id,
        "alignment_id": alignment.id,
    }


def mapped_building(identifier, bounds):
    return {
        "type": "Feature",
        "id": identifier,
        "geometry": mapping(polygon_geo(box(*bounds))),
        "properties": {"tags": {"building": "yes"}},
    }


def test_preview_rejects_whole_polygons_exactly_like_render_extent(fixture):
    service, project, providers, _ = fixture
    providers.building_rows.extend(
        [
            mapped_building("way/outside", (270, -5, 280, 5)),
            mapped_building("way/crossing", (240, -5, 260, 5)),
        ]
    )
    draft = resolved(fixture)
    alignment = auto_alignment(service, project, draft)
    assert alignment.status == "candidate"
    preview = alignment.preview_buildings
    assert preview["omitted_outside_extent_count"] == 2
    assert "не показаны здания: 2" in alignment.message
    service.apply(project.id, apply_request(draft, alignment))
    frame = render_frame(service._snapshot(project), service.directory)
    admitted = []
    for row in draft.map_buildings["features"]:
        try:
            for longitude, latitude in shape(row["geometry"]).exterior.coords:
                frame.wgs84_to_local(longitude, latitude)
        except RenderGeoreferenceError:
            continue
        admitted.append(row["id"])
    assert [row["id"] for row in preview["features"]] == admitted
    assert "way/outside" not in admitted and "way/crossing" not in admitted


def test_empty_supported_preview_cannot_be_applied_as_successful_alignment(fixture):
    service, project, providers, _ = fixture
    providers.building_rows = [mapped_building("way/outside", (270, -5, 280, 5))]
    draft = resolved(fixture)
    pairs = [
        LocationControlPair(
            cad_xy=(1000 + x, 2000 + y),
            lon_lat=geographic(x, y),
            usage="holdout" if index == 3 else "fit",
        )
        for index, (x, y) in enumerate([(0, 0), (100, 0), (0, 100), (100, 100)])
    ]
    alignment = service.align(
        project.id, LocationAlignmentRequest(draft_id=draft.id, control_pairs=pairs)
    )
    assert alignment.status == "needs_controls" and alignment.transform is None
    assert alignment.preview_buildings["features"] == []
    assert alignment.preview_buildings["omitted_outside_extent_count"] == 1
    assert "нет целых контуров зданий" in alignment.message
    with pytest.raises(LocationError):
        service.apply(project.id, apply_request(draft, alignment))
    assert not (service.directory / "georeferences" / f"{project.id}.json").exists()
