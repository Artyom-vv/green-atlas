"""Saved zone policy applies to manual, bulk, automatic and validation paths."""

import pytest
from application_factory import recompose_application
from test_placement_allocation import application

from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import InMemoryProjectHistory
from app.planning.change_contracts import PlanChangeSetApplyRequest, PlanChangeSetDraft
from app.planning.contracts import PlanObject
from app.planning.recommendation_contracts import RecommendationRequest
from app.species.assortment import TerritoryContext
from app.species.catalog import list_species
from app.species.placement_policy import plant_eligibility


def context(category):
    return TerritoryContext(
        category=category, regime="ordinary", basis="Контрольный участок"
    )


def ready():
    app, project = application()
    app = recompose_application(
        app, geometry_query=IndexedGeometryQuery(), history=InMemoryProjectHistory()
    )
    for zone, category in zip(
        project.planting_zones, ["courtyard", "preschool"], strict=True
    ):
        zone.territory = context(category)
    project = app.repository.save(project)
    return app, project


def addition(x=20, species="sorbus-aucuparia@2026-08-28.1"):
    return dict(
        type="add", object=dict(kind="tree", x=x, y=20, species_revision_id=species)
    )


def preview(app, project, ops, source="manual"):
    return app.preview_change_set(
        project.id,
        PlanChangeSetDraft(
            base_plan_version=project.plan.version,
            label="Проверка ассортимента",
            source=source,
            operations=ops,
        ),
    )


@pytest.mark.parametrize(
    "source", ["manual", "group", "pattern", "recommendation", "brush", "system"]
)
def test_no_source_can_bypass_negative_cell(source):
    app, project = ready()
    rejected = preview(app, project, [addition(420)], source)
    assert not rejected.can_apply and not rejected.additions
    check = rejected.candidate_results[0]
    assert check.code == "ASSORTMENT_NOT_RECOMMENDED"
    assert check.assortment.category == "preschool"
    assert check.assortment.source_row_id == "main-deciduous_tree-42"
    assert app.get(project.id).plan.objects == []


def test_manual_move_checks_destination_not_stale_zone_id():
    app, project = ready()
    project.plan.objects = [
        PlanObject(
            id="tree",
            kind="tree",
            x=20,
            y=20,
            radius=1.5,
            species_revision_id="sorbus-aucuparia@2026-08-28.1",
            planting_zone_id="west",
        )
    ]
    app.repository.save(project)
    result = preview(
        app, project, [dict(type="update", object_id="tree", changes=dict(x=420))]
    )
    assert not result.can_apply
    assert result.candidate_results[0].assortment.zone_id == "east"
    assert app.get(project.id).plan.objects[0].x == 20


def test_missing_context_never_infers_courtyard_or_accepts_anonymous_plant():
    app, project = application(with_context=False)
    result = preview(app, project, [addition()])
    assert result.candidate_results[0].code == "ASSORTMENT_CONTEXT_REQUIRED"
    assert (
        plant_eligibility(None, context("courtyard")).code
        == "ASSORTMENT_SPECIES_REQUIRED"
    )


def test_two_zones_get_different_species_and_one_atomic_preview():
    app, project = ready()
    # Birch is excluded for healthcare; preschool permits it. Categories come
    # from each saved zone, never an overriding category in the request.
    project.planting_zones[0].territory = context("healthcare")
    app.repository.save(project)
    result = app.preview_recommendation(
        project.id,
        RecommendationRequest(
            base_plan_version=1,
            zone_ids=["west", "east"],
            max_sites=4,
            profile="balanced",
        ),
    )
    assert result.change_set and result.change_set.can_apply
    assert [
        (z.zone_id, z.requested_count, z.accepted_count) for z in result.zone_results
    ] == [("west", 2, 2), ("east", 2, 2)]
    for plant in result.change_set.additions:
        zone = next(z for z in project.planting_zones if z.id == plant.planting_zone_id)
        assert plant_eligibility(plant.species_revision_id, zone.territory).allowed
    assert all(
        not p.species_revision_id.startswith("betula-pendula@")
        for p in result.change_set.additions
        if p.planting_zone_id == "west"
    )
    assert len(app.changes._previews) == 1
    assert {e.object_id for e in result.explanations} == {
        p.id for p in result.change_set.additions
    }


def test_request_cannot_override_saved_zone():
    app, project = ready()
    with pytest.raises(ValueError, match="отличается"):
        app.preview_recommendation(
            project.id,
            RecommendationRequest(
                base_plan_version=1, zone_ids=["east"], territory=context("courtyard")
            ),
        )


def test_changing_only_saved_categories_changes_the_selected_species():
    app, project = ready()
    project.planting_zones[0].territory = context("healthcare")
    request = RecommendationRequest(
        base_plan_version=1, zone_ids=["west", "east"], max_sites=4
    )

    def selected_by_zone():
        app.repository.save(project)
        result = app.preview_recommendation(project.id, request)
        assert result.change_set and result.change_set.can_apply
        assert len(result.change_set.additions) == 4
        return {
            zone.id: {
                p.species_revision_id
                for p in result.change_set.additions
                if p.planting_zone_id == zone.id
            }
            for zone in project.planting_zones
        }

    original = selected_by_zone()
    assert original["west"] != original["east"]
    project.planting_zones[0].territory, project.planting_zones[1].territory = (
        project.planting_zones[1].territory,
        project.planting_zones[0].territory,
    )
    swapped = selected_by_zone()
    assert swapped["west"] == original["east"]
    assert swapped["east"] == original["west"]
    assert app.get(project.id).plan.objects == []


