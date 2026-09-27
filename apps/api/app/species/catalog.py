from __future__ import annotations

from app.species.contracts import GrowthEnvelopeForecast, SpeciesRevision
from app.species.forecast import forecast_at
from app.species.source_profiles import SOURCE_GROWTH_LABELS, source_profiles


MOSCOW_RULES = "https://www.mos.ru/upload/content/files/49f68586dd69e7d9cc0a0d9a6e933190/Postanovlenieot10_09_2002N743-PPObytverjdeniiPravilsozdaniyasoderjaniyaiohranizelenih_Tekst%281%29.pdf"


def _forecast_anchors(crown: tuple[float, float], growth_rate: str, root_architecture: str, size_class: str = "standard") -> tuple[list[GrowthEnvelopeForecast], list[GrowthEnvelopeForecast]]:
    """Return bounded, nonlinear scenario anchors rather than a false exact size."""
    rates = {"slow": 0.070, "moderate": 0.095, "fast": 0.125}
    size_shift = {"unspecified": 0.0, "sapling": -0.04, "standard": 0.04, "large": 0.10}[size_class]
    root_factor = {"shallow": (0.9, 1.35), "mixed": (0.75, 1.2), "deep": (0.65, 1.05), "uncertain": (0.6, 1.5)}[root_architecture]
    mature_min, mature_max = crown[0] / 2, crown[1] / 2
    canopy: list[GrowthEnvelopeForecast] = []
    roots: list[GrowthEnvelopeForecast] = []
    for year in (0, 5, 10, 15, 20, 30, 40):
        # Bounded saturating curve. The wide range is intentional until a
        # Moscow-calibrated species dataset replaces these catalogue priors.
        ratio = 0.14 if year == 0 else 0.14 + 0.86 * (1 - 2.718281828 ** (-rates[growth_rate] * year))
        scale = min(1.0, max(0.10, ratio + size_shift))
        canopy_min = round(mature_min * max(0.10, scale - 0.08), 2)
        canopy_max = round(mature_max * min(1.0, scale + 0.12), 2)
        confidence = "medium" if year <= 10 and root_architecture != "uncertain" else "low"
        canopy.append(GrowthEnvelopeForecast(horizon_year=year, radius_min_m=canopy_min, radius_max_m=canopy_max, confidence=confidence, basis="Нелинейный диапазон каталога; не нормативный отступ"))
        roots.append(GrowthEnvelopeForecast(horizon_year=year, radius_min_m=round(canopy_min * root_factor[0], 2), radius_max_m=round(canopy_max * root_factor[1], 2), confidence="low", basis="Сценарная корневая зона; требует проверки дендрологом"))
    return canopy, roots


def _species(
    species_id: str,
    common_name: str,
    scientific_name: str,
    kind: str,
    crown_shape: str,
    height: tuple[float, float],
    crown: tuple[float, float],
    growth_rate: str,
    root_architecture: str,
    provenance: str,
    source_url: str,
    *,
    specialist_review: bool = False,
    risk_flags: list[str] | None = None,
    revision_tag: str = "2026-08-28.1",
    evidence_note: str | None = None,
) -> SpeciesRevision:
    canopy, roots = _forecast_anchors(crown, growth_rate, root_architecture)
    return SpeciesRevision(
        id=f"{species_id}@{revision_tag}",
        species_id=species_id,
        revision=1,
        common_name=common_name,
        scientific_name=scientific_name,
        kind=kind,
        crown_shape=crown_shape,
        mature_height_min_m=height[0],
        mature_height_max_m=height[1],
        mature_crown_diameter_min_m=crown[0],
        mature_crown_diameter_max_m=crown[1],
        growth_rate=growth_rate,
        root_architecture=root_architecture,
        provenance=provenance,
        territory_policy="specialist_review" if specialist_review else "general_draft",
        risk_flags=risk_flags or [],
        evidence_note=evidence_note or "Габариты являются диапазоном для эскизной проверки. Фактический сорт, возраст, условия участка и проектное решение уточняет дендролог.",
        source_urls=[source_url, MOSCOW_RULES],
        canopy_forecast=canopy,
        root_forecast=roots,
    )


