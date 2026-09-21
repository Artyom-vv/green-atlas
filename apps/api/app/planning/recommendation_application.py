from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from app.planning.change_contracts import PlanChangeSetDraft, PlanObjectAddOperation
from app.planning.domain import PlanVersionConflict
from app.planning.evaluation import PlanEvaluation
from app.planning.pattern_contracts import FillPatternRequest, PatternSkippedCandidate
from app.planning.ports import CandidateGeneratorPort, ChangeSetPreviewPort
from app.planning.recommendation_config import RECOMMENDATION_PROFILES
from app.planning.recommendation_contracts import (
    EffectEstimate,
    EvidenceAssessment,
    RecommendationExplanation,
    RecommendationPreview,
    RecommendationRequest,
    RecommendationSpeciesOption,
)
from app.planning.results import rejected_category
from app.planning.rules import (
    default_layout_radius,
    growth_radii,
)
from app.planning.species_qualification import eligible, qualify_species
from app.planning.species_selection import (
    SELECTION_REASONS,
    crown_projection_sum,
    selection_key,
)
from app.projects.contracts import Project
from app.projects.ports import ProjectReader
from app.species.assortment import (
    ASSORTMENT_REVISION,
)
from app.species.catalog import get_species, list_species
from app.species.site_profiles import site_profile_inventory