def test_saved_category_change_invalidates_old_preview():
    app, project = ready()
    result = preview(app, project, [addition()])
    assert result.can_apply
    project.planting_zones[0].territory = context("preschool")
    app.repository.save(project)
    with pytest.raises(ValueError):
        app.apply_change_set(
            project.id,
            PlanChangeSetApplyRequest(
                preview_id=result.id, digest=result.digest, base_plan_version=1
            ),
        )
    assert not app.get(project.id).plan.objects


def test_new_profiles_keep_explicit_model_uncertainty_and_municipal_limits():
    added = [s for s in list_species() if s.id.endswith("@2026-09-21.1")]
    assert len(added) == 6 and len(list_species()) == 20
    assert all(s.root_architecture == "uncertain" for s in added)
    assert not plant_eligibility(
        "berberis-thunbergii@2026-09-21.1", context("preschool")
    ).allowed
    assert not plant_eligibility(
        "thuja-occidentalis@2026-09-21.1", context("major_road")
    ).allowed
    assert plant_eligibility(
        "hydrangea-arborescens@2026-09-21.1", context("courtyard")
    ).allowed


def test_unknown_revision_and_blank_basis_cannot_be_approved():
    assert (
        plant_eligibility("missing@revision", context("courtyard")).code
        == "ASSORTMENT_SPECIES_UNKNOWN"
    )
    with pytest.raises(ValueError):
        TerritoryContext(category="courtyard", regime="ordinary", basis="   ")
    app, project = ready()
    result = preview(app, project, [addition(species="missing@revision")])
    assert not result.can_apply
    assert result.candidate_results[0].assortment.code == "ASSORTMENT_SPECIES_UNKNOWN"


def test_old_unqualified_plan_can_be_repaired_without_reimporting():
    app, project = application(with_context=False)
    app = recompose_application(
        app, geometry_query=IndexedGeometryQuery(), history=InMemoryProjectHistory()
    )
    project.plan.objects = [
        PlanObject(
            id="old",
            kind="tree",
            x=20,
            y=20,
            radius=1.5,
            species_revision_id="sorbus-aucuparia@2026-08-28.1",
            locked=True,
        )
    ]
    app.repository.save(project)
    app.validation.refresh(project, project.plan)
    assert any(i.code == "ASSORTMENT_CONTEXT_REQUIRED" for i in project.plan.issues)
    unlock = preview(
        app, project, [dict(type="update", object_id="old", changes=dict(locked=False))]
    )
    assert unlock.can_apply and unlock.candidate_results[0].assortment is None
    app.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=unlock.id,
            digest=unlock.digest,
            base_plan_version=project.plan.version,
        ),
    )
    current = app.get(project.id)
    assert not current.plan.objects[0].locked
    assert preview(app, current, [dict(type="delete", object_id="old")]).can_apply
    zones = [z.model_copy(deep=True) for z in current.planting_zones]
    zones[0].territory = context("courtyard")
    saved = app.save_planting_zones(project.id, zones)
    assert not any(i.rule_id == "moscow_assortment" for i in saved.plan.issues)
    assert saved.plan.objects[0].id == "old"
    assert app.get(project.id).planting_zones[0].territory == context("courtyard")


def test_replacement_and_known_site_conflicts_use_same_policy():
    from app.species.site_contracts import SiteConditions

    app, project = ready()
    project.plan.objects = [
        PlanObject(
            id="east",
            kind="tree",
            x=420,
            y=20,
            radius=1.5,
            species_revision_id="tilia-cordata@2026-08-28.1",
        )
    ]
    app.repository.save(project)
    result = preview(
        app,
        project,
        [
            dict(
                type="update",
                object_id="east",
                changes=dict(species_revision_id="sorbus-aucuparia@2026-08-28.1"),
            )
        ],
    )
    assert not result.can_apply
    assert result.candidate_results[0].code == "ASSORTMENT_NOT_RECOMMENDED"
    project.planting_zones[0].site_conditions = SiteConditions(
        light="full_shade", basis="Synthetic shaded courtyard"
    )
    app.repository.save(project)
    result = preview(app, project, [addition(species="acer-ginnala@2026-09-21.1")])
    assert not result.can_apply
    assert result.candidate_results[0].code in {
        "SITE_CONDITIONS_CONFLICT",
        "SITE_CONDITIONS_UNREVIEWED",
    }


def test_zone_rename_receipt_preserves_context_and_rejects_tampering():
    from app.planting_zone_changes import (
        ZoneChangeCommit,
        ZoneChangeDraft,
        get_zone_change_service,
    )

    app, project = ready()
    service = get_zone_change_service(app)
    proposed = service.preview(
        project.id,
        ZoneChangeDraft(
            operation="update",
            zone_id="west",
            label="Updated courtyard",
            base_state_version=project.state_version,
        ),
    )
    assert proposed.before_zones[0].territory == context("courtyard")
    proposed.after_zones[0].territory.category = "industrial"
    # The mutable response must not mutate the saved approval snapshot.
    service.commit(
        project.id,
        ZoneChangeCommit(
            preview_id=proposed.id,
            digest=proposed.digest,
            base_state_version=project.state_version,
        ),
    )
    assert app.get(project.id).planting_zones[0].territory == context("courtyard")
