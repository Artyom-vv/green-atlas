"""Normal zone writes keep plantings, receipts and concurrent revisions intact."""

from pathlib import Path
from typing import Any, Literal

import pytest
from application_factory import recompose_application
from pydantic import ValidationError
from shapely.geometry import box, mapping
from test_placement_allocation import application

from app.application import ProjectApplication
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.history.adapters import SqliteProjectHistory
from app.planning.contracts import Plan, PlanObject
from app.planting_zones.change_contracts import ZoneChangeResult, ZoneSnapshot
from app.planting_zones.config import MAX_ZONE_COUNT
from app.planting_zones.contracts import PlantingZoneAssignment, PlantingZonesRequest
from app.planting_zones.domain import attach_planting_zone_features
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    expected_project_version,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason
from app.validation.adapters import RuleBasedPlanValidator
from app.validation.contracts import ValidationIssue


def seeded(
    *, plants: bool = True, plan: bool = True
) -> tuple[ProjectApplication, Project]:
    app, project = application()
    app = recompose_application(app, geometry_query=IndexedGeometryQuery())
    assert project.plan is not None
    if plants:
        project.plan.objects = [
            PlanObject(
                id="tree-west",
                kind="tree",
                x=30,
                y=30,
                radius=2,
                planting_zone_id="west",
                species_revision_id="tilia-cordata@2026-08-28.1",
            )
        ]
    if not plan:
        project.plan = None
    attach_planting_zone_features(project)
    if project.plan is not None:
        app.validation.refresh(project, project.plan)
    project = Project.model_validate_json(project.model_dump_json())
    app.repository.save(project)
    return app, app.get(project.id)


def draft(
    project: Project,
    operation: Literal["create", "update", "delete"] = "update",
    *,
    zone_id: str = "east",
    label: str | None = None,
    geometry: dict[str, Any] | None = None,
) -> list[PlantingZoneAssignment]:
    """Build the zone population sent by the existing manual editor."""
    zones = [zone.model_copy(deep=True) for zone in project.planting_zones]
    if operation == "create":
        zones.append(
            PlantingZoneAssignment(
                id="new-area",
                label=label or "New area",
                geometry=geometry or mapping(box(100, 100, 130, 130)),
            )
        )
    elif operation == "delete":
        zones = [zone for zone in zones if zone.id != zone_id]
    else:
        target = next(zone for zone in zones if zone.id == zone_id)
        target.label = label or "Renamed East"
        if geometry is not None:
            target.geometry = geometry
    return [
        PlantingZoneAssignment.model_validate_json(zone.model_dump_json())
        for zone in zones
    ]


def test_applied_zone_receipt_recovers_exact_success_after_later_project_changes() -> (
    None
):
    app, project = seeded()
    assert project.plan is not None
    zones = draft(project)
    receipt = ZoneChangeResult(
        preview_id="retained-zone-operation",
        digest="a" * 64,
        project_id=project.id,
        operation="update",
        target_zone_id="east",
        base_state_version=project.state_version,
        state_version=project.state_version + 1,
        geometry_version=project.geometry_version + 1,
        plan_version=project.plan.version,
        after_zones=tuple(
            ZoneSnapshot.model_validate(zone.model_dump()) for zone in zones
        ),
    )
    assert (
        app.zones.get_zone_change_receipt(
            project.id, receipt.preview_id, receipt.digest, receipt.base_state_version
        )
        is None
    )
    app.zones.save_planting_zones(
        project.id,
        zones,
        preserve_plan=True,
        mutation_receipt=receipt.model_dump(mode="json"),
    )
    changed = app.get(project.id)
    changed.name = "Later manual change"
    app.repository.save(changed)
    before = app.get(project.id).model_dump_json()
    recovered = app.zones.get_zone_change_receipt(
        project.id, receipt.preview_id, receipt.digest, receipt.base_state_version
    )
    assert recovered == receipt
    assert recovered is not None
    recovered.after_zones[0].geometry["coordinates"] = []
    assert (
        app.zones.get_zone_change_receipt(
            project.id, receipt.preview_id, receipt.digest, receipt.base_state_version
        )
        == receipt
    )
    for digest, version in [
        ("b" * 64, receipt.base_state_version),
        (receipt.digest, receipt.base_state_version + 1),
    ]:
        with pytest.raises(DomainFailure) as failure:
            app.zones.get_zone_change_receipt(
                project.id, receipt.preview_id, digest, version
            )
        assert failure.value.reason is FailureReason.ZONE_CHANGE_RECEIPT_MISMATCH
        assert failure.value.params == {}
    assert (
        app.zones.get_zone_change_receipt(
            "other-project",
            receipt.preview_id,
            receipt.digest,
            receipt.base_state_version,
        )
        is None
    )
    assert app.get(project.id).model_dump_json() == before


