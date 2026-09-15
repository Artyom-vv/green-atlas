"""Real zone persistence, immutable approval and adversarial concurrency cases."""

from application_factory import recompose_application

from app.planting_zones.domain import attach_planting_zone_features
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from threading import Barrier

import pytest
from pydantic import ValidationError
from shapely.geometry import box, mapping, shape

from app.contracts import PlanObject, PlantingZoneAssignment
from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import InMemoryProjectHistory
from app.planting_zone_changes import (
    ZoneChangeCommit, ZoneChangeDraft, ZoneChangePreview, ZoneChangeService,
    get_zone_change_service,
)
from app.projects.concurrency import (
    ProjectVersionConflict, expected_project_version, reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.adapters import SqliteProjectRepository
from test_placement_allocation import application


def seeded(*, plants=True, plan=True):
    app, project = application()
    app = recompose_application(app, geometry_query=IndexedGeometryQuery())
    app = recompose_application(app, history=InMemoryProjectHistory())
    if plants:
        project.plan.objects = [PlanObject(
            id="tree-west", kind="tree", x=30, y=30, radius=2, planting_zone_id="west",
            species_revision_id="tilia-cordata@2026-08-28.1",
        )]
    if not plan:
        project.plan = None
    attach_planting_zone_features(project)
    if project.plan is not None:
        app.validation.refresh(project, project.plan)
    app.repository.save(project)
    return app, app.get(project.id)


def draft(project, operation="update", **changes):
    values = {"operation": operation, "base_state_version": project.state_version}
    if operation == "create":
        values.update(label="New area", geometry=mapping(box(100, 100, 130, 130)))
    else:
        values["zone_id"] = "east"
        if operation == "update":
            values["label"] = "Renamed East"
    return ZoneChangeDraft(**{**values, **changes})


def approval(preview):
    return ZoneChangeCommit(preview_id=preview.id, digest=preview.digest,
                            base_state_version=preview.base_state_version)


def test_applied_receipt_recovers_exact_success_after_later_project_changes_and_preview_expiry():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project, "create"))
    assert service.get_applied(project.id, preview.id, preview.digest, preview.base_state_version) is None
    result = service.commit(project.id, approval(preview))
    changed = app.get(project.id)
    changed.name = "Later manual change"
    app.repository.save(changed)
    before = app.get(project.id).model_dump(mode="json")
    service._clock = lambda: preview.expires_at + timedelta(seconds=1)
    service._previews.clear()
    recovered = service.get_applied(project.id, preview.id, preview.digest, preview.base_state_version)
    assert recovered == result
    recovered.after_zones[0].geometry["coordinates"] = []
    assert service.get_applied(project.id, preview.id, preview.digest, preview.base_state_version) == result
    assert app.get(project.id).model_dump(mode="json") == before
    assert service.get_applied("other-project", preview.id, preview.digest, preview.base_state_version) is None
    assert ZoneChangeService(app.zones).get_applied(project.id, preview.id, preview.digest, preview.base_state_version) == result


