"""Exhaustive catalogue-pair comparison using existing mixed placement.

Exhaustive refers to eligible catalogue pairs, not spatial arrangements or all
botanical species. Each pair uses the existing bounded, two-order search.
"""

from fractions import Fraction
from itertools import product
from math import pi

from app.planning.allocation import composition_targets
from app.planning.composition_selection_contracts import (
    CompositionPairTrial,
    CompositionSelectionRequest,
    CompositionSelectionResult,
)
from app.planning.config import GROWTH_REVIEW_HORIZON_YEAR
from app.planning.domain import PlanVersionConflict
from app.planning.pattern_application import PatternApplication
from app.planning.pattern_contracts import FillPatternRequest, PatternPreview
from app.planning.species_qualification import eligible, qualify_species
from app.projects.contracts import Project
from app.species.assortment import ASSORTMENT_REVISION
from app.species.catalog import get_species, list_species
from app.species.site_profiles import site_profile_inventory


def _pair_request(
    request: CompositionSelectionRequest, tree: str, shrub: str
) -> FillPatternRequest:
    values = request.placement.model_dump()
    values.update(tree_species_revision_id=tree, shrub_species_revision_id=shrub)
    return FillPatternRequest.model_validate(values)


def _measure(
    preview: PatternPreview, tree: str, shrub: str, targets: dict
) -> tuple[CompositionPairTrial, Fraction]:
    plants = preview.change_set.additions if preview.change_set else []
    trees = sum(p.kind == "tree" for p in plants)
    shrubs = sum(p.kind == "shrub" for p in plants)
    fulfillment = min(
        Fraction(trees, targets["tree"]), Fraction(shrubs, targets["shrub"])
    )
    radii = [
        f.radius_max_m
        for p in plants
        for f in p.canopy_forecast
        if f.horizon_year == GROWTH_REVIEW_HORIZON_YEAR
    ]
    return CompositionPairTrial(
        tree_species_revision_id=tree,
        shrub_species_revision_id=shrub,
        trees=trees,
        shrubs=shrubs,
        minimum_quota_fraction=float(fulfillment),
        crown_projection_sum_m2=sum(pi * r**2 for r in radii),
        maximum_crown_radius_m=max(radii, default=0),
    ), fulfillment


def select_composition(
    project: Project,
    request: CompositionSelectionRequest,
    patterns: PatternApplication,
) -> CompositionSelectionResult:
    """Read one snapshot; only the winning proposal receives an apply token."""
    if project.source_review is not None or project.plan is None:
        raise ValueError("Автоматический подбор требует плана и расчёта ограничений")
    if project.plan.version != request.placement.base_plan_version:
        raise PlanVersionConflict(
            request.placement.base_plan_version, project.plan.version
        )
    if set(request.placement.zone_ids) - {z.id for z in project.planting_zones}:
        raise ValueError("Один из выбранных участков больше не существует")

    options = [
        qualify_species(s.id, request.territory, request.site_conditions)
        for s in sorted(list_species(), key=lambda item: item.id)
    ]
    candidates = {
        kind: [
            o.species_revision_id
            for o in options
            if eligible(o) and get_species(o.species_revision_id).kind == kind
        ]
        for kind in ("tree", "shrub")
    }
    result = CompositionSelectionResult(
        species_options=options,
        assortment_revision=ASSORTMENT_REVISION,
        site_evidence_revision=site_profile_inventory().revision,
        selection_reason="Нет квалифицированной пары деревьев и кустарников. Неизвестная пригодность требует проверки, а не означает запрет.",
        data_gaps=[
            "Местная калибровка роста, корневая архитектура, почва, сорт и уход",
            "Тень, инсоляция, водный баланс и биоразнообразие не рассчитаны",
        ],
    )
    if request.site_conditions is None:
        result.data_gaps.append(
            "Условия места не заданы; экологическая пригодность не проверена"
        )
    else:
        result.data_gaps.append(
            "Условия места заданы пользователем; однородность всех участков требует проверки"
        )
    targets = composition_targets(
        request.placement.target_count, request.placement.tree_share
    )
    best_key = None
    for tree, shrub in product(candidates["tree"], candidates["shrub"]):
        preview = patterns.preview_on_snapshot(
            project, _pair_request(request, tree, shrub), cache_preview=False
        )
        trial, fulfillment = _measure(preview, tree, shrub, targets)
        result.pairs.append(trial)
        # Protect both requested tiers, then total count. Profile is a tie-break
        # only: a large crown must not outweigh missing requested plants.
        objective = 0.0
        if request.objective == "shade":
            objective = -trial.crown_projection_sum_m2
        elif request.objective == "low_future_conflict":
            objective = trial.maximum_crown_radius_m
        key = (-fulfillment, -(trial.trees + trial.shrubs), objective, tree, shrub)
        if best_key is None or key < best_key:
            best_key = key
            result.selected_pair_index = len(result.pairs) - 1
    if result.selected_pair_index is None:
        return result
    winner = result.pairs[result.selected_pair_index]
    result.preview = patterns.preview_on_snapshot(
        project,
        _pair_request(
            request, winner.tree_species_revision_id, winner.shrub_species_revision_id
        ),
        cache_preview=True,
    )
    verified, _ = _measure(
        result.preview,
        winner.tree_species_revision_id,
        winner.shrub_species_revision_id,
        targets,
    )
    if verified != winner:
        raise RuntimeError("Winning composition changed during final verification")
    result.selection_reason = (
        "Сравнены все квалифицированные пары текущего каталога: сначала минимальная доля выполнения двух квот, "
        "затем общее число посадок, затем выбранный критерий и стабильный идентификатор. "
        "Для каждой пары проверено до двух порядков расстановки; глобальный пространственный оптимум не доказан."
    )
    if request.objective == "shade":
        result.selection_reason += " Сумма прогнозных площадей крон — приближение, не площадь тени или объединения крон."
    elif request.objective == "low_future_conflict":
        result.selection_reason += " Меньшая максимальная прогнозная крона — приближение, не прогноз всех конфликтов."
    for option in options:
        if option.species_revision_id in (
            winner.tree_species_revision_id,
            winner.shrub_species_revision_id,
        ):
            result.data_gaps.extend(option.notes)
            result.data_gaps.append(
                get_species(option.species_revision_id).evidence_note
            )
    result.data_gaps = list(dict.fromkeys(result.data_gaps))
    return result
