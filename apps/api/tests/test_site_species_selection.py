"""Ecological evidence changes proposals without guessing missing conditions."""

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from test_placement_allocation import application

from app.api import get_application, router
from app.history.adapters import InMemoryProjectHistory
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.recommendation_contracts import RecommendationRequest
from app.species.assortment import TerritoryContext
from app.species.catalog import list_species
from app.species.site_contracts import SiteConditions
from app.species.site_profiles import site_profile, site_profile_inventory
from app.species.site_suitability import assess_site
from scripts.planning_lab.contracts import parse_case
from scripts.planning_lab.runner import run_case


def request(project, *, control=True, **conditions):
    return RecommendationRequest(
        base_plan_version=project.plan.version,
        zone_ids=["west"],
        max_sites=5,
        profile="low_future_conflict",
        plant_kind="shrub",
        territory=TerritoryContext(
            category="courtyard",
            regime="ordinary",
            spread_control_confirmed=control,
            basis="Synthetic ordinary courtyard",
        ),
        site_conditions=SiteConditions(basis="Synthetic site observation", **conditions)
        if conditions
        else None,
    )


def selected(result):
    return (
        {p.species_revision_id.split("@")[0] for p in result.change_set.additions}
        if result.change_set
        else set()
    )


def options(result):
    return {o.species_revision_id.split("@")[0]: o for o in result.species_options}


def test_periodically_wet_soil_changes_actual_choice_and_can_apply():
    app, project = application()
    app.history = InMemoryProjectHistory()
    app.history_application.history = app.history
    baseline = app.preview_recommendation(project.id, request(project))
    assert selected(baseline) == {"spiraea-japonica"}
    req = request(project, light="full_sun", moisture="occasionally_wet")
    result = app.preview_recommendation(project.id, req)
    assert selected(result) == {"cornus-alba"}
    assert len(result.change_set.additions) == 5
    assert result.site_conditions == req.site_conditions
    assert result.site_evidence_revision == site_profile_inventory().revision
    assert options(result)["spiraea-japonica"].site_suitability.status == "unknown"
    assert result.evidence.sunlight == result.evidence.soil == "partial"
    assert result.evidence.hydrology == "missing"
    assert all(e.status == "unknown" for x in result.explanations for e in x.effects)
    again = app.preview_recommendation(project.id, req)
    assert [(p.x, p.y, p.species_revision_id) for p in result.change_set.additions] == [
        (p.x, p.y, p.species_revision_id) for p in again.change_set.additions
    ]
    app.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=result.change_set.id,
            digest=result.change_set.digest,
            base_plan_version=project.plan.version,
        ),
    )
    assert len(app.get(project.id).plan.objects) == 5
    assert all(
        p.species_revision_id.startswith("cornus-alba@")
        for p in app.get(project.id).plan.objects
    )


def test_ecological_match_cannot_override_municipal_spread_control():
    app, project = application()
    result = app.preview_recommendation(
        project.id, request(project, control=False, moisture="occasionally_wet")
    )
    assert result.change_set is None
    assert not app.changes._previews
    dogwood = options(result)["cornus-alba"]
    assert dogwood.site_suitability.status == "documented_match"
    assert dogwood.assortment_status == "individual_review"
    assert app.get(project.id).plan.objects == []


def test_explicit_poor_drainage_conflict_does_not_fall_back_to_unknown_species():
    app, project = application()
    result = app.preview_recommendation(
        project.id, request(project, light="full_sun", drainage="poorly_drained")
    )
    assert result.change_set is None
    assert not app.changes._previews
    lilac = options(result)["syringa-vulgaris"].site_suitability
    assert lilac.status == "documented_conflict"
    assert lilac.source_url.endswith("/syringa-vulgaris/")
    assert options(result)["cornus-alba"].site_suitability.status == "unknown"
    assert "Причины сохранены" in result.evidence.note


def test_partial_observation_does_not_claim_whole_site_is_verified():
    app, project = application()
    result = app.preview_recommendation(
        project.id, request(project, light="partial_shade")
    )
    assert result.change_set
    assert result.evidence.sunlight == "partial"
    assert result.evidence.soil == "missing"
    checks = options(result)["spiraea-japonica"].site_suitability.checks
    assert [c.dimension for c in checks if c.status == "not_provided"] == [
        "moisture",
        "drainage",
    ]
    assert result.data_gaps


def test_full_shade_separates_known_conflict_from_unreviewed_and_unlisted():
    context = SiteConditions(light="full_shade", basis="Site observation")
    assert (
        assess_site(context, site_profile("larix-decidua")).status
        == "documented_conflict"
    )
    assert assess_site(context, site_profile("picea-pungens")).status == "unknown"
    assert assess_site(context, site_profile("acer-platanoides")).status == "unknown"