@pytest.mark.parametrize("tamper", ["digest", "version", "boolean_version", "cached_project", "cached_preview"])
def test_applied_receipt_read_rejects_wrong_or_corrupted_identity(tamper):
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project))
    service.commit(project.id, approval(preview))
    digest, version = preview.digest, preview.base_state_version
    if tamper == "digest":
        digest = "a" * 64
    elif tamper == "version":
        version += 1
    elif tamper == "boolean_version":
        version = True
    else:
        value = json.loads(service._applied[(project.id, preview.id)])
        value["project_id" if tamper == "cached_project" else "preview_id"] = "other"
        service._applied[(project.id, preview.id)] = json.dumps(value)
    with pytest.raises(ValueError):
        service.get_applied(project.id, preview.id, digest, version)


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_exact_zone_population_preview_and_commit_preserve_all_plant_fields(operation):
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    before = project.model_dump(mode="json")
    preview = service.preview(project.id, draft(project, operation))
    assert preview.before_area_m2 == (None if operation == "create" else shape(project.planting_zones[1].geometry).area)
    assert preview.after_area_m2 == (None if operation == "delete" else shape(next(zone.geometry for zone in preview.after_zones if zone.id == preview.target_zone_id)).area)
    assert preview.can_apply and not preview.blockers and not preview.affected_planting_ids
    assert [zone.model_dump(mode="json") for zone in preview.before_zones] == before["planting_zones"]
    assert app.get(project.id).model_dump(mode="json") == before
    result = service.commit(project.id, approval(preview))
    saved = app.get(project.id)
    assert saved.state_version == result.state_version == project.state_version + 1
    assert saved.geometry_version == result.geometry_version == project.geometry_version + 1
    assert saved.plan.version == result.plan_version == project.plan.version
    assert saved.plan.objects == project.plan.objects
    assert [zone.model_dump(mode="json") for zone in saved.planting_zones] == [
        zone.model_dump(mode="json") for zone in preview.after_zones
    ]
    assert result.after_zones == preview.after_zones and result.plantings_unchanged
    assert saved.planting_zones[0] == project.planting_zones[0]
    assert len(saved.planting_zones) == {"create": 3, "update": 2, "delete": 1}[operation]
    if operation == "create":
        assert preview.target_zone_id not in {zone.id for zone in project.planting_zones}
        assert saved.planting_zones[-1].id == preview.target_zone_id


def test_preview_and_commit_work_before_a_manual_plan_exists():
    app, project = seeded(plants=False, plan=False)
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project, "create"))
    assert preview.base_plan_version is None
    result = service.commit(project.id, approval(preview))
    assert result.plan_version is None and app.get(project.id).plan is None


def test_rename_of_occupied_zone_is_allowed_without_touching_plantings():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project, zone_id="west", label="Renamed occupied area"))
    assert preview.can_apply
    service.commit(project.id, approval(preview))
    assert app.get(project.id).plan.objects == project.plan.objects


@pytest.mark.parametrize("changes", [
    {"operation": "delete", "zone_id": "west"},
    {"operation": "update", "zone_id": "west", "geometry": mapping(box(80, 80, 190, 190))},
    {"operation": "create", "label": "Cover planted tree", "geometry": mapping(box(20, 20, 40, 40))},
    {"operation": "create", "label": "Cross planted footprint", "geometry": mapping(box(30, 20, 40, 40))},
])
def test_occupied_deletion_and_geometric_impact_are_uncommittable(changes):
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, ZoneChangeDraft(base_state_version=project.state_version, **changes))
    assert not preview.can_apply and preview.blockers
    if changes["operation"] != "delete":
        assert preview.affected_planting_ids == ("tree-west",)
    with pytest.raises(ValueError):
        service.commit(project.id, approval(preview))
    assert app.get(project.id).model_dump(mode="json") == project.model_dump(mode="json")


def test_deleting_the_last_empty_zone_remains_blocked():
    app, project = seeded(plants=False)
    project.planting_zones = project.planting_zones[:1]
    app.repository.save(project)
    project = app.get(project.id)
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project, "delete", zone_id="west"))
    assert not preview.can_apply and "хотя бы один" in preview.blockers[0]
    with pytest.raises(ValueError):
        service.commit(project.id, approval(preview))


@pytest.mark.parametrize("geometry", [
    mapping(box(900, 900, 930, 930)),
    mapping(box(195, 20, 210, 60)),
    mapping(box(210, 10, 211, 11)),
    {"type": "Polygon", "coordinates": [[[210, 10], [230, 30], [210, 30], [230, 10], [210, 10]]]},
    {"type": "Point", "coordinates": [220, 20]},
])
def test_domain_geometry_failures_remain_visible_in_saved_uncommittable_preview(geometry):
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project, "create", geometry=geometry))
    assert not preview.can_apply and preview.preflight.can_save is False
    assert service.get_preview(project.id, preview.id, preview.digest) == preview
    with pytest.raises(ValueError):
        service.commit(project.id, approval(preview))
    assert app.get(project.id).state_version == project.state_version


