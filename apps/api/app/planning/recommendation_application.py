from __future__ import annotations

import json
from hashlib import sha256
from typing import Literal

from app.planning.change_contracts import PlanChangeSetDraft, PlanObjectAddOperation
from app.planning.domain import PlanVersionConflict
from app.planning.evaluation import PlanEvaluation, accepted_partial_source_warning
from app.planning.pattern_contracts import FillPatternRequest, PatternSkippedCandidate
from app.planning.ports import CandidateGeneratorPort, ChangeSetPreviewPort
from app.planning.recommendation_config import RECOMMENDATION_PROFILES
from app.planning.recommendation_contracts import (
    EffectEstimate,
    EvidenceAssessment,
    RecommendationExplanation,
    RecommendationPreview,
    RecommendationRequest,
)
from app.planning.results import rejected_category
from app.planning.rules import (
    growth_radii,
)
from app.projects.ports import ProjectReader
from app.species.catalog import get_species


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
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)
        requested_zone_ids = set(request.zone_ids)
        known_zone_ids = {zone.id for zone in project.planting_zones}
        if requested_zone_ids - known_zone_ids:
            raise ValueError("Один из выбранных участков больше не существует")

        profile = RECOMMENDATION_PROFILES[request.profile]
        revision = get_species(profile["species"])
        fill = FillPatternRequest(
            base_plan_version=request.base_plan_version,
            zone_ids=request.zone_ids,
            layout=profile["layout"],
            spacing_m=profile["spacing"],
            edge_offset_m=profile["edge"],
            seed=profile["seed"],
            plant_kind="tree",
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
                request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()
        recommendation_id = f"recommendation-{digest[:16]}"
        operations = [
            PlanObjectAddOperation.model_validate(
                {
                    "type": "add",
                    "object": {
                        "kind": "tree",
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
            )
            for result in initial.candidate_results:
                candidate = candidates[result.operation_index]
                if (
                    (
                        result.status == "allowed"
                        or accepted_partial_source_warning(
                            project, status=result.status, code=result.code
                        )
                    )
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
            )

        mapped_physical_kinds = {
            layer.mapped_kind.value
            for layer in project.layers
            if layer.mapped_kind is not None
            and layer.mapped_kind.value not in {"ignore", "other"}
        }
        spatial_evidence: Literal["verified", "partial", "missing"] = (
            "verified"
            if project.geometry is not None
            and {"site_border", "building", "road"} <= mapped_physical_kinds
            else "partial"
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
