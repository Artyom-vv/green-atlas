"""Packaged ecological evidence; no online lookup during recommendation."""

from functools import lru_cache
from pathlib import Path

from app.species.site_contracts import SiteProfileInventory, SiteSpeciesProfile


@lru_cache(maxsize=1)
def _inventory() -> SiteProfileInventory:
    return SiteProfileInventory.model_validate_json(
        Path(__file__).with_name("data").joinpath("site-profiles.json").read_bytes()
    )


def site_profile_inventory() -> SiteProfileInventory:
    return _inventory().model_copy(deep=True)


def site_profile(species_id: str) -> SiteSpeciesProfile | None:
    for profile in _inventory().profiles:
        if profile.species_id == species_id:
            return profile.model_copy(deep=True)
    return None
