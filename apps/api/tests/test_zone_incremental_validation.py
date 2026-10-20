"""Normal zone edits preserve unrelated saved defects without admitting new ones."""

from typing import Any, Literal

import pytest
from shapely.errors import GEOSException
from shapely.geometry import box, mapping, shape
from test_planting_zone_changes import draft, seeded

from app.application import ProjectApplication
from app.planting_zones.contracts import PlantingZoneAssignment, PlantingZonePreview
from app.planting_zones.domain import attach_planting_zone_features
from app.projects.contracts import Project
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureParams, FailureReason

ZoneProject = tuple[ProjectApplication, Project]


@pytest.fixture(params=["site_border", "site_surface"])
def outside_project(request: pytest.FixtureRequest) -> ZoneProject:
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
    assert project.geometry is not None
    project.geometry.feature_collection["features"][0]["properties"]["kind"] = (
        request.param
    )
    attach_planting_zone_features(project)
    app.repository.save(project)
    return app, app.get(project.id)


def assert_committed_without_remapping(
    app: ProjectApplication,
    project: Project,
    zones: list[PlantingZoneAssignment],
) -> tuple[list[PlantingZoneAssignment], Project]:
    before = project.model_dump(mode="json")
    app.zones.validate_planting_zones(project, zones)
    assert app.get(project.id).model_dump(mode="json") == before
    saved = app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    assert saved.plan is not None and project.plan is not None
    saved_json = saved.model_dump(mode="json")
    expected = [zone.model_dump(mode="json") for zone in zones]
    assert saved_json["planting_zones"] == expected
    assert saved.plan.id == project.plan.id
    assert saved.plan.objects == project.plan.objects
    assert saved.plan.version == project.plan.version
    assert saved.state_version == project.state_version + 1
    assert saved.geometry_version == project.geometry_version + 1
    expected_by_id = {zone.id: zone for zone in zones}
    for original in project.planting_zones:
        if original.id in expected_by_id and original == expected_by_id[original.id]:
            assert original.model_dump(mode="json") in saved_json["planting_zones"]
    displayed = {
        feature["properties"]["planting_zone_id"]: feature["geometry"]
        for feature in saved_json["geometry"]["feature_collection"]["features"]
        if feature.get("properties", {}).get("kind") == "planting_area"
    }
    assert displayed == {zone["id"]: zone["geometry"] for zone in expected}
    return zones, saved


def assert_rejected_without_write(
    app: ProjectApplication,
    project: Project,
    zones: list[PlantingZoneAssignment],
    reason: FailureReason,
    params: FailureParams,
    explanation: str,
) -> PlantingZonePreview | None:
    before = project.model_dump_json()
    changed = next((zone for zone in zones if zone not in project.planting_zones), None)
    preview = None
    if changed is not None:
        preview = PlantingZonePreview.model_validate(
            app.zones.preview_planting_zone(project.id, changed)
        )
        assert not preview.can_save
    assert app.get(project.id).model_dump_json() == before
    with pytest.raises(DomainFailure) as failure:
        app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    assert failure.value.reason is reason
    assert failure.value.params == params
    assert public_failure_message(failure.value) == explanation
    assert app.get(project.id).model_dump_json() == before
    return preview


@pytest.mark.parametrize("operation", ["delete", "create", "update"])
def test_unrelated_saved_outside_zone_does_not_block_write(
    outside_project: ZoneProject,
    operation: Literal["create", "update", "delete"],
) -> None:
    app, project = outside_project
    geometry = (
        mapping(box(250, 100, 280, 130))
        if operation == "create"
        else mapping(box(410, 10, 490, 90))
    )
    zones, saved = assert_committed_without_remapping(
        app, project, draft(project, operation, geometry=geometry)
    )
    assert (
        len(saved.planting_zones) == {"delete": 2, "create": 4, "update": 3}[operation]
    )
    target_id = "new-area" if operation == "create" else "east"
    if operation == "delete":
        assert all(zone.id != target_id for zone in zones)
    else:
        target = next(zone for zone in saved.planting_zones if zone.id == target_id)
        assert shape(target.geometry).equals(shape(geometry))


@pytest.mark.parametrize("resubmit_geometry", [False, True])
def test_renaming_saved_outside_zone_keeps_its_exact_contour(
    outside_project: ZoneProject,
    resubmit_geometry: bool,
) -> None:
    app, project = outside_project
    original = project.planting_zones[-1].model_dump(mode="json")
    _, saved = assert_committed_without_remapping(
        app,
        project,
        draft(
            project,
            zone_id="old-outside",
            label="Renamed outside",
            geometry=original["geometry"] if resubmit_geometry else None,
        ),
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
    outside_project: ZoneProject,
    operation: Literal["create", "update"],
    geometry: dict[str, Any],
) -> None:
    app, project = outside_project
    assert_rejected_without_write(
        app,
        project,
        draft(project, operation, geometry=geometry),
        FailureReason.ZONE_OUTSIDE_SITE,
        {"zone_label": "New area" if operation == "create" else "Renamed East"},
        f"Участок «{'New area' if operation == 'create' else 'Renamed East'}» выходит за границы территории",
    )


