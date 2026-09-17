"""Match only supplied observations; absence in a source is not intolerance."""

from app.species.site_contracts import (
    SiteCheckStatus,
    SiteConditionCheck,
    SiteConditions,
    SiteDimension,
    SiteSpeciesProfile,
    SiteSuitability,
)

SITE_DIMENSIONS: tuple[SiteDimension, ...] = ("light", "moisture", "drainage")
DIMENSION_LABELS = {
    "light": "Освещённость",
    "moisture": "Влажность почвы",
    "drainage": "Дренирование почвы",
}


def assess_site(
    context: SiteConditions, profile: SiteSpeciesProfile | None
) -> SiteSuitability:
    checks = []
    status: SiteCheckStatus
    for dimension in SITE_DIMENSIONS:
        value = getattr(context, dimension)
        label = DIMENSION_LABELS[dimension]
        if value is None:
            status = "not_provided"
            reason = f"{label}: данные участка не заданы."
        elif profile is None:
            status = "unknown"
            reason = f"{label}: сведения для этого вида ещё не проверены."
        elif value in getattr(profile, f"avoid_{dimension}"):
            status = "documented_conflict"
            reason = f"{label}: источник явно описывает неблагоприятность заданного условия для вида."
        elif value in getattr(profile, dimension):
            status = "documented_match"
            reason = f"{label}: заданное условие указано в проверенном источнике."
        else:
            status = "unknown"
            reason = f"{label}: заданное условие не подтверждено проверенным источником; это не доказательство непереносимости."
        checks.append(
            SiteConditionCheck(
                dimension=dimension, value=value, status=status, reason=reason
            )
        )
    statuses = {check.status for check in checks}
    return SiteSuitability(
        status="documented_conflict"
        if "documented_conflict" in statuses
        else "unknown"
        if "unknown" in statuses
        else "documented_match",
        checks=checks,
        source_url=profile.source_url if profile else None,
        checked_on=profile.checked_on if profile else None,
        notes=list(profile.notes) if profile else [],
    )
