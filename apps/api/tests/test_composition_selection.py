"""Automatic species pairs share the real geometry and mutation safeguards."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from test_placement_allocation import application

from app.history.adapters import InMemoryProjectHistory
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.composition_selection import select_composition
from app.planning.composition_selection_contracts import CompositionSelectionRequest
from app.planning.domain import PlanVersionConflict
from app.planning.species_qualification import eligible
from app.species.catalog import get_species
from scripts.planning_lab.contracts import CompositionSelectionCase, parse_case
from scripts.planning_lab.runner import run_case


def case():
    result = parse_case(
        (
            Path(__file__).resolve().parents[3]
            / "fixtures/planning-lab/automatic-mixed-courtyard-saved-context.json"
        ).read_bytes()
    )
    assert isinstance(result, CompositionSelectionCase)
    return result


def test_all_pairs_are_compared_and_composition_precedes_total_count(monkeypatch):
    from app.species.catalog import list_species

    baseline = [s for s in list_species() if not s.id.endswith("@2026-09-21.1")]
    monkeypatch.setattr(
        "app.planning.composition_selection.list_species", lambda: baseline
    )
    report = run_case(case())
    result = report["content"]["result"]
    pairs = result["pairs"]
    assert len(pairs) == 18
    winner = pairs[result["selected_pair_index"]]
    assert (winner["trees"], winner["shrubs"]) == (4, 4)
    assert winner["tree_species_revision_id"].startswith("sorbus-aucuparia@")
    assert any(p["trees"] + p["shrubs"] > 8 for p in pairs)
    assert result["preview"]["capacity_shortfall"] == 12
    assert all(
        c["status"] == "allowed" and c["rule_trace"]
        for c in result["preview"]["change_set"]["candidate_results"]
    )
    assert sum(c["cache_preview"] for c in report["content"]["validation_calls"]) == 1
    assert report["content_sha256"] == run_case(case())["content_sha256"]


def test_conditions_filter_both_kinds_without_claiming_unknown_is_forbidden():
    source = case()
    raw = source.request.model_dump()
    raw["site_conditions"] = {
        "light": "partial_shade",
        "moisture": "moist",
        "drainage": "well_drained",
        "basis": "Synthetic measured homogeneous conditions",
    }
    source.request = CompositionSelectionRequest.model_validate(raw)
    for zone in source.project.planting_zones:
        zone.site_conditions = source.request.site_conditions
    report = run_case(source)
    assert report["content"]["error"] is None
    result = report["content"]["result"]
    qualified = {
        o["species_revision_id"]
        for o in result["species_options"]
        if o["assortment_status"] == "listed"
        and o["site_suitability"]["status"] == "documented_match"
    }
    assert result["pairs"]
    assert all(
        p["tree_species_revision_id"] in qualified
        and p["shrub_species_revision_id"] in qualified
        for p in result["pairs"]
    )
    assert all(
        not p["tree_species_revision_id"].startswith("sorbus-") for p in result["pairs"]
    )
    rowan = next(
        o
        for o in result["species_options"]
        if o["species_revision_id"].startswith("sorbus-")
    )
    assert rowan["site_suitability"]["status"] == "unknown"


def test_missing_eligible_tier_does_not_silently_substitute():
    source = case()
    values = source.request.model_dump()
    values["site_conditions"] = {
        "light": "full_shade",
        "basis": "Synthetic shady courtyard",
    }
    source.request = CompositionSelectionRequest.model_validate(values)
    for zone in source.project.planting_zones:
        zone.site_conditions = source.request.site_conditions
    report = run_case(source)
    result = report["content"]["result"]
    assert result["pairs"] == [] and result["preview"] is None
    assert report["content"]["generation_calls"] == []
    assert report["content"]["validation_calls"] == []


def test_single_cached_winner_applies_with_existing_version_guard():
    app, project = application()
    app.history = InMemoryProjectHistory()
    app.history_application.history = app.history
    values = case().request.model_dump()
    values["placement"].update(base_plan_version=project.plan.version, target_count=4)
    request = CompositionSelectionRequest.model_validate(values)
    for zone in project.planting_zones:
        zone.territory = request.territory
        zone.site_conditions = request.site_conditions
    project = app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    result = select_composition(project, request, app.patterns)
    assert len(app.changes._previews) == 1
    assert app.get(project.id).model_dump_json() == before
    qualified = {
        kind: [
            o
            for o in result.species_options
            if eligible(o) and get_species(o.species_revision_id).kind == kind
        ]
        for kind in ("tree", "shrub")
    }
    assert len(result.pairs) == len(qualified["tree"]) * len(qualified["shrub"])
    preview = result.preview.change_set
    app.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=preview.id,
            base_plan_version=preview.base_plan_version,
            digest=preview.digest,
        ),
    )
    assert len(app.get(project.id).plan.objects) == 4
    with pytest.raises(PlanVersionConflict):
        select_composition(app.get(project.id), request, app.patterns)


@pytest.mark.parametrize(
    "change",
    [
        {"tree_share": 0},
        {"target_count": 1},
        {"placement_mode": "spacing"},
        {"tree_species_revision_id": "sorbus-aucuparia@2026-08-28.1"},
    ],
)
def test_unsupported_or_ambiguous_request_rejected(change):
    values = case().request.model_dump()
    values["placement"].update(change)
    with pytest.raises(ValidationError):
        CompositionSelectionRequest.model_validate(values)


@pytest.mark.parametrize("objective", ["balanced", "shade", "low_future_conflict"])
def test_profile_tie_break_and_one_snapshot(objective, monkeypatch):
    app, project = application()
    values = case().request.model_dump()
    values["placement"].update(base_plan_version=project.plan.version, target_count=4)
    values["objective"] = objective
    request = CompositionSelectionRequest.model_validate(values)
    for zone in project.planting_zones:
        zone.territory = request.territory
        zone.site_conditions = request.site_conditions
    project = app.repository.save(project)

    def unexpected_read(*args, **kwargs):
        pytest.fail("Pair search must reuse its supplied snapshot")

    monkeypatch.setattr(app.patterns.repository, "get", unexpected_read)
    result = select_composition(project, request, app.patterns)
    winner = result.pairs[result.selected_pair_index]
    assert (winner.trees, winner.shrubs) == (2, 2)
    full_pairs = [p for p in result.pairs if p.trees == p.shrubs == 2]
    if objective == "shade":
        assert winner.crown_projection_sum_m2 == max(
            p.crown_projection_sum_m2 for p in full_pairs
        )
    elif objective == "low_future_conflict":
        assert winner.maximum_crown_radius_m == min(
            p.maximum_crown_radius_m for p in full_pairs
        )
    else:
        assert (
            winner.tree_species_revision_id,
            winner.shrub_species_revision_id,
        ) == min(
            (p.tree_species_revision_id, p.shrub_species_revision_id)
            for p in full_pairs
        )


def test_composition_request_cannot_replace_the_saved_zone_category():
    app, project = application()
    request = case().request
    request.territory.category = "preschool"
    with pytest.raises(ValueError, match="сохранённых условий"):
        select_composition(project, request, app.patterns)
