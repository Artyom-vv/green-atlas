"""Territory filtering and spatial alternatives change actual proposed plants."""

from pathlib import Path

import pytest
from shapely.geometry import box, mapping
from test_placement_allocation import application

from app.dxf_import.review_contracts import SourceReview
from app.history.adapters import InMemoryProjectHistory
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.recommendation_contracts import RecommendationRequest
from app.species.assortment import TerritoryContext, assortment_status
from scripts.planning_lab.contracts import parse_case
from scripts.planning_lab.runner import run_case


def preview_saved(app, project, req):
    current = app.repository.get(project.id)
    for zone in current.planting_zones:
        if zone.id in req.zone_ids:
            zone.territory = req.territory or TerritoryContext(
                category="courtyard",
                regime="ordinary",
                basis="Explicit synthetic test category",
            )
            zone.site_conditions = req.site_conditions
    # A repeated read-only preview should not create a new basis version.
    if current.planting_zones != app.repository.get(project.id).planting_zones:
        app.repository.save(current)
    return app.preview_recommendation(project.id, req)


def context(category="courtyard", regime="ordinary"):
    return TerritoryContext(
        category=category, regime=regime, basis="Explicit test brief"
    )


def request(project, category=None, profile="balanced"):
    return RecommendationRequest(
        base_plan_version=project.plan.version,
        zone_ids=["west"],
        max_sites=5,
        profile=profile,
        territory=context(category) if category else None,
    )


@pytest.mark.parametrize(
    "species,category",
    [
        ("betula-pendula", "healthcare"),
        ("sorbus-aucuparia", "preschool"),
        ("sorbus-aucuparia", "school_sport"),
        ("picea-abies", "major_road"),
        ("quercus-robur", "industrial"),
    ],
)
def test_reviewed_negative_cells_are_not_automatic_choices(species, category):
    assert assortment_status(species, context(category)) == "not_recommended"


def test_healthcare_replaces_fixed_birch_with_a_qualified_alternative():
    app, project = application()
    original = preview_saved(app, project, request(project))
    assert all(
        p.species_revision_id.startswith("betula-pendula@")
        for p in original.change_set.additions
    )
    result = preview_saved(app, project, request(project, "healthcare"))
    assert result.change_set and result.change_set.can_apply
    assert all(
        not p.species_revision_id.startswith("betula-pendula@")
        for p in result.change_set.additions
    )
    birch = next(
        o
        for o in result.species_options
        if o.species_revision_id.startswith("betula-pendula@")
    )
    assert birch.assortment_status == "not_recommended"
    assert birch.source_page == 3 and birch.source_row == 2
    assert app.get(project.id).plan.objects == []


def test_narrow_zone_finds_smaller_species_instead_of_empty_fixed_linden():
    app, project = application()
    project.planting_zones[0].geometry = mapping(box(0, 0, 16, 150))
    project = app.repository.save(project)
    original = preview_saved(app, project, request(project, profile="shade"))
    assert original.change_set and original.change_set.can_apply
    result = preview_saved(app, project, request(project, "courtyard", "shade"))
    assert result.change_set and result.change_set.additions
    assert all(
        not p.species_revision_id.startswith("tilia-cordata@")
        for p in result.change_set.additions
    )
    assert all(
        e.effect != "shade" or e.status == "unknown"
        for ex in result.explanations
        for e in ex.effects
    )
    assert "не расчёт тени" in result.selection_reason


@pytest.mark.parametrize("regime", ["individual_project", "unknown"])
def test_special_or_unknown_regime_does_not_inherit_ordinary_table(regime):
    app, project = application()
    req = request(project)
    req.territory = context(regime=regime)
    result = preview_saved(app, project, req)
    assert result.change_set is None
    assert all(
        o.assortment_status == "individual_review" for o in result.species_options
    )
    assert not app.changes._previews


def test_unreviewed_species_is_not_inferred_from_a_related_elm():
    assert assortment_status("ulmus-laevis", context()) == "unreviewed"


def test_trials_do_not_fill_apply_cache_and_selected_result_is_applicable():
    app, project = application()
    app.history = InMemoryProjectHistory()
    app.history_application.history = app.history
    result = preview_saved(
        app, project, request(project, "preschool", "low_future_conflict")
    )
    assert len(app.changes._previews) == 1
    assert result.change_set and result.change_set.can_apply
    assert all(
        not p.species_revision_id.startswith("sorbus-aucuparia@")
        for p in result.change_set.additions
    )
    first = [(p.x, p.y, p.species_revision_id) for p in result.change_set.additions]
    repeated = preview_saved(
        app, project, request(project, "preschool", "low_future_conflict")
    )
    assert first == [
        (p.x, p.y, p.species_revision_id) for p in repeated.change_set.additions
    ]
    app.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=result.change_set.id,
            digest=result.change_set.digest,
            base_plan_version=project.plan.version,
        ),
    )
    assert len(app.get(project.id).plan.objects) == len(first)


def test_pending_geometry_cannot_be_bypassed_by_species_search():
    app, project = application()
    project.source_review = SourceReview()
    app.repository.save(project)
    with pytest.raises(ValueError, match="расчёта ограничений"):
        preview_saved(app, project, request(project, "courtyard"))


def test_frozen_recommendation_case_is_repeatable_and_records_alternative_checks():
    fixture = (
        Path(__file__).resolve().parents[3]
        / "fixtures/planning-lab/narrow-courtyard-territory-saved-context.json"
    )
    source = parse_case(fixture.read_bytes())
    first, second = run_case(source), run_case(source)
    assert first["content_sha256"] == second["content_sha256"]
    assert first["content"]["result"]["change_set"]["additions"]
    assert len(first["content"]["generation_calls"]) > 1
    assert len(first["content"]["result"]["species_options"]) == 12