def test_periodic_wetness_is_not_persistent_waterlogging():
    profile = site_profile("cornus-alba")
    assert (
        assess_site(
            SiteConditions(moisture="occasionally_wet", basis="Observed"), profile
        ).status
        == "documented_match"
    )
    assert (
        assess_site(
            SiteConditions(moisture="persistently_wet", basis="Observed"), profile
        ).status
        == "unknown"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"basis": "Observed"},
        {"light": "full_sun", "basis": "   "},
        {"light": "almost_sunny", "basis": "Observed"},
        {"light": "full_sun", "basis": "Observed", "drainange": "poorly_drained"},
    ],
)
def test_invalid_or_misspelled_observations_are_not_silently_ignored(payload):
    with pytest.raises(ValidationError):
        SiteConditions.model_validate(payload)


def test_http_context_is_validated_and_cannot_be_ignored_by_legacy_path():
    service, project = application()
    web = FastAPI()
    web.include_router(router)
    web.dependency_overrides[get_application] = lambda: service
    client = TestClient(web)
    endpoint = f"/api/projects/{project.id}/plan/recommendations/preview"
    payload = request(project, moisture="occasionally_wet").model_dump(mode="json")
    response = client.post(endpoint, json=payload)
    assert response.status_code == 200
    assert response.json()["site_evidence_revision"]
    assert len(response.json()["change_set"]["additions"]) == 5
    payload["territory"] = None
    assert client.post(endpoint, json=payload).status_code == 422


def test_frozen_site_case_is_repeatable_and_keeps_evidence_identity():
    path = (
        Path(__file__).resolve().parents[3]
        / "fixtures/planning-lab/courtyard-shrub-periodically-wet.json"
    )
    case = parse_case(path.read_bytes())
    first, second = run_case(case), run_case(case)
    assert first["content_sha256"] == second["content_sha256"]
    assert first["content"]["basis"][
        "site_species_profiles"
    ] == site_profile_inventory().model_dump(mode="json")
    assert len(first["content"]["generation_calls"]) == 2  # eligible trial + final
    assert len(first["content"]["result"]["change_set"]["additions"]) == 5


def test_profile_access_does_not_mutate_later_recommendations():
    original = site_profile_inventory().model_dump(mode="json")
    site_profile("cornus-alba").moisture.clear()
    site_profile_inventory().profiles.clear()
    assert site_profile_inventory().model_dump(mode="json") == original


def test_every_calculation_species_has_an_identified_ecological_source():
    inventory = site_profile_inventory()
    assert {p.species_id for p in inventory.profiles} == {
        p.species_id for p in list_species()
    }
    assert all(
        p.source_url.startswith("https://") and p.checked_on for p in inventory.profiles
    )


def test_sunny_narrow_zone_can_choose_rowan_with_all_observed_conditions():
    path = (
        Path(__file__).resolve().parents[3]
        / "fixtures/planning-lab/narrow-courtyard-territory.json"
    )
    case = parse_case(path.read_bytes())
    case.request.site_conditions = SiteConditions(
        light="full_sun",
        moisture="moist",
        drainage="well_drained",
        basis="Synthetic homogeneous observation",
    )
    result = run_case(case)["content"]["result"]
    assert len(result["change_set"]["additions"]) == 5
    assert {p["species_revision_id"] for p in result["change_set"]["additions"]} == {
        "sorbus-aucuparia@2026-08-28.1"
    }
    rowan = next(
        o
        for o in result["species_options"]
        if o["species_revision_id"].startswith("sorbus-aucuparia@")
    )
    assert rowan["site_suitability"]["status"] == "documented_match"
    assert rowan["site_suitability"]["source_url"].endswith("/sorbus-aucuparia/")
    case.request.site_conditions.light = "partial_shade"
    shaded = run_case(case)["content"]["result"]
    rowan = next(
        o
        for o in shaded["species_options"]
        if o["species_revision_id"].startswith("sorbus-aucuparia@")
    )
    assert rowan["site_suitability"]["status"] == "unknown"
    assert rowan["accepted_count"] == 0


def test_elm_source_does_not_infer_light_drainage_or_municipal_approval():
    app, project = application()
    req = request(project, moisture="moist")
    req.plant_kind = "tree"
    result = app.preview_recommendation(project.id, req)
    elm = options(result)["ulmus-laevis"]
    assert elm.site_suitability.status == "documented_match"
    assert elm.assortment_status == "unreviewed"
    assert elm.accepted_count == 0
    assert not any(
        p.species_revision_id.startswith("ulmus-laevis@")
        for p in result.change_set.additions
    )
    for conditions in (
        {"light": "full_sun"},
        {"drainage": "well_drained"},
        {"moisture": "persistently_wet"},
    ):
        assert (
            assess_site(
                SiteConditions(basis="Observed", **conditions),
                site_profile("ulmus-laevis"),
            ).status
            == "unknown"
        )
