"""Zone edits preserve unrelated saved defects without admitting new ones."""

import pytest
from shapely.errors import GEOSException
from shapely.geometry import box, mapping, shape
from test_planting_zone_changes import approval, draft, seeded

from app.planting_zone_changes import ZoneChangeService
from app.planting_zones.contracts import PlantingZoneAssignment
from app.planting_zones.domain import attach_planting_zone_features


@pytest.fixture(params=["site_border", "site_surface"])
def outside_project(request):
    app, project = seeded()
    # Represent a previously persisted contour, not a newly admitted edit.
    project.planting_zones.append(
        PlantingZoneAssignment.model_validate_json(
            PlantingZoneAssignment(
                id="old-outside",
                label="Saved outside area",
                geometry=mapping(box(700, 700, 750, 750)),
            ).model_dump_json()
        )
    )
    project.geometry.feature_collection["features"][0]["properties"]["kind"] = (
        request.param
    )
    attach_planting_zone_features(project)
    app.repository.save(project)
    return app, app.get(project.id)


def assert_committed_without_remapping(app, project, request):
    service = ZoneChangeService(app.zones)
    before = project.model_dump(mode="json")
    preview = service.preview(project.id, request)
    assert preview.can_apply, preview.blockers
    assert not preview.blockers and not preview.affected_planting_ids
    if preview.preflight is not None:
        assert preview.preflight.can_save
    assert service.get_preview(project.id, preview.id, preview.digest) == preview
    assert app.get(project.id).model_dump(mode="json") == before

    result = service.commit(project.id, approval(preview))
    saved = app.get(project.id)
    saved_json = saved.model_dump(mode="json")
    expected = [zone.model_dump(mode="json") for zone in preview.after_zones]
    assert saved_json["planting_zones"] == expected
    assert result.after_zones == preview.after_zones
    assert result.plantings_unchanged
    assert saved.plan.id == project.plan.id
    assert saved.plan.objects == project.plan.objects
    assert saved.plan.version == project.plan.version == result.plan_version
    assert saved.state_version == project.state_version + 1 == result.state_version
    assert saved.geometry_version == project.geometry_version + 1
    for original in before["planting_zones"]:
        if original["id"] != preview.target_zone_id:
            assert original in saved_json["planting_zones"]
    displayed = {
        feature["properties"]["planting_zone_id"]: feature["geometry"]
        for feature in saved_json["geometry"]["feature_collection"]["features"]
        if feature.get("properties", {}).get("kind") == "planting_area"
    }
    assert displayed == {zone["id"]: zone["geometry"] for zone in expected}
    return preview, saved


def assert_rejected_without_write(app, project, request, reason):
    service = ZoneChangeService(app.zones)
    before = project.model_dump(mode="json")
    preview = service.preview(project.id, request)
    assert not preview.can_apply
    assert any(reason in blocker for blocker in preview.blockers), preview.blockers
    if preview.preflight is not None:
        assert not preview.preflight.can_save
    assert service.get_preview(project.id, preview.id, preview.digest) == preview
    assert app.get(project.id).model_dump(mode="json") == before
    with pytest.raises(ValueError, match=reason):
        service.commit(project.id, approval(preview))
    assert app.get(project.id).model_dump(mode="json") == before
    assert (
        app.repository.mutation_receipt(project.id, "planting_zones", preview.id)
        is None
    )
    return preview


@pytest.mark.parametrize("operation", ["delete", "create", "update"])
def test_unrelated_saved_outside_zone_does_not_block_preview_or_commit(
    outside_project, operation
):
    app, project = outside_project
    changes = {}
    if operation == "create":
        changes["geometry"] = mapping(box(250, 100, 280, 130))
    elif operation == "update":
        changes["geometry"] = mapping(box(410, 10, 490, 90))
    preview, saved = assert_committed_without_remapping(
        app, project, draft(project, operation, **changes)
    )
    assert (
        len(saved.planting_zones) == {"delete": 2, "create": 4, "update": 3}[operation]
    )
    if operation == "delete":
        assert all(zone.id != preview.target_zone_id for zone in saved.planting_zones)
    else:
        target = next(
            zone for zone in saved.planting_zones if zone.id == preview.target_zone_id
        )
        assert shape(target.geometry).equals(shape(changes["geometry"]))


@pytest.mark.parametrize("resubmit_geometry", [False, True])
def test_renaming_saved_outside_zone_keeps_its_exact_contour(
    outside_project, resubmit_geometry
):
    app, project = outside_project
    original = project.planting_zones[-1].model_dump(mode="json")
    changes = {"geometry": original["geometry"]} if resubmit_geometry else {}
    _, saved = assert_committed_without_remapping(
        app,
        project,
        draft(project, zone_id="old-outside", label="Renamed outside", **changes),
    )
    assert saved.planting_zones[-1].model_dump(mode="json") == {
        **original,
        "label": "Renamed outside",
    }