def test_safe_geometry_update_away_from_existing_plants_preserves_untouched_zones():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project, geometry=mapping(box(410, 10, 490, 90))))
    assert preview.can_apply
    service.commit(project.id, approval(preview))
    assert app.get(project.id).planting_zones[0] == project.planting_zones[0]


def test_changing_nested_zone_priority_is_an_impact_even_when_both_cover_the_plant():
    app, project = seeded()
    project.planting_zones.append(PlantingZoneAssignment(id="inner", label="Inner", geometry=mapping(box(20, 20, 40, 40))))
    project.plan.objects[0].planting_zone_id = "inner"
    app.repository.save(project)
    project = app.get(project.id)
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project, zone_id="west", geometry=mapping(box(25, 25, 35, 35))))
    assert not preview.can_apply and preview.affected_planting_ids == ("tree-west",)
    with pytest.raises(ValueError):
        service.commit(project.id, approval(preview))


def test_first_zone_can_be_created_but_the_zone_count_cannot_exceed_the_domain_limit():
    app, project = seeded(plants=False, plan=False)
    project.planting_zones = []
    app.repository.save(project)
    project = app.get(project.id)
    service = ZoneChangeService(app.zones)
    first = service.preview(project.id, draft(project, "create"))
    assert first.before_zones == () and first.can_apply
    service.commit(project.id, approval(first))
    current = app.get(project.id)
    current.planting_zones = [PlantingZoneAssignment(
        id=f"area-{index}", label=f"Area {index}", geometry=mapping(box(100, 100, 130, 130)),
    ) for index in range(40)]
    app.repository.save(current)
    with pytest.raises(ValueError, match="не более 40"):
        service.preview(project.id, draft(app.get(project.id), "create"))


@pytest.mark.parametrize("invalid", [
    {"operation": "create", "zone_id": "forged", "label": "Test", "geometry": mapping(box(100, 100, 130, 130))},
    {"operation": "delete", "zone_id": "east", "label": "also rename"},
    {"operation": "delete"},
    {"operation": "update", "zone_id": "east"},
    {"operation": "update", "zone_id": "east", "label": "   "},
    {"operation": "update", "zone_id": "east", "label": "Test", "object_ids": ["tree-west"]},
    {"operation": "create", "label": "Test", "geometry": {"type": "Polygon", "coordinates": [float("nan")]}},
])
def test_closed_draft_refuses_ambiguous_and_extra_mutations(invalid):
    with pytest.raises((ValidationError, ValueError)):
        ZoneChangeDraft(base_state_version=1, **invalid)


def test_unknown_target_and_no_op_are_refused_without_a_proposal():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    for request in [draft(project, zone_id="missing"), draft(project, label="East")]:
        with pytest.raises(ValueError):
            service.preview(project.id, request)
    assert not service._previews


def test_commit_has_no_slot_for_replacing_the_approved_population():
    with pytest.raises(ValidationError):
        ZoneChangeCommit(preview_id="id", digest="a" * 64, base_state_version=1,
                         after_zones=[{"id": "injected"}])


def test_draft_and_returned_preview_mutations_cannot_change_the_saved_proposal():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    request = draft(project, "create")
    preview = service.preview(project.id, request)
    original = preview.model_dump(mode="json")
    request.geometry["coordinates"] = []
    preview.after_zones[-1].geometry["coordinates"] = []
    reloaded = service.get_preview(project.id, preview.id, preview.digest)
    assert reloaded.model_dump(mode="json") == original
    service.commit(project.id, approval(preview))
    assert app.get(project.id).planting_zones[-1].geometry == original["after_zones"][-1]["geometry"]


@pytest.mark.parametrize("mutation", ["digest", "state", "cross_project", "stored_population"])
def test_tampered_approval_or_saved_payload_never_writes(mutation):
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project))
    request = approval(preview)
    target_project = project.id
    if mutation == "digest":
        request = request.model_copy(update={"digest": "a" * 64})
    elif mutation == "state":
        request = request.model_copy(update={"base_state_version": project.state_version + 1})
    elif mutation == "cross_project":
        target_project = app.create_project("Other").id
    else:
        data = json.loads(service._previews[preview.id])
        data["after_zones"][0]["label"] = "Also change untouched West"
        service._previews[preview.id] = json.dumps(data)
    with pytest.raises(ValueError):
        service.commit(target_project, request)
    assert app.get(project.id).model_dump(mode="json") == project.model_dump(mode="json")


