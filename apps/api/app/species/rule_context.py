"""Catalog evidence used by a rule, separate from editable symbol dimensions."""

from app.species.catalog import get_species


def mature_crown_diameter(revision_id: str | None) -> float | None:
    if not revision_id:
        return None
    try:
        return get_species(revision_id).mature_crown_diameter_max_m
    except (KeyError, ValueError):
        return None