def test_referenced_zone_deletion_stays_protected_with_unrelated_outside_zone(
    outside_project: ZoneProject,
) -> None:
    app, project = outside_project
    assert_rejected_without_write(
        app,
        project,
        draft(project, "delete", zone_id="west"),
        FailureReason.ZONE_OCCUPIED,
        {},
        "Нельзя удалить участок, в котором уже есть посадки",
    )


@pytest.mark.parametrize("operation", ["create", "update"])
def test_new_partial_overlap_stays_rejected_with_unrelated_outside_zone(
    outside_project: ZoneProject,
    operation: Literal["create", "update"],
) -> None:
    app, project = outside_project
    preview = assert_rejected_without_write(
        app,
        project,
        draft(project, operation, geometry=mapping(box(190, 100, 220, 130))),
        FailureReason.ZONE_OVERLAP,
        {
            "zone_label": "New area" if operation == "create" else "Renamed East",
            "other_zone_label": "West",
        },
        f"Участки «West» и «{'New area' if operation == 'create' else 'Renamed East'}» пересекаются",
    )
    assert preview is not None
    assert any(overlap.zone_id == "west" for overlap in preview.overlaps)


@pytest.mark.parametrize("operation", ["delete", "create", "update"])
def test_unchanged_overlapping_pair_does_not_block_an_unrelated_edit(
    operation: Literal["create", "update", "delete"],
) -> None:
    app, project = seeded()
    project.planting_zones.append(
        PlantingZoneAssignment(
            id="old-overlap",
            label="Saved overlap",
            geometry=mapping(box(180, 100, 220, 160)),
        )
    )
    attach_planting_zone_features(project)
    project = Project.model_validate_json(project.model_dump_json())
    app.repository.save(project)
    project = app.get(project.id)
    geometry = (
        mapping(box(250, 100, 280, 130))
        if operation == "create"
        else mapping(box(410, 10, 490, 90))
    )
    assert_committed_without_remapping(
        app, project, draft(project, operation, geometry=geometry)
    )


@pytest.mark.parametrize(
    "operation",
    [
        pytest.param(
            operation,
            marks=pytest.mark.xfail(
                strict=True,
                raises=GEOSException,
                reason=(
                    "Known failure: changed zone coverage requires the legacy "
                    "PositionChecker, whose union includes unchanged self-intersecting "
                    "AOIs. Geometry repair remains out of scope."
                ),
            ),
        )
        for operation in ("delete", "create")
    ]
    + ["update"],
)
def test_unchanged_self_intersecting_zone_is_preserved_without_repair(
    operation: Literal["create", "update", "delete"],
) -> None:
    app, project = seeded()
    geometry = {
        "type": "Polygon",
        "coordinates": [[[700, 700], [750, 750], [700, 750], [750, 700], [700, 700]]],
    }
    assert not shape(geometry).is_valid
    project.planting_zones.append(
        PlantingZoneAssignment(
            id="old-invalid", label="Saved self-intersection", geometry=geometry
        )
    )
    attach_planting_zone_features(project)
    app.repository.save(project)
    project = app.get(project.id)
    assert_committed_without_remapping(app, project, draft(project, operation))


def test_unique_ids_remain_a_global_guard_even_without_geometry_changes(
    outside_project: ZoneProject,
) -> None:
    app, project = outside_project
    zones = [zone.model_copy(deep=True) for zone in project.planting_zones]
    zones.append(zones[-1].model_copy(deep=True))
    before = project.model_dump_json()
    with pytest.raises(DomainFailure) as failure:
        app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    assert failure.value.reason is FailureReason.ZONE_ID_DUPLICATE
    assert failure.value.params == {}
    assert (
        public_failure_message(failure.value)
        == "Идентификаторы участков должны быть уникальны"
    )
    assert app.get(project.id).model_dump_json() == before


def test_existing_missing_reference_is_not_ignored_by_an_unrelated_rename(
    outside_project: ZoneProject,
) -> None:
    app, project = outside_project
    assert project.plan is not None
    project.plan.objects[0].planting_zone_id = "missing-zone"
    app.repository.save(project)
    project = app.get(project.id)
    assert_rejected_without_write(
        app,
        project,
        draft(project),
        FailureReason.ZONE_OCCUPIED,
        {},
        "Нельзя удалить участок, в котором уже есть посадки",
    )


def test_last_unreferenced_outside_zone_can_be_deleted(
    outside_project: ZoneProject,
) -> None:
    app, project = outside_project
    assert project.plan is not None
    project.plan.objects = []
    project.planting_zones = project.planting_zones[-1:]
    attach_planting_zone_features(project)
    app.repository.save(project)
    project = app.get(project.id)
    zones, saved = assert_committed_without_remapping(
        app, project, draft(project, "delete", zone_id="old-outside")
    )
    assert zones == []
    assert saved.planting_zones == []
    assert saved.plan is not None
    assert saved.plan.objects == []


def test_initial_empty_zone_setup_stays_rejected() -> None:
    app, project = seeded(plants=False)
    project.planting_zones = []
    attach_planting_zone_features(project)
    app.repository.save(project)
    project = app.get(project.id)
    before = project.model_dump_json()
    with pytest.raises(DomainFailure) as failure:
        app.zones.save_planting_zones(project.id, [], preserve_plan=True)
    assert failure.value.reason is FailureReason.ZONES_REQUIRED
    assert failure.value.params == {}
    assert public_failure_message(failure.value) == "Выберите хотя бы один участок"
    assert app.get(project.id).model_dump_json() == before
