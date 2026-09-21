import hashlib
import json

import pytest
from test_placement_allocation import application

from app.history.adapters import InMemoryProjectHistory
from app.planning.change_contracts import PlanChangeSetApplyRequest
from app.planning.recommendation_contracts import RecommendationRequest
from app.species.assortment import TerritoryContext, assortment_status
from app.species.catalog import get_species, growth_forecasts, list_species
from app.species.source_profiles import source_profiles


def test_legacy_revisions_are_byte_equivalent_to_previous_catalogue():
    rows = [
        s.model_dump(mode="json")
        for s in list_species()
        if s.id.endswith("@2026-08-28.1")
    ]
    encoded = json.dumps(
        rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    # Captured from dc3abd4 before adding any source profiles.
    assert (
        hashlib.sha256(encoded).hexdigest()
        == "5a0a48a94c4d0b4812435d0840c53e6340b23a5b20c5820040fb65f1c1351875"
    )


@pytest.mark.parametrize(
    "species,height,width",
    [
        ("syringa-vulgaris", (2.4384, 4.8768), (1.8288, 3.6576)),
        ("physocarpus-opulifolius", (1.524, 2.4384), (1.2192, 1.8288)),
        ("larix-decidua", (18.288, 30.48), (6.096, 9.144)),
        ("picea-pungens", (9.144, 18.288), (3.048, 6.096)),
    ],
)
def test_published_dimensions_are_converted_without_inventing_root_architecture(
    species, height, width
):
    revision = get_species(species + "@2026-09-17.1")
    assert (revision.mature_height_min_m, revision.mature_height_max_m) == height
    assert (
        revision.mature_crown_diameter_min_m,
        revision.mature_crown_diameter_max_m,
    ) == width
    assert revision.root_architecture == "uncertain"
    assert "root_data_missing" in revision.risk_flags
    assert "не измерения роста в Москве" in revision.evidence_note
    assert "не доказанный предел корней" in revision.evidence_note
    canopy, roots = growth_forecasts(revision, "standard")
    assert canopy == revision.canopy_forecast and roots == revision.root_forecast
    assert all(f.confidence == "low" for f in [*canopy, *roots])


def test_source_qualifiers_and_municipal_conditions_remain_effective():
    context = TerritoryContext(
        category="major_road", regime="ordinary", basis="Synthetic brief"
    )
    assert assortment_status("larix-decidua", context) == "not_recommended"
    assert assortment_status("physocarpus-opulifolius", context) == "individual_review"
    context.spread_control_confirmed = True
    assert assortment_status("physocarpus-opulifolius", context) == "listed"
    context.regime = "individual_project"
    assert assortment_status("physocarpus-opulifolius", context) == "individual_review"
    assert len(source_profiles()) == 10


def test_lilac_can_be_selected_and_applied_with_explicit_uncertainties(monkeypatch):
    from app.planning import recommendation_application
    from app.species.catalog import list_species

    catalogue = list_species("shrub")
    monkeypatch.setattr(
        recommendation_application,
        "list_species",
        lambda kind: [s for s in catalogue if not s.id.endswith("@2026-09-21.1")],
    )
    app, project = application()
    app.history = InMemoryProjectHistory()
    app.history_application.history = app.history
    req = RecommendationRequest(
        base_plan_version=project.plan.version,
        zone_ids=["west"],
        profile="shade",
        plant_kind="shrub",
        max_sites=5,
        territory=TerritoryContext(
            category="courtyard", regime="ordinary", basis="Synthetic brief"
        ),
    )
    project.planting_zones[0].territory = req.territory
    app.repository.save(project)
    result = app.preview_recommendation(project.id, req)
    assert result.change_set and result.change_set.can_apply
    assert len(result.change_set.additions) == 5
    assert all(
        p.species_revision_id == "syringa-vulgaris@2026-09-17.1"
        for p in result.change_set.additions
    )
    options = {o.species_revision_id.split("@")[0]: o for o in result.species_options}
    assert (
        options["syringa-vulgaris"].crown_projection_sum_m2
        > options["spiraea-japonica"].crown_projection_sum_m2
    )
    assert any("Корневая архитектура" in gap for gap in result.data_gaps)
    assert all(
        any("не доказанный предел корней" in risk for risk in e.biological_risks)
        for e in result.explanations
    )
    assert all(
        effect.status == "unknown" for e in result.explanations for effect in e.effects
    )
    assert len(app.changes._previews) == 1
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
        p.species_revision_id == "syringa-vulgaris@2026-09-17.1"
        for p in app.get(project.id).plan.objects
    )
