"""Automatic capacity and composition reuse the editor's checked pattern pipeline."""

from collections.abc import Callable

from app.planning.change_contracts import (
    PlanChangeOperation,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
)
from app.planning.contracts import PlanObjectCreate
from app.planning.pattern_application import PatternApplication
from app.planning.pattern_contracts import (
    FillPatternRequest,
    PatternPreview,
    PlacementMaskRequest,
)
from app.planning.patterns import MAX_PATTERN_CANDIDATES
from app.planning.ports import ChangeSetPreviewPort
from app.planning.recommendation_config import RECOMMENDATION_PROFILES
from app.planning.recommendation_contracts import (
    RecommendationPreview,
    RecommendationRequest,
)
from app.projects.contracts import Project
from app.species.catalog import get_species


def automatic_species_preview(
    project: Project,
    request: RecommendationRequest,
    species_revision_id: str,
    patterns: PatternApplication,
) -> PatternPreview:
    species = get_species(species_revision_id)
    profile = RECOMMENDATION_PROFILES[request.profile]
    values = dict(
        base_plan_version=request.base_plan_version,
        zone_ids=request.zone_ids,
        plant_kind=species.kind,
        species_revision_id=species.id,
        size_class="standard",
        placement_mode="spacing",
        seed=profile["seed"],
    )
    # PatternApplication derives spacing and the road offset from the same
    # growth envelopes and rules used by manual placement, then checks every site.
    pattern = (
        PlacementMaskRequest(mask_id="road_edges", **values)
        if request.arrangement == "road_edges"
        else FillPatternRequest(layout=profile["layout"], **values)
    )
    result = patterns.preview_on_snapshot(project, pattern, cache_preview=False)
    if result.generated_count >= MAX_PATTERN_CANDIDATES:
        raise ValueError(
            "Автоподбор достиг предела числа кандидатов. Выберите меньший участок; частичный результат не сохранён."
        )
    return result


def automatic_composition(
    project: Project,
    request: RecommendationRequest,
    choose: Callable[[Project, RecommendationRequest], RecommendationPreview],
    previews: ChangeSetPreviewPort,
) -> RecommendationPreview:
    """Try trees first, then fill remaining safe places with qualified shrubs."""
    working = project.model_copy(deep=True)
    assert working.plan is not None
    kinds = [request.plant_kind] if request.plant_kind else ["tree", "shrub"]
    result: RecommendationPreview | None = None
    operations: list[PlanChangeOperation] = []
    for kind in kinds:
        trial = choose(working, request.model_copy(update={"plant_kind": kind}))
        additions = (
            trial.change_set.additions
            if trial.change_set and trial.change_set.can_apply
            else []
        )
        if result is None:
            result = trial.model_copy(deep=True)
        else:
            if not operations and additions:
                result.evidence = trial.evidence.model_copy(deep=True)
                result.site_conditions = trial.site_conditions
            result.species_options.extend(trial.species_options)
            result.explanations.extend(trial.explanations)
            result.skipped.extend(trial.skipped)
            result.data_gaps.extend(trial.data_gaps)
        working.plan.objects.extend(p.model_copy(deep=True) for p in additions)
        operations.extend(
            PlanObjectAddOperation(
                object=PlanObjectCreate.model_validate(p.model_dump())
            )
            for p in additions
        )
    assert result is not None
    result.change_set = (
        previews.preview_on_snapshot(
            project,
            PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="recommendation",
                label="Автоподбор состава",
                operations=operations,
            ),
            cache_preview=False,
        )
        if operations
        else None
    )
    if result.change_set and not result.change_set.can_apply:
        raise ValueError("Совместная проверка деревьев и кустарников не пройдена")
    result.arrangement = request.arrangement
    priority_reason = {
        "shade": "Выбор по площади прогнозных крон; фактическая тень не рассчитана.",
        "low_future_conflict": "Выбор по меньшей прогнозной кроне.",
    }.get(request.profile, "Выбраны виды с большим числом допустимых мест.")
    result.selection_reason = (
        (
            "Сначала деревья, затем кустарники в оставшихся местах. "
            if request.plant_kind is None
            else ""
        )
        + ("Вдоль распознанных дорог. " if request.arrangement == "road_edges" else "")
        + priority_reason
    )
    if not operations:
        result.selection_reason = "Для выбранных участков и схемы допустимых мест не найдено. Причины сохранены по проверенным видам."
    result.data_gaps = list(dict.fromkeys(result.data_gaps))
    return result