class RecommendationApplication:
    def __init__(
        self,
        repository: ProjectReader,
        candidate_generator: CandidateGeneratorPort,
        evaluation: PlanEvaluation,
        previews: ChangeSetPreviewPort,
    ) -> None:
        self.repository = repository
        self.candidate_generator = candidate_generator
        self.evaluation = evaluation
        self.previews = previews

    def preview_recommendation(
        self, project_id: str, request: RecommendationRequest
    ) -> RecommendationPreview:
        """Build one confirmable proposal without pretending missing ecology data exists.

        The recommendation is deliberately a read-only draft. Hard spatial
        constraints use the normalized DXF and the current plan. Species and
        growth envelopes come from the versioned catalogue. Environmental
        effects remain explicitly unknown until the project contains the
        corresponding sunlight, soil and hydrology evidence.
        """
        project = self.repository.get(project_id)
        if project.source_review is not None:
            raise ValueError("Автоматический подбор требует расчёта ограничений")
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)
        requested_zone_ids = set(request.zone_ids)
        known_zone_ids = {zone.id for zone in project.planting_zones}
        if requested_zone_ids - known_zone_ids:
            raise ValueError("Один из выбранных участков больше не существует")

        from app.planning.zone_recommendation import recommend_by_zone

        return recommend_by_zone(project, request, self._select_for_zone, self.previews)

    def _select_for_zone(
        self,
        project: Project,
        request: RecommendationRequest,
    ) -> RecommendationPreview:
        if request.territory is None:
            raise ValueError("Укажите категорию территории рабочего участка")
        preferred = RECOMMENDATION_PROFILES[request.profile]["species"]
        options = []
        best_key = None
        selected_id = None
        for species in sorted(
            list_species(request.effective_plant_kind), key=lambda item: item.id
        ):
            option = RecommendationSpeciesOption.model_validate(
                qualify_species(
                    species.id, request.territory, request.site_conditions
                ).model_dump()
            )
            options.append(option)
            if not eligible(option):
                continue
            trial = self._preview_species(
                project, request, species.id, cache_final=False
            )
            option.accepted_count = (
                len(trial.change_set.additions) if trial.change_set else 0
            )
            option.crown_projection_sum_m2 = crown_projection_sum(trial)
            key = selection_key(
                trial, request.profile, species.id == preferred, species.id
            )
            if best_key is None or key < best_key:
                best_key, selected_id = key, species.id

        if selected_id is None:
            result = RecommendationPreview(
                profile=request.profile,
                evidence=EvidenceAssessment(
                    spatial_constraints="partial",
                    species_catalog="partial",
                    note="Подбор не выполнен: требуется индивидуальная проверка территории или ассортимента.",
                ),
                species_options=options,
                assortment_revision=ASSORTMENT_REVISION,
                selection_reason="Для переданного контекста нет квалифицированного автоматического выбора.",
                data_gaps=["Индивидуальный проект или уточнение категории территории"],
            )
            if request.site_conditions is not None:
                result.evidence.note = "Подбор не выполнен: нет вида с подтверждённым сочетанием категории территории и заданных условий места. Причины сохранены по каждому виду."
                result.data_gaps = [
                    "Проверка ассортимента и условий места по отклонённым вариантам"
                ]
                self._record_site_context(result, request)
            return result
        result = self._preview_species(project, request, selected_id, cache_final=False)
        result.species_options = options
        result.assortment_revision = ASSORTMENT_REVISION
        result.selection_reason = SELECTION_REASONS[request.profile]
        if result.change_set is None:
            result.selection_reason = "Ни один проверенный вид не дал допустимых посадок при текущих параметрах. Это не доказательство невозможности другого проектного решения."
        result.evidence.species_catalog = "partial"
        result.evidence.note += " Проверены строки ассортимента для части каталога; почва, конкретный сорт и примечания таблицы требуют оценки."
        selected_option = next(
            item for item in options if item.species_revision_id == selected_id
        )
        selected_revision = get_species(selected_id)
        if selected_option.site_suitability is not None:
            self._record_site_context(result, request)
            site_reasons = [
                check.reason for check in selected_option.site_suitability.checks
            ]
            for explanation in result.explanations:
                explanation.biological_risks.extend(site_reasons)
                explanation.biological_risks.extend(
                    selected_option.site_suitability.notes
                )
        for explanation in result.explanations:
            explanation.biological_risks.extend(selected_option.notes)
            explanation.biological_risks.append(selected_revision.evidence_note)
        result.data_gaps.extend(selected_option.notes)
        if "root_data_missing" in selected_revision.risk_flags:
            result.data_gaps.append(
                "Корневая архитектура и местная калибровка роста выбранного вида"
            )
        return result

    @staticmethod
    def _record_site_context(
        result: RecommendationPreview, request: RecommendationRequest
    ) -> None:
        context = request.site_conditions
        if context is None:
            return
        result.site_conditions = context.model_copy(deep=True)
        result.site_evidence_revision = site_profile_inventory().revision
        # Observations supplied by a caller are not a verified sunlight or
        # hydrology simulation. Keep effects unknown and remaining gaps visible.
        if context.light is not None:
            result.evidence.sunlight = "partial"
        if context.moisture is not None or context.drainage is not None:
            result.evidence.soil = "partial"
        result.evidence.note += " Сверены только явно заданные условия с опубликованными сведениями о видах; это не местная калибровка и не полная оценка пригодности."
        result.data_gaps.extend(
            [
                "Кислотность, засоление и уплотнение почвы, сорт и уход",
                "Проверка однородности условий во всех выбранных участках",
            ]
        )

    def _preview_species(
        self,
        project: Project,
        request: RecommendationRequest,
        species_revision_id: str,
        *,
        cache_final: bool,
    ) -> RecommendationPreview:
        profile = RECOMMENDATION_PROFILES[request.profile]
        revision = get_species(species_revision_id)
        fill = FillPatternRequest(
            base_plan_version=request.base_plan_version,
            zone_ids=request.zone_ids,
            layout=profile["layout"],
            # Shrub spacing uses the existing catalogue envelope, not a tree
            # preset or a fabricated statutory distance. Exact sites are checked below.
            spacing_m=profile["spacing"]
            if request.effective_plant_kind == "tree"
            else revision.mature_crown_diameter_max_m,
            edge_offset_m=profile["edge"]
            if request.effective_plant_kind == "tree"
            else default_layout_radius(request.effective_plant_kind),
            seed=profile["seed"],
            plant_kind=request.effective_plant_kind,
            size_class="standard",
            species_revision_id=revision.id,
            placement_mode="count",
            target_count=request.max_sites,
        )
        candidates = self.candidate_generator.generate(
            fill,
            self.evaluation.automatic_generation_zones(
                project,
                fill.plant_kind,
                fill.layout_radius_m,
                set(request.zone_ids),
                growth_radii=growth_radii([revision.id], fill.size_class),
            ),
        )
        digest = sha256(
            json.dumps(
                request.model_dump(
                    mode="json",
                    exclude={"site_conditions"}
                    if request.site_conditions is None
                    else set(),
                ),
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
        ).hexdigest()
        recommendation_id = f"recommendation-{digest[:16]}"
        operations = [
            PlanObjectAddOperation.model_validate(
                {
                    "type": "add",
                    "object": {
                        "kind": request.effective_plant_kind,
                        "x": candidate.x,
                        "y": candidate.y,
                        "size_class": "standard",
                        "species_revision_id": revision.id,
                        "pattern_id": recommendation_id,
                        "group_ids": [recommendation_id],
                    },
                }
            )
            for candidate in candidates
        ]

        skipped: list[PatternSkippedCandidate] = []
        accepted_operations: list[PlanObjectAddOperation] = []
        if operations:
            initial = self.previews.preview_on_snapshot(
                project,
                PlanChangeSetDraft(
                    base_plan_version=request.base_plan_version,
                    source="recommendation",
                    label="Проверка предложения",
                    operations=[*operations],
                ),
                cache_preview=False,
            )
            for result in initial.candidate_results:
                candidate = candidates[result.operation_index]
                if (
                    result.status == "allowed"
                    and len(accepted_operations) < request.max_sites
                ):
                    accepted_operations.append(operations[result.operation_index])
                else:
                    reason = (
                        result.reason
                        if result.status != "allowed"
                        else "Не включено из-за заданного лимита предложения"
                    )
                    skipped.append(
                        PatternSkippedCandidate(
                            x=candidate.x,
                            y=candidate.y,
                            status=result.status
                            if result.status != "allowed"
                            else "blocked",
                            code=result.code
                            if result.status != "allowed"
                            else "RECOMMENDATION_LIMIT",
                            category=rejected_category(result.category)
                            if result.status != "allowed"
                            else "operation",
                            reason=reason,
                            rule_id=result.rule_id,
                            source_layer=result.source_layer,
                            source_feature_ids=result.source_feature_ids,
                            actual_distance_m=result.actual_distance_m,
                            required_distance_m=result.required_distance_m,
                            suggested_action=result.suggested_action,
                            zone_id=result.zone_id,
                        )
                    )

        change_set = None
        if accepted_operations:
            change_set = self.previews.preview_on_snapshot(
                project,
                PlanChangeSetDraft(
                    base_plan_version=request.base_plan_version,
                    source="recommendation",
                    label=f"Предложение посадок: {len(accepted_operations)}",
                    operations=[*accepted_operations],
                ),
                cache_preview=cache_final,
            )

        # Layer names alone do not establish completeness of networks or norms.
        spatial_evidence: Literal["partial", "missing"] = (
            "partial" if project.geometry is not None else "missing"
        )
        evidence = EvidenceAssessment(
            spatial_constraints=spatial_evidence,
            species_catalog="verified",
            note="Проверены только распознанные объекты DXF, рабочие области и текущие посадки. Необозначенные сети и условия участка требуют проверки специалистом.",
        )
        effects = [
            EffectEstimate(
                effect="shade",
                status="unknown",
                reason="Нет инсоляции и модели затенения участка",
            ),
            EffectEstimate(
                effect="continuity",
                status="unknown",
                reason="Нет целевой схемы зелёного каркаса и связности",
            ),
            EffectEstimate(
                effect="stormwater",
                status="unknown",
                reason="Нет данных о почве, рельефе и водоотводе",
            ),
            EffectEstimate(
                effect="comfort",
                status="unknown",
                reason="Нет сценариев использования территории и потоков людей",
            ),
        ]
        explanations = [
            RecommendationExplanation(
                object_id=object_.id,
                rank=index,
                hard_constraints=[
                    "Внутри выбранной рабочей области",
                    "Не пересекает распознанные запретные зоны DXF",
                    "Соблюдает шаг относительно текущих и закреплённых посадок",
                    "Проверяет прогноз кроны и корней на 20 лет",
                ],
                effects=[item.model_copy(deep=True) for item in effects],
            )
            for index, object_ in enumerate(
                change_set.additions if change_set else [], start=1
            )
        ]
        return RecommendationPreview(
            profile=request.profile,
            evidence=evidence,
            change_set=change_set,
            explanations=explanations,
            skipped=skipped,
            data_gaps=[
                "Инсоляция и тени",
                "Почва и влажность",
                "Рельеф и водоотвод",
                "Подтверждённые инженерные сети",
            ],
        )