@pytest.mark.parametrize("operation", ["create", "update", "delete"])
def test_exact_zone_population_write_preserves_all_plant_fields(
    operation: Literal["create", "update", "delete"],
) -> None:
    app, project = seeded()
    zones = draft(project, operation)
    saved = app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    assert saved.plan is not None and project.plan is not None
    assert saved.plan.model_dump(
        exclude={"validation_basis"}
    ) == project.plan.model_dump(exclude={"validation_basis"})
    assert saved.state_version == project.state_version + 1
    assert saved.geometry_version == project.geometry_version + 1
    assert saved.planting_zones == zones
    assert saved.planting_zones[0] == project.planting_zones[0]
    zones[0].geometry["coordinates"] = []
    assert app.get(project.id).planting_zones[0] == project.planting_zones[0]


def test_zone_creation_works_before_a_manual_plan_exists() -> None:
    app, project = seeded(plants=False, plan=False)
    saved = app.zones.save_planting_zones(project.id, draft(project, "create"))
    assert saved.plan is None
    assert len(saved.planting_zones) == 3


def test_rename_of_occupied_zone_is_allowed_without_touching_plantings() -> None:
    app, project = seeded()
    saved = app.zones.save_planting_zones(
        project.id,
        draft(project, zone_id="west", label="Renamed occupied area"),
        preserve_plan=True,
    )
    assert saved.plan is not None and project.plan is not None
    assert saved.plan.model_dump(
        exclude={"validation_basis"}
    ) == project.plan.model_dump(exclude={"validation_basis"})


def test_deleting_the_last_empty_zone_succeeds_without_changing_plan() -> None:
    app, project = seeded(plants=False)
    project.planting_zones = project.planting_zones[:1]
    app.repository.save(project)
    project = app.get(project.id)
    saved = app.zones.save_planting_zones(project.id, [], preserve_plan=True)
    assert saved.planting_zones == []
    assert saved.plan is not None and project.plan is not None
    assert saved.plan.model_dump(
        exclude={"validation_basis"}
    ) == project.plan.model_dump(exclude={"validation_basis"})
    assert saved.state_version == project.state_version + 1


@pytest.mark.parametrize(
    "geometry",
    [
        mapping(box(900, 900, 930, 930)),
        mapping(box(195, 20, 210, 60)),
        mapping(box(210, 10, 211, 11)),
        {
            "type": "Polygon",
            "coordinates": [[[210, 10], [230, 30], [210, 30], [230, 10], [210, 10]]],
        },
        {"type": "Point", "coordinates": [220, 20]},
    ],
)
def test_domain_geometry_failures_remain_visible_without_a_write(
    geometry: dict[str, Any],
) -> None:
    app, project = seeded()
    zones = draft(project, "create", geometry=geometry)
    assert not app.zones.preview_planting_zone(project.id, zones[-1])["can_save"]
    with pytest.raises(ValueError):
        app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    assert app.get(project.id).model_dump_json() == project.model_dump_json()


def test_public_zone_population_cannot_exceed_domain_limit() -> None:
    zones = [
        PlantingZoneAssignment(
            id=f"area-{index}",
            label=f"Area {index}",
            geometry=mapping(box(100, 100, 130, 130)),
        )
        for index in range(MAX_ZONE_COUNT)
    ]
    assert PlantingZonesRequest(zones=zones).zones == zones
    with pytest.raises(ValidationError):
        PlantingZonesRequest(
            zones=zones + [zones[0].model_copy(update={"id": "extra"})]
        )


@pytest.mark.parametrize("preserve", [True, False])
def test_plan_guard_refuses_validator_side_effects_before_write_and_keeps_manual_default(
    monkeypatch: pytest.MonkeyPatch, preserve: bool
) -> None:
    app, project = seeded()
    assert isinstance(app.validator, RuleBasedPlanValidator)
    # Внедряемая проверка меняет ручные поля. Помечаем её прежний результат
    # устаревшим, чтобы проверить защиту именно при обязательном полном вызове.
    assert project.plan is not None
    project.plan.validation_basis = None
    project = app.repository.save(project)
    original = app.validator.validate_plan

    def changing_validator(current: Project, plan: Plan) -> list[ValidationIssue]:
        issues = original(current, plan)
        plan.objects[0].locked = True
        return issues

    monkeypatch.setattr(app.validator, "validate_plan", changing_validator)
    zones = draft(project)
    if preserve:
        with pytest.raises(DomainFailure) as failure:
            app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
        assert failure.value.reason is FailureReason.ZONE_CHANGE_PLANTINGS_AFFECTED
        assert failure.value.params == {}
        assert app.get(project.id).model_dump_json() == project.model_dump_json()
        assert project.id not in app.validator._position_checkers
    else:
        saved = app.zones.save_planting_zones(project.id, zones)
        assert saved.plan is not None
        assert saved.plan.objects[0].locked