def test_ttl_eviction_and_restart_require_a_new_preview():
    app, project = seeded()
    now = [datetime(2026, 9, 10, tzinfo=UTC)]
    service = ZoneChangeService(app.zones, max_previews=1, ttl_seconds=10, clock=lambda: now[0])
    first = service.preview(project.id, draft(project, label="First"))
    second = service.preview(project.id, draft(project, label="Second"))
    with pytest.raises(ValueError, match="недоступно"):
        service.commit(project.id, approval(first))
    with pytest.raises(ValueError, match="недоступно"):
        ZoneChangeService(app.zones).commit(project.id, approval(second))
    now[0] += timedelta(seconds=10)
    with pytest.raises(ValueError, match="устарело"):
        service.commit(project.id, approval(second))


def test_stale_state_blocks_preview_lookup_and_commit():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project))
    changed = app.get(project.id)
    changed.name = "Concurrent edit"
    app.repository.save(changed)
    for action in [lambda: service.preview(project.id, draft(project)),
                   lambda: service.get_preview(project.id, preview.id, preview.digest),
                   lambda: service.commit(project.id, approval(preview))]:
        with pytest.raises(ProjectVersionConflict):
            action()
    assert app.get(project.id).planting_zones == project.planting_zones


@pytest.mark.parametrize("corruption", ["zone", "plant", "plan_version", "geometry_version"])
def test_before_snapshot_mismatch_is_detected_even_without_state_version_advance(corruption):
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project))
    stored = app.repository._projects[project.id]
    if corruption == "zone":
        stored.planting_zones[0].label = "Concurrent mutation"
    elif corruption == "plant":
        stored.plan.objects[0].locked = True
    elif corruption == "plan_version":
        stored.plan.version += 1
    else:
        stored.geometry_version += 1
    with pytest.raises(ValueError, match="Основание"):
        service.commit(project.id, approval(preview))


def test_commit_is_idempotent_and_shared_service_has_one_proposal_store():
    app, project = seeded()
    service = get_zone_change_service(app)
    preview = service.preview(project.id, draft(project))
    assert get_zone_change_service(app) is service
    first = service.commit(project.id, approval(preview))
    assert service.commit(project.id, approval(preview)) == first
    assert app.get(project.id).state_version == project.state_version + 1
    with pytest.raises(ValueError):
        service.commit(project.id, approval(preview).model_copy(update={"digest": "a" * 64}))


def test_only_one_of_two_conflicting_previews_can_commit():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    previews = [service.preview(project.id, draft(project, label=label)) for label in ["A", "B"]]
    barrier = Barrier(2)
    def commit(preview):
        barrier.wait(timeout=5)
        try:
            return service.commit(project.id, approval(preview)).status
        except ProjectVersionConflict:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(commit, previews))
    assert sorted(outcomes) == ["applied", "stale"]
    assert app.get(project.id).state_version == project.state_version + 1


def test_commit_keeps_the_outer_if_match_context_advanced():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project))
    token = set_expected_project_version(str(project.state_version))
    try:
        result = service.commit(project.id, approval(preview))
        assert expected_project_version() == result.state_version
        assert app.get(project.id).state_version == result.state_version
    finally:
        reset_expected_project_version(token)
    assert expected_project_version() is None


def test_commit_does_not_override_an_incompatible_outer_if_match():
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project))
    token = set_expected_project_version(str(project.state_version + 1))
    try:
        with pytest.raises(ProjectVersionConflict):
            service.commit(project.id, approval(preview))
        assert expected_project_version() == project.state_version + 1
    finally:
        reset_expected_project_version(token)
    assert app.get(project.id).state_version == project.state_version


