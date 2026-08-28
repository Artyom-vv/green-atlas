from __future__ import annotations

from app.contracts import GrowthEnvelopeForecast, SpeciesRevision


MOSCOW_RULES = "https://www.mos.ru/upload/content/files/49f68586dd69e7d9cc0a0d9a6e933190/Postanovlenieot10_09_2002N743-PPObytverjdeniiPravilsozdaniyasoderjaniyaiohranizelenih_Tekst%281%29.pdf"


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
) -> SpeciesRevision:
    return SpeciesRevision(
        id=f"{species_id}@2026-08-28.1",
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
        evidence_note="Габариты являются диапазоном для эскизной проверки. Фактический сорт, возраст, условия участка и проектное решение уточняет дендролог.",
        source_urls=[source_url, MOSCOW_RULES],
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


def list_species(kind: str | None = None) -> list[SpeciesRevision]:
    return [item.model_copy(deep=True) for item in CATALOG if kind is None or item.kind == kind]


def get_species(revision_id: str) -> SpeciesRevision:
    try:
        return next(item.model_copy(deep=True) for item in CATALOG if item.id == revision_id)
    except StopIteration as error:
        raise ValueError("Выбранная ревизия породы не найдена") from error


def growth_forecasts(revision: SpeciesRevision, size_class: str) -> tuple[list[GrowthEnvelopeForecast], list[GrowthEnvelopeForecast]]:
    growth = {"slow": (0.25, 0.45, 0.72), "moderate": (0.32, 0.58, 0.84), "fast": (0.42, 0.7, 0.92)}[revision.growth_rate]
    size_bonus = {"unspecified": 0, "sapling": -0.05, "standard": 0.04, "large": 0.1}[size_class]
    mature_min = revision.mature_crown_diameter_min_m / 2
    mature_max = revision.mature_crown_diameter_max_m / 2
    canopy: list[GrowthEnvelopeForecast] = []
    roots: list[GrowthEnvelopeForecast] = []
    root_factor = {"shallow": (0.9, 1.35), "mixed": (0.75, 1.2), "deep": (0.65, 1.05), "uncertain": (0.6, 1.5)}[revision.root_architecture]
    for horizon, ratio in zip((5, 10, 20), growth, strict=True):
        scale = min(1, max(0.15, ratio + size_bonus))
        canopy_min = round(mature_min * scale, 2)
        canopy_max = round(mature_max * min(1, scale + 0.14), 2)
        canopy.append(GrowthEnvelopeForecast(
            horizon_year=horizon,
            radius_min_m=canopy_min,
            radius_max_m=canopy_max,
            confidence="medium" if horizon <= 10 else "low",
            basis=f"Диапазон каталога {revision.id}; не нормативный отступ",
        ))
        roots.append(GrowthEnvelopeForecast(
            horizon_year=horizon,
            radius_min_m=round(canopy_min * root_factor[0], 2),
            radius_max_m=round(canopy_max * root_factor[1], 2),
            confidence="low",
            basis=f"Сценарная корневая зона {revision.id}; требует проверки дендрологом",
        ))
    return canopy, roots
