from collections import Counter

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_placement_allocation import application

from app.api import router
from app.composition import get_application
from app.planning.recommendation_contracts import RecommendationRequest
from app.species.assortment import (
    ASSORTMENT_REVISION,
    ASSORTMENT_SHA256,
    TerritoryContext,
    assortment_status,
)
from app.species.assortment_inventory import assortment_inventory
from app.species.catalog import list_species


def test_complete_source_inventory_preserves_sections_and_missing_cells():
    inventory = assortment_inventory()
    assert inventory.source_sha256 == ASSORTMENT_SHA256
    assert inventory.revision == ASSORTMENT_REVISION
    assert Counter(e.kind for e in inventory.entries) == {
        "tree": 123,
        "shrub": 113,
        "vine": 12,
    }
    assert Counter(e.tier for e in inventory.entries) == {"main": 164, "additional": 84}
    # An empty original row is not filled by inheriting the previous plant.
    missing = [e for e in inventory.entries if "?" in e.cells]
    assert [e.id for e in missing] == ["additional-vine-5"]
    assert not missing[0].matrix_reviewed
    assert not missing[0].calculation_species_id
    for tier in ("main", "additional"):
        for section in {e.section for e in inventory.entries}:
            numbers = [
                e.row
                for e in inventory.entries
                if e.tier == tier and e.section == section
            ]
            assert numbers == list(range(1, len(numbers) + 1))


def test_repeated_source_name_is_not_deduplicated_or_merged():
    matches = [
        e for e in assortment_inventory().entries if e.name == "Жимолость Максимовича"
    ]
    assert len(matches) == 2
    assert {e.cells for e in matches} == {"+--+++++", "+--+-+++"}
    assert all(e.calculation_species_id is None for e in matches)


def test_reference_rows_do_not_become_fabricated_growth_models():
    revisions = list_species()
    mapped = [e for e in assortment_inventory().entries if e.calculation_species_id]
    assert len(revisions) == 14 and len(mapped) == 13
    by_species = {s.species_id: s for s in revisions}
    assert all(by_species[e.calculation_species_id].kind == e.kind for e in mapped)
    assert all(e.matrix_reviewed and e.conditions_reviewed for e in mapped)
    assert (
        assortment_status(
            "ulmus-laevis",
            TerritoryContext(
                category="park", regime="ordinary", basis="Test assignment"
            ),
        )
        == "unreviewed"
    )


def test_inventory_read_does_not_mutate_shared_source():
    result = assortment_inventory("shrub")
    result.entries[0].cells = "????????"
    result.entries.clear()
    assert len(assortment_inventory("shrub").entries) == 113
    assert assortment_inventory("shrub").entries[0].cells != "????????"
    with pytest.raises(ValueError):
        assortment_inventory("grass")


@pytest.mark.parametrize(
    "category,control,status",
    [
        ("preschool", False, "not_recommended"),
        ("preschool", True, "not_recommended"),
        ("courtyard", False, "individual_review"),
        ("courtyard", True, "listed"),
    ],
)
def test_dogwood_source_conditions_are_not_turned_into_an_unconditional_plus(
    category, control, status
):
    context = TerritoryContext(
        category=category,
        regime="ordinary",
        basis="Test brief",
        spread_control_confirmed=control,
    )
    assert assortment_status("cornus-alba", context) == status
    assert assortment_status("spiraea-japonica", context) == "listed"


def test_shrub_recommendation_uses_shrub_geometry_and_excludes_dogwood_at_preschool():
    app, project = application()
    req = RecommendationRequest(
        base_plan_version=project.plan.version,
        zone_ids=["west"],
        max_sites=5,
        plant_kind="shrub",
        profile="balanced",
        territory=TerritoryContext(
            category="preschool", regime="ordinary", basis="Test brief"
        ),
    )
    result = app.preview_recommendation(project.id, req)
    assert result.change_set and result.change_set.can_apply
    assert len(result.change_set.additions) == 5
    assert all(
        p.kind == "shrub" and p.species_revision_id.startswith("spiraea-japonica@")
        for p in result.change_set.additions
    )
    assert len(app.changes._previews) == 1
    assert app.get(project.id).plan.objects == []
    dogwood = next(
        o
        for o in result.species_options
        if o.species_revision_id.startswith("cornus-alba@")
    )
    assert dogwood.assortment_status == "not_recommended"
    assert dogwood.source_page == 10 and dogwood.source_row == 25
    again = app.preview_recommendation(project.id, req)
    assert [(p.x, p.y) for p in result.change_set.additions] == [
        (p.x, p.y) for p in again.change_set.additions
    ]


def test_new_shrub_path_requires_explicit_territory_but_old_requests_stay_valid():
    payload = dict(base_plan_version=1, zone_ids=["west"])
    assert RecommendationRequest(**payload).effective_plant_kind == "tree"
    with pytest.raises(ValueError, match="контекст территории"):
        RecommendationRequest(**payload, plant_kind="shrub")


def test_http_catalog_exposes_reference_rows_without_changing_existing_revision_endpoint():
    service, _ = application()
    web = FastAPI()
    web.include_router(router)
    web.dependency_overrides[get_application] = lambda: service
    client = TestClient(web)
    reference = client.get("/api/species/assortment", params={"kind": "shrub"})
    assert reference.status_code == 200
    assert len(reference.json()["entries"]) == 113
    existing = client.get("/api/species", params={"kind": "shrub"})
    assert existing.status_code == 200
    assert len(existing.json()) == 4
    assert all(s["canopy_forecast"] for s in existing.json())