@pytest.mark.parametrize("preserve", [True, False])
def test_plan_guard_refuses_validator_side_effects_before_write_and_keeps_manual_default(monkeypatch, preserve):
    app, project = seeded()
    original = app.validator.validate_plan
    def changing_validator(current, plan):
        issues = original(current, plan)
        plan.objects[0].locked = True
        return issues
    monkeypatch.setattr(app.validator, "validate_plan", changing_validator)
    zones = [zone.model_copy(deep=True) for zone in project.planting_zones]
    zones[-1].label = "Rename"
    if preserve:
        with pytest.raises(ValueError, match="существующие посадки"):
            app.save_planting_zones(project.id, zones, preserve_plan=True)
        assert app.get(project.id).model_dump(mode="json") == project.model_dump(mode="json")
        assert project.id not in app.validator._position_checkers
    else:
        # The existing manual call retains its previous default semantics.
        app.save_planting_zones(project.id, zones)
        assert app.get(project.id).plan.objects[0].locked


def test_cas_race_discards_prospective_validation_cache_and_preserves_winning_edit(monkeypatch):
    app, project = seeded()
    service = ZoneChangeService(app.zones)
    preview = service.preview(project.id, draft(project))
    original = app.repository.save
    def racing_save(candidate, **kwargs):
        token = set_expected_project_version(None)
        try:
            concurrent = app.get(project.id)
            concurrent.name = "Winning change"
            original(concurrent)
        finally:
            reset_expected_project_version(token)
        return original(candidate, **kwargs)
    monkeypatch.setattr(app.repository, "save", racing_save)
    with pytest.raises(ProjectVersionConflict):
        service.commit(project.id, approval(preview))
    current = app.get(project.id)
    assert current.name == "Winning change" and current.planting_zones == project.planting_zones
    assert current.plan.objects == project.plan.objects
    assert project.id not in app.validator._position_checkers
    assert not service._applied


def test_sqlite_commit_persists_exact_zones_and_survives_repository_reopen(tmp_path):
    app, project = seeded()
    path = tmp_path / "zones.sqlite3"
    repository = SqliteProjectRepository(path)
    with closing(repository._connection):
        app = recompose_application(app, repository=repository)
        project = repository.create(project)
        project = repository.get(project.id)
        service = ZoneChangeService(app.zones)
        preview = service.preview(project.id, draft(project, "create"))
        result = service.commit(project.id, approval(preview))
        restored = SqliteProjectRepository(path)
        with closing(restored._connection):
            saved = restored.get(project.id)
            assert saved.state_version == result.state_version
            assert saved.plan.objects == project.plan.objects
            assert [zone.model_dump(mode="json") for zone in saved.planting_zones] == [
                zone.model_dump(mode="json") for zone in preview.after_zones
            ]


def test_sqlite_concurrent_writer_cannot_be_overwritten_by_an_approved_zone_change(tmp_path, monkeypatch):
    app, project = seeded()
    path = tmp_path / "zone-race.sqlite3"
    repository = SqliteProjectRepository(path)
    other = SqliteProjectRepository(path)
    with closing(repository._connection), closing(other._connection):
        app = recompose_application(app, repository=repository)
        project = repository.create(project)
        project = repository.get(project.id)
        service = ZoneChangeService(app.zones)
        preview = service.preview(project.id, draft(project))
        original = repository.save_with_receipt
        def racing_save(candidate, *args, **kwargs):
            token = set_expected_project_version(None)
            try:
                concurrent = other.get(project.id)
                concurrent.name = "Other process"
                other.save(concurrent)
            finally:
                reset_expected_project_version(token)
            return original(candidate, *args, **kwargs)
        monkeypatch.setattr(repository, "save_with_receipt", racing_save)
        with pytest.raises(ProjectVersionConflict):
            service.commit(project.id, approval(preview))
        saved = other.get(project.id)
        assert saved.name == "Other process"
        assert saved.planting_zones == project.planting_zones and saved.plan.objects == project.plan.objects
        assert other.mutation_receipt(project.id, "planting_zones", preview.id) is None
        assert project.id not in app.validator._position_checkers
