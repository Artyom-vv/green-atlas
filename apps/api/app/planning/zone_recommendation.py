"""Choose species independently for saved zone contexts, publish one proposal."""

from collections.abc import Callable

from app.planning.allocation import equal_zone_targets
from app.planning.change_contracts import (
    PlanChangeOperation,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
)
from app.planning.contracts import PlanObjectCreate
from app.planning.ports import ChangeSetPreviewPort
from app.planning.recommendation_contracts import (
    EvidenceAssessment,
    RecommendationPreview,
    RecommendationRequest,
    ZoneRecommendationSummary,
)
from app.projects.contracts import Project
from app.species.assortment import ASSORTMENT_REVISION
from app.species.site_profiles import site_profile_inventory


def recommend_by_zone(
    project: Project,
    request: RecommendationRequest,
    choose: Callable[[Project, RecommendationRequest], RecommendationPreview],
    previews: ChangeSetPreviewPort,
) -> RecommendationPreview:
    if project.plan is None:
        raise ValueError("План ещё не создан")
    zones = [z for z in project.planting_zones if z.id in request.zone_ids]
    for zone in zones:
        if zone.territory is None:
            raise ValueError(f"Участок «{zone.label}»: укажите категорию территории")
        if request.territory is not None and request.territory != zone.territory:
            raise ValueError(
                "Категория запроса отличается от сохранённых условий участка"
            )
        if (
            request.site_conditions is not None
            and request.site_conditions != zone.site_conditions
        ):
            raise ValueError(
                "Условия запроса отличаются от сохранённых условий участка"
            )
    working = project.model_copy(deep=True)
    assert working.plan is not None
    automatic = request.selection_mode == "automatic"
    quotas = (
        {}
        if automatic
        else equal_zone_targets([z.id for z in zones], request.max_sites)
    )
    result = RecommendationPreview(
        profile=request.profile,
        arrangement=request.arrangement,
        evidence=EvidenceAssessment(
            spatial_constraints="partial",
            species_catalog="partial",
            note="Каждый участок проверен по сохранённой категории и известным условиям; экологические эффекты не рассчитаны.",
        ),
        assortment_revision=ASSORTMENT_REVISION,
        site_evidence_revision=site_profile_inventory().revision,
        selection_reason=(
            "Состав и количество подобраны по свободным местам каждого участка."
            if automatic
            else "Виды подобраны отдельно для каждого участка. Общий лимит поровну распределён между участками; недобор не переносится скрыто в другую зону."
        ),
    )
    operations: list[PlanChangeOperation] = []
    for zone in zones:
        assert zone.territory is not None
        quota = quotas.get(zone.id)
        if quota == 0:
            result.zone_results.append(
                ZoneRecommendationSummary(
                    zone_id=zone.id,
                    requested_count=0,
                    accepted_count=0,
                    territory=zone.territory,
                    site_conditions=zone.site_conditions,
                    selection_reason="Лимит запроса исчерпан распределением между участками",
                )
            )
            continue
        local = request.model_copy(
            update={
                "zone_ids": [zone.id],
                "max_sites": quota if quota is not None else request.max_sites,
                "territory": zone.territory,
                "site_conditions": zone.site_conditions,
            }
        )
        trial = choose(working, local)
        additions = (
            trial.change_set.additions
            if trial.change_set and trial.change_set.can_apply
            else []
        )
        result.zone_results.append(
            ZoneRecommendationSummary(
                zone_id=zone.id,
                requested_count=quota,
                accepted_count=len(additions),
                territory=zone.territory,
                site_conditions=zone.site_conditions,
                species_options=trial.species_options,
                selection_reason=trial.selection_reason,
            )
        )
        if len(zones) == 1:
            result.species_options = trial.species_options
            result.selection_reason = trial.selection_reason
            result.site_conditions = trial.site_conditions
            result.evidence = trial.evidence
        result.skipped.extend(trial.skipped)
        result.data_gaps.extend(trial.data_gaps)
        if additions:
            working.plan.objects.extend(p.model_copy(deep=True) for p in additions)
            operations.extend(
                PlanObjectAddOperation(
                    object=PlanObjectCreate.model_validate(p.model_dump())
                )
                for p in additions
            )
            result.explanations.extend(trial.explanations)
    if operations:
        result.change_set = previews.preview_on_snapshot(
            project,
            PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="recommendation",
                label="Подбор растений по условиям участков",
                operations=operations,
            ),
        )
        if not result.change_set.can_apply:
            raise ValueError(
                "Совместная проверка предложения не пройдена; проект не изменён"
            )
        for index, (explanation, plant) in enumerate(
            zip(result.explanations, result.change_set.additions, strict=True), 1
        ):
            explanation.object_id, explanation.rank = plant.id, index
    result.data_gaps = list(dict.fromkeys(result.data_gaps))
    return result