@pytest.mark.parametrize("operation", ["create", "update"])
@pytest.mark.parametrize(
    "geometry",
    [mapping(box(600, 20, 640, 60)), mapping(box(505, 120, 525, 150))],
    ids=["wholly-outside", "crosses-boundary"],
)
def test_new_or_changed_outside_contour_is_rejected_by_default(
    outside_project, operation, geometry
):
    app, project = outside_project
    assert_rejected_without_write(
        app,
        project,
        draft(project, operation, geometry=geometry),
        "выходит за границы территории",
    )


def test_referenced_zone_deletion_stays_protected_with_unrelated_outside_zone(
    outside_project,
):
    app, project = outside_project
    preview = assert_rejected_without_write(
        app,
        project,
        draft(project, "delete", zone_id="west"),
        "Нельзя удалить участок, в котором уже есть посадки",
    )
    assert preview.affected_planting_ids == ("tree-west",)


@pytest.mark.parametrize("operation", ["create", "update"])
def test_new_partial_overlap_stays_rejected_with_unrelated_outside_zone(
    outside_project, operation
):
    app, project = outside_project
    preview = assert_rejected_without_write(
        app,
        project,
        draft(project, operation, geometry=mapping(box(190, 100, 220, 130))),
        "пересекаются",
    )
    assert any(overlap.zone_id == "west" for overlap in preview.preflight.overlaps)


@pytest.mark.parametrize("operation", ["delete", "create", "update"])
def test_unchanged_overlapping_pair_does_not_block_an_unrelated_edit(operation):
    app, project = seeded()
    project.planting_zones.append(
        PlantingZoneAssignment(
            id="old-overlap",
            label="Saved overlap",
            geometry=mapping(box(180, 100, 220, 160)),
        )
    )
    attach_planting_zone_features(project)
    app.repository.save(project)
    project = app.get(project.id)
    changes = {}
    if operation == "create":
        changes["geometry"] = mapping(box(250, 100, 280, 130))
    elif operation == "update":
        changes["geometry"] = mapping(box(410, 10, 490, 90))
    assert_committed_without_remapping(
        app, project, draft(project, operation, **changes)
    )


@pytest.mark.parametrize("operation", ["delete", "create", "update"])
@pytest.mark.xfail(
    strict=True,
    raises=GEOSException,
    reason=(
        "Known failure: commit -> validation.refresh -> legacy PositionChecker "
        "unary_union includes unchanged self-intersecting user AOIs. Keep until "
        "the project validator supports them; frozen legacy geometry is out of scope."
    ),
)
def test_unchanged_self_intersecting_zone_is_preserved_without_repair(operation):
    app, project = seeded()
    geometry = {
        "type": "Polygon",
        "coordinates": [
            [
                [700, 700],
                [750, 750],
                [700, 750],
                [750, 700],
                [700, 700],
            ]
        ],
    }
    assert not shape(geometry).is_valid
    project.planting_zones.append(
        PlantingZoneAssignment(
            id="old-invalid",
            label="Saved self-intersection",
            geometry=geometry,
        )
    )
    attach_planting_zone_features(project)
    app.repository.save(project)
    project = app.get(project.id)
    assert_committed_without_remapping(app, project, draft(project, operation))


def test_unique_ids_remain_a_global_guard_even_without_geometry_changes(
    outside_project,
):
    app, project = outside_project
    zones = [zone.model_copy(deep=True) for zone in project.planting_zones]
    zones.append(zones[-1].model_copy(deep=True))
    before = project.model_dump(mode="json")
    with pytest.raises(
        ValueError, match="Идентификаторы участков должны быть уникальны"
    ):
        app.save_planting_zones(project.id, zones, preserve_plan=True)
    assert app.get(project.id).model_dump(mode="json") == before


def test_existing_missing_reference_is_not_ignored_by_an_unrelated_rename(
    outside_project,
):
    app, project = outside_project
    project.plan.objects[0].planting_zone_id = "missing-zone"
    app.repository.save(project)
    project = app.get(project.id)
    assert_rejected_without_write(
        app,
        project,
        draft(project),
        "Нельзя удалить участок, в котором уже есть посадки",
    )


def test_last_unreferenced_outside_zone_can_be_deleted(outside_project):
    app, project = outside_project
    project.plan.objects = []
    project.planting_zones = project.planting_zones[-1:]
    attach_planting_zone_features(project)
    app.repository.save(project)
    project = app.get(project.id)
    preview, saved = assert_committed_without_remapping(
        app,
        project,
        draft(project, "delete", zone_id="old-outside"),
    )
    assert preview.after_zones == ()
    assert saved.planting_zones == []
    assert saved.plan.objects == []


def test_initial_empty_zone_setup_stays_rejected():
    app, project = seeded(plants=False)
    project.planting_zones = []
    attach_planting_zone_features(project)
    app.repository.save(project)
    project = app.get(project.id)
    before = project.model_dump(mode="json")
    with pytest.raises(ValueError, match="Выберите хотя бы один участок"):
        app.save_planting_zones(project.id, [], preserve_plan=True)
    assert app.get(project.id).model_dump(mode="json") == before
