"""Mandatory species eligibility for a saved zone, shared by editing and review."""

from app.species.assortment import (
    TerritoryContext,
    assortment_status,
)
from app.species.assortment_inventory import assortment_entry
from app.species.catalog import get_species
from app.species.eligibility_contracts import PlantEligibility
from app.species.site_contracts import SiteConditions
from app.species.site_profiles import site_profile
from app.species.site_suitability import assess_site


def plant_eligibility(
    revision_id: str | None,
    territory: TerritoryContext | None,
    conditions: SiteConditions | None = None,
    *,
    zone_id: str | None = None,
) -> PlantEligibility:
    result = PlantEligibility(
        allowed=False,
        code="ASSORTMENT_CONTEXT_REQUIRED",
        reason="Укажите категорию территории рабочего участка",
        zone_id=zone_id,
    )
    if territory is None:
        return result
    result.category = territory.category
    if not revision_id:
        result.code, result.reason = (
            "ASSORTMENT_SPECIES_REQUIRED",
            "Выберите растение для проверки московского ассортимента",
        )
        return result
    try:
        species = get_species(revision_id)
    except ValueError:
        result.code = "ASSORTMENT_SPECIES_UNKNOWN"
        result.reason = (
            "Расчётный профиль растения не найден; выберите актуальную породу"
        )
        return result
    row = assortment_entry(species.species_id)
    if row:
        result.source_page, result.source_row, result.source_row_id = (
            row.page,
            row.row,
            row.id,
        )
        result.notes = list(row.notes)
    status = assortment_status(species.species_id, territory)
    messages = {
        "not_recommended": (
            "ASSORTMENT_NOT_RECOMMENDED",
            f"{species.common_name}: не рекомендована для этой категории территории в таблице mos.ru",
        ),
        "unreviewed": (
            "ASSORTMENT_UNREVIEWED",
            f"{species.common_name}: строка ассортимента для этой территории не подтверждена",
        ),
        "individual_review": (
            "ASSORTMENT_INDIVIDUAL_REVIEW",
            "Нужна проверка особого режима территории или подтверждение контроля распространения",
        ),
    }
    if status != "listed":
        result.code, result.reason = messages[status]
        return result
    if conditions:
        suitability = assess_site(conditions, site_profile(species.species_id))
        if suitability.status != "documented_match":
            result.code = (
                "SITE_CONDITIONS_CONFLICT"
                if suitability.status == "documented_conflict"
                else "SITE_CONDITIONS_UNREVIEWED"
            )
            result.reason = "; ".join(
                c.reason
                for c in suitability.checks
                if c.status in {"unknown", "documented_conflict"}
            )
            return result
    result.allowed, result.code = True, "ASSORTMENT_INCLUDED"
    result.reason = f"{species.common_name}: включена в ассортимент этой категории; геометрия и условия посадки проверяются отдельно"
    return result
