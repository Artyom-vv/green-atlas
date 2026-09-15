"""Stable derived findings and an explicit validation basis across operations."""

from application_factory import recompose_application

from app.planting_zones.domain import attach_planting_zone_features
from copy import deepcopy

from app.contracts import Plan, PlanObject, Project
from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import InMemoryProjectHistory
from app.projects.adapters import SqliteProjectRepository
from app.validation.adapters import RuleBasedPlanValidator
from test_placement_allocation import application


def overlapping_plan():
    return Plan(id="plan", objects=[
        PlanObject(id="b", kind="tree", x=3, y=0, radius=2),
        PlanObject(id="a", kind="tree", x=0, y=0, radius=2),
    ])


def test_repeated_validation_and_object_reordering_preserve_complete_findings():
    project = Project(id="project", name="Repeated validation")
    plan = overlapping_plan()
    validator = RuleBasedPlanValidator()
    first = validator.validate_plan(project, plan)
    assert first and len({issue.id for issue in first}) == len(first)
    assert validator.validate_plan(project, plan) == first
    reordered = plan.model_copy(deep=True)
    reordered.objects.reverse()
    assert RuleBasedPlanValidator().validate_plan(project, reordered) == first
    assert [obj.id for obj in reordered.objects] == ["a", "b"]


def test_measurement_changes_preserve_finding_identity_but_not_stale_values():
    project = Project(id="project", name="Distance")
    plan = overlapping_plan()
    validator = RuleBasedPlanValidator()
    original = next(issue for issue in validator.validate_plan(project, plan) if issue.code == "PLANT_SPACING")
    plan.objects[0].x = 2
    changed = next(issue for issue in validator.validate_plan(project, plan) if issue.code == "PLANT_SPACING")
    assert changed.id == original.id
    assert changed.actual != original.actual and changed.description != original.description
    plan.objects[0].x = 50
    assert all(issue.code != "PLANT_SPACING" for issue in validator.validate_plan(project, plan))
    plan.objects[0].x = 3
    assert next(issue for issue in validator.validate_plan(project, plan) if issue.code == "PLANT_SPACING") == original


def test_finding_identity_is_scoped_to_project_and_plan():
    validator = RuleBasedPlanValidator()
    first = validator.validate_plan(Project(id="one", name="One"), overlapping_plan())
    second = validator.validate_plan(Project(id="two", name="Two"), overlapping_plan())
    assert {issue.id for issue in first}.isdisjoint(issue.id for issue in second)
    other_plan = overlapping_plan().model_copy(update={"id": "other-plan"})
    third = validator.validate_plan(Project(id="one", name="One"), other_plan)
    assert {issue.id for issue in first}.isdisjoint(issue.id for issue in third)


def seeded():
    app, project = application()
    app = recompose_application(app, history=InMemoryProjectHistory())
    app = recompose_application(app, geometry_query=IndexedGeometryQuery())
    project.plan.objects = [PlanObject(id="tree", kind="tree", x=30, y=30, radius=2, planting_zone_id="west")]
    attach_planting_zone_features(project)
    app.validation.refresh(project, project.plan)
    return app, app.repository.save(project)


def test_zone_rename_preserves_findings_and_plantings_and_records_new_geometry_basis():
    app, project = seeded()
    before = deepcopy(project.plan)
    assert before.issues and before.validation_basis
    zones = [zone.model_copy(update={"label": "Renamed"}) if zone.id == "east" else zone for zone in project.planting_zones]
    after = app.save_planting_zones(project.id, zones, preserve_plan=True)
    assert after.plan.id == before.id and after.plan.version == before.version
    assert after.plan.objects == before.objects and after.plan.issues == before.issues
    assert after.plan.validation_basis == before.validation_basis.model_copy(update={"geometry_version": after.geometry_version})
    assert after.geometry_version == project.geometry_version + 1
    app.validation.refresh(after, after.plan)
    assert after.plan.issues == before.issues


def test_sqlite_reload_and_fresh_validator_preserve_validation_identity_and_basis(tmp_path):
    app, project = seeded()
    app = recompose_application(app, repository=SqliteProjectRepository(str(tmp_path / "validation.sqlite3")))
    saved = app.repository.create(project)
    before = saved.plan.model_dump(mode="json")
    app = recompose_application(app, repository=SqliteProjectRepository(str(tmp_path / "validation.sqlite3")))
    app = recompose_application(app, validator=RuleBasedPlanValidator())
    reloaded = app.get(saved.id)
    app.validation.refresh(reloaded, reloaded.plan)
    assert reloaded.plan.model_dump(mode="json") == before


def test_object_digest_excludes_derived_status_and_detects_geometry_changes():
    app, project = seeded()
    original = deepcopy(project.plan.validation_basis)
    project.plan.objects[0].status = "error"
    app.validation.refresh(project, project.plan)
    assert project.plan.validation_basis == original
    project.plan.objects[0].x += 1
    app.validation.refresh(project, project.plan)
    assert project.plan.validation_basis.objects_digest != original.objects_digest


def test_legacy_plan_does_not_claim_a_validation_basis_until_rechecked():
    assert Plan.model_validate({"id": "legacy", "objects": [], "issues": [], "version": 1}).validation_basis is None
