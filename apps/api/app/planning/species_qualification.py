"""Shared, source-backed eligibility; unknown evidence never means prohibition."""

from app.planning.recommendation_contracts import QualifiedSpeciesOption
from app.species.assortment import (
    ASSORTMENT_SOURCE,
    TerritoryContext,
    assortment_status,
)
from app.species.assortment_inventory import assortment_entry
from app.species.catalog import get_species
from app.species.site_contracts import SiteConditions
from app.species.site_profiles import site_profile
from app.species.site_suitability import assess_site


def qualify_species(
    revision_id: str, territory: TerritoryContext, conditions: SiteConditions | None
) -> QualifiedSpeciesOption:
    species = get_species(revision_id)
    row = assortment_entry(species.species_id)
    return QualifiedSpeciesOption(
        species_revision_id=species.id,
        assortment_status=assortment_status(species.species_id, territory),
        source_url=ASSORTMENT_SOURCE if row else None,
        source_page=row.page if row else None,
        source_row=row.row if row else None,
        source_row_id=row.id if row else None,
        source_tier=row.tier if row else None,
        notes=list(row.notes) if row else ["Строка этого вида ещё не квалифицирована"],
        site_suitability=assess_site(conditions, site_profile(species.species_id))
        if conditions is not None
        else None,
    )


def eligible(option: QualifiedSpeciesOption) -> bool:
    return option.assortment_status == "listed" and (
        option.site_suitability is None
        or option.site_suitability.status == "documented_match"
    )