def test_cas_race_discards_prospective_validation_cache_and_preserves_winning_edit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = seeded()
    assert isinstance(app.validator, RuleBasedPlanValidator)
    original = app.repository.save

    def racing_save(
        candidate: Project,
        *,
        source: bytes | bytearray | None = None,
        source_components: dict[str, bytes] | None = None,
    ) -> Project:
        token = set_expected_project_version(None)
        try:
            concurrent = app.get(project.id)
            concurrent.name = "Winning change"
            original(concurrent)
        finally:
            reset_expected_project_version(token)
        return original(candidate, source=source, source_components=source_components)

    monkeypatch.setattr(app.repository, "save", racing_save)
    with pytest.raises(ProjectVersionConflict):
        app.zones.save_planting_zones(project.id, draft(project), preserve_plan=True)
    current = app.get(project.id)
    assert (
        current.name == "Winning change"
        and current.planting_zones == project.planting_zones
    )
    assert current.plan == project.plan
    assert project.id not in app.validator._position_checkers


def test_zone_write_keeps_outer_if_match_advanced_and_rejects_stale_context() -> None:
    app, project = seeded()
    token = set_expected_project_version(str(project.state_version))
    try:
        saved = app.zones.save_planting_zones(
            project.id, draft(project), preserve_plan=True
        )
        assert expected_project_version() == saved.state_version
        with pytest.raises(ProjectVersionConflict):
            set_expected_project_version(str(project.state_version))
            app.zones.save_planting_zones(
                project.id, draft(project), preserve_plan=True
            )
    finally:
        reset_expected_project_version(token)
    assert expected_project_version() is None


def test_sqlite_zone_write_survives_repository_reopen(tmp_path: Path) -> None:
    app, project = seeded()
    path = tmp_path / "zones.sqlite3"
    repository = SqliteProjectRepository(path)
    try:
        app = recompose_application(
            app, repository=repository, history=SqliteProjectHistory(repository)
        )
        project = repository.create(project)
        zones = draft(project, "create")
        saved = app.zones.save_planting_zones(project.id, zones, preserve_plan=True)
    finally:
        repository.close()
    restored = SqliteProjectRepository(path)
    try:
        reloaded = restored.get(project.id)
        assert reloaded.state_version == saved.state_version
        assert reloaded.plan is not None and project.plan is not None
        assert reloaded.plan.model_dump(
            exclude={"validation_basis"}
        ) == project.plan.model_dump(exclude={"validation_basis"})
        assert reloaded == saved
        assert reloaded.planting_zones == zones
    finally:
        restored.close()


def test_sqlite_concurrent_writer_cannot_be_overwritten_by_zone_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app, project = seeded()
    assert isinstance(app.validator, RuleBasedPlanValidator)
    validator = app.validator
    path = tmp_path / "zone-race.sqlite3"
    repository = SqliteProjectRepository(path)
    other = SqliteProjectRepository(path)
    try:
        app = recompose_application(
            app, repository=repository, history=SqliteProjectHistory(repository)
        )
        project = repository.create(project)
        original = repository.save

        def racing_save(
            candidate: Project,
            *,
            source: bytes | bytearray | None = None,
            source_components: dict[str, bytes] | None = None,
        ) -> Project:
            token = set_expected_project_version(None)
            try:
                concurrent = other.get(project.id)
                concurrent.name = "Other process"
                other.save(concurrent)
            finally:
                reset_expected_project_version(token)
            return original(
                candidate, source=source, source_components=source_components
            )

        monkeypatch.setattr(repository, "save", racing_save)
        with pytest.raises(ProjectVersionConflict):
            app.zones.save_planting_zones(
                project.id, draft(project), preserve_plan=True
            )
        saved = other.get(project.id)
        assert saved.name == "Other process"
        assert (
            saved.planting_zones == project.planting_zones
            and saved.plan == project.plan
        )
        assert project.id not in validator._position_checkers
    finally:
        repository.close()
        other.close()
