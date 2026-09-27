"""Isolated catalogue port from ccd285b, not the branch's new planning policy."""

import hashlib
import json
from collections import Counter

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_placement_allocation import application

from app.api import router
from app.composition import get_application
from app.species.assortment_inventory import assortment_inventory
from app.species.catalog import get_species, growth_forecasts, list_species


def test_existing_ten_revisions_and_forecasts_are_unchanged():
    rows = [s.model_dump(mode="json") for s in list_species() if s.id.endswith("@2026-08-28.1")]
    assert len(rows) == 10
    encoded = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    assert hashlib.sha256(encoded).hexdigest() == "5a0a48a94c4d0b4812435d0840c53e6340b23a5b20c5820040fb65f1c1351875"


def test_reference_inventory_is_not_248_calculation_profiles():
    inventory = assortment_inventory()
    assert Counter(e.kind for e in inventory.entries) == {"tree": 123, "shrub": 113, "vine": 12}
    assert len(list_species()) == 20
    assert sum(e.calculation_species_id is not None for e in inventory.entries) == 19
    assert [e.id for e in inventory.entries if "?" in e.cells] == ["additional-vine-5"]
    assert all(get_species(s.id).id == s.id for s in list_species())


def test_new_dimensions_do_not_claim_measured_roots_or_depth():
    new = [s for s in list_species() if not s.id.endswith("@2026-08-28.1")]
    assert len(new) == 10
    for species in new:
        assert species.root_architecture == "uncertain"
        assert "root_data_missing" in species.risk_flags
        assert "не доказанный предел корней" in species.evidence_note
        canopy, roots = growth_forecasts(species, "standard")
        assert all(row.confidence == "low" for row in [*canopy, *roots])


def test_catalogue_endpoints_are_read_only_and_kind_filtered():
    service, project = application()
    before = service.get(project.id).model_dump_json()
    web = FastAPI()
    web.include_router(router)
    web.dependency_overrides[get_application] = lambda: service
    with TestClient(web) as client:
        response = client.get("/api/species/assortment", params={"kind": "shrub"})
        assert response.status_code == 200
        assert len(response.json()["entries"]) == 113
        response = client.get("/api/species", params={"kind": "shrub"})
        assert response.status_code == 200
        assert len(response.json()) == 8
    assert service.get(project.id).model_dump_json() == before
    with pytest.raises(ValueError):
        assortment_inventory("unknown")