CATALOG: tuple[SpeciesRevision, ...] = (
    _species("tilia-cordata", "Липа мелколистная", "Tilia cordata", "tree", "spreading", (18, 25), (8, 14), "moderate", "mixed", "native", "https://www.rhs.org.uk/plants/18225/tilia-cordata/details", specialist_review=True, risk_flags=["broad_crown"]),
    _species("acer-platanoides", "Клён остролистный", "Acer platanoides", "tree", "round", (18, 25), (8, 14), "moderate", "shallow", "native", "https://www.rhs.org.uk/plants/274/acer-platanoides/details", specialist_review=True, risk_flags=["broad_crown", "shallow_roots"]),
    _species("quercus-robur", "Дуб черешчатый", "Quercus robur", "tree", "spreading", (20, 40), (12, 24), "slow", "deep", "native", "https://www.rhs.org.uk/plants/14294/quercus-robur/details", specialist_review=True, risk_flags=["broad_crown"]),
    _species("betula-pendula", "Берёза повислая", "Betula pendula", "tree", "conical", (15, 25), (6, 10), "fast", "shallow", "native", "https://www.rhs.org.uk/plants/2261/betula-pendula/details", risk_flags=["shallow_roots"]),
    _species("sorbus-aucuparia", "Рябина обыкновенная", "Sorbus aucuparia", "tree", "oval", (4, 12), (2.5, 6), "moderate", "mixed", "native", "https://www.rhs.org.uk/plants/17539/sorbus-aucuparia/details"),
    _species("ulmus-laevis", "Вяз гладкий", "Ulmus laevis", "tree", "spreading", (20, 30), (10, 18), "fast", "mixed", "native", "https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:856888-1", specialist_review=True, risk_flags=["broad_crown"]),
    _species("pinus-sylvestris", "Сосна обыкновенная", "Pinus sylvestris", "tree", "irregular", (20, 35), (6, 10), "moderate", "deep", "native", "https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:262233-1"),
    _species("picea-abies", "Ель европейская", "Picea abies", "tree", "conical", (20, 35), (6, 10), "moderate", "shallow", "native", "https://powo.science.kew.org/taxon/urn:lsid:ipni.org:names:262700-1", risk_flags=["shallow_roots"]),
    _species("cornus-alba", "Дёрен белый", "Cornus alba", "shrub", "round", (2, 3), (2, 4), "fast", "mixed", "native", "https://www.rhs.org.uk/plants/4380/cornus-alba/details"),
    _species("spiraea-japonica", "Спирея японская", "Spiraea japonica", "shrub", "round", (0.6, 1.5), (0.8, 1.8), "moderate", "shallow", "introduced", "https://www.rhs.org.uk/plants/17696/spiraea-japonica/details"),
)


# New source-backed dimensions reuse the existing sketch scenario, explicitly
# leaving root architecture unknown. Earlier revision IDs and values stay frozen.
CATALOG += tuple(
    _species(
        profile.species_id, profile.common_name, profile.scientific_name,
        profile.kind, profile.crown_shape, profile.height_m, profile.width_m,
        SOURCE_GROWTH_LABELS[profile.growth_label], "uncertain", "not_assessed",
        profile.source_url, specialist_review=True,
        risk_flags=["uncalibrated_growth", "root_data_missing"],
        revision_tag=profile.revision_tag, evidence_note=profile.evidence_note,
    )
    for profile in source_profiles()
)


def list_species(kind: str | None = None) -> list[SpeciesRevision]:
    return [item.model_copy(deep=True) for item in CATALOG if kind is None or item.kind == kind]


def get_species(revision_id: str) -> SpeciesRevision:
    try:
        return next(item.model_copy(deep=True) for item in CATALOG if item.id == revision_id)
    except StopIteration as error:
        raise ValueError("Выбранная ревизия породы не найдена") from error


def growth_forecasts(revision: SpeciesRevision, size_class: str) -> tuple[list[GrowthEnvelopeForecast], list[GrowthEnvelopeForecast]]:
    return _forecast_anchors(
        (revision.mature_crown_diameter_min_m, revision.mature_crown_diameter_max_m),
        revision.growth_rate,
        revision.root_architecture,
        size_class,
    )


__all__ = ["forecast_at", "get_species", "growth_forecasts", "list_species"]
