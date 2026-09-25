from __future__ import annotations

import json
from hashlib import sha256

from shapely.geometry import Point, shape
from shapely.ops import unary_union

from app.planning.change_contracts import (
    PlanChangeOperation,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
    PlanObjectDeleteOperation,
)
from app.planning.domain import PlanVersionConflict
from app.planning.evaluation import PlanEvaluation, accepted_partial_source_warning
from app.planning.pattern_contracts import (
    BrushPreview,
    BrushPreviewRequest,
    PatternSkippedCandidate,
)
from app.planning.ports import CandidateGeneratorPort, ChangeSetPreviewPort
from app.planning.results import rejected_category, summarize_skips
from app.planning.rules import (
    growth_radii,
)
from app.projects.ports import ProjectReader
from app.species.catalog import get_species


class BrushApplication:
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

    def preview_brush(
        self, project_id: str, request: BrushPreviewRequest
    ) -> BrushPreview:
        project = self.repository.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)

        request_digest = sha256(
            json.dumps(
                request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()
        brush_id = f"brush-{request_digest[:16]}"
        known_zone_ids = {zone.id for zone in project.planting_zones}
        if set(request.zone_ids) - known_zone_ids:
            raise ValueError("Один из выбранных участков больше не существует")
        selected_zone_geometry = unary_union(
            [
                shape(zone.geometry)
                for zone in project.planting_zones
                if zone.id in set(request.zone_ids)
            ]
        )
        subtract_corridors = []
        for stroke in request.strokes:
            geometry = shape(stroke.geometry)
            if (
                geometry.geom_type != "LineString"
                or geometry.is_empty
                or len(geometry.coords) < 2
            ):
                raise ValueError("Мазок должен быть линией минимум из двух точек")
            if stroke.mode == "subtract":
                subtract_corridors.append(
                    geometry.buffer(
                        request.width_m / 2, cap_style="round", join_style="round"
                    )
                )

        skipped: list[PatternSkippedCandidate] = []
        operations: list[PlanChangeOperation] = []
        operation_points: list[tuple[float, float]] = []
        removal_candidates = []
        if subtract_corridors:
            subtract_geometry = unary_union(subtract_corridors).intersection(
                selected_zone_geometry
            )
            removal_candidates = [
                object_
                for object_ in project.plan.objects
                if subtract_geometry.covers(Point(object_.x, object_.y))
            ]
            for object_ in removal_candidates:
                if object_.locked:
                    skipped.append(
                        PatternSkippedCandidate(
                            x=object_.x,
                            y=object_.y,
                            status="blocked",
                            code="LOCKED_OBJECT",
                            category="operation",
                            reason="Закреплённая посадка не удалена кистью",
                            suggested_action="Снять закрепление после проверки объекта",
                            zone_id=object_.planting_zone_id,
                        )
                    )
                    continue
                operations.append(
                    PlanObjectDeleteOperation.model_validate(
                        {"type": "delete", "object_id": object_.id}
                    )
                )
                operation_points.append((object_.x, object_.y))
        brush_kind = "shrub" if request.composition == "shrubs" else "tree"
        for expected_kind, revision_id in (
            ("tree", request.tree_species_revision_id),
            ("shrub", request.shrub_species_revision_id),
        ):
            if revision_id and get_species(revision_id).kind != expected_kind:
                raise ValueError("Порода не соответствует составу кисти")
        candidates = self.candidate_generator.generate(
            request,
            self.evaluation.automatic_generation_zones(
                project,
                brush_kind,
                None,
                set(request.zone_ids),
                growth_radii=growth_radii(
                    [
                        request.tree_species_revision_id,
                        request.shrub_species_revision_id,
                    ],
                    request.size_class,
                ),
            ),
        )
        for candidate in candidates:
            kind = candidate.kind or "tree"
            operations.append(
                PlanObjectAddOperation.model_validate(
                    {
                        "type": "add",
                        "object": {
                            "kind": kind,
                            "x": candidate.x,
                            "y": candidate.y,
                            "size_class": request.size_class
                            if (
                                request.tree_species_revision_id
                                if kind == "tree"
                                else request.shrub_species_revision_id
                            )
                            else "unspecified",
                            "species_revision_id": request.tree_species_revision_id
                            if kind == "tree"
                            else request.shrub_species_revision_id,
                            "pattern_id": brush_id,
                            "group_ids": [brush_id],
                        },
                    }
                )
            )
            operation_points.append((candidate.x, candidate.y))

        if not operations:
            return BrushPreview(
                brush_id=brush_id,
                requested_count=len(candidates) + len(removal_candidates),
                accepted_count=0,
                added_count=0,
                removed_count=0,
                skipped=skipped,
                reason_summary=summarize_skips(skipped),
            )

        initial = self.previews.preview_on_snapshot(
            project,
            PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="brush",
                label="Проверка мазка",
                operations=[*operations],
            ),
            cache_preview=False,
        )
        accepted_operations: list[PlanChangeOperation] = []
        accepted_additions = 0
        accepted_removals = 0
        for result in initial.candidate_results:
            if result.status != "allowed" and not accepted_partial_source_warning(
                project, status=result.status, code=result.code
            ):
                x, y = operation_points[result.operation_index]
                skipped.append(
                    PatternSkippedCandidate(
                        x=x,
                        y=y,
                        status=result.status,
                        code=result.code,
                        category=rejected_category(result.category),
                        reason=result.reason,
                        rule_id=result.rule_id,
                        source_layer=result.source_layer,
                        source_feature_ids=result.source_feature_ids,
                        actual_distance_m=result.actual_distance_m,
                        required_distance_m=result.required_distance_m,
                        suggested_action=result.suggested_action,
                        zone_id=result.zone_id,
                    )
                )
                continue
            operation = operations[result.operation_index]
            accepted_operations.append(operation)
            if operation.type == "add":
                accepted_additions += 1
            else:
                accepted_removals += 1

        change_set = None
        if accepted_operations:
            change_set = self.previews.preview_on_snapshot(
                project,
                PlanChangeSetDraft(
                    base_plan_version=request.base_plan_version,
                    source="brush",
                    label=f"Кисть: +{accepted_additions}, −{accepted_removals}",
                    operations=[*accepted_operations],
                ),
            )
        return BrushPreview(
            brush_id=brush_id,
            requested_count=len(candidates) + len(removal_candidates),
            accepted_count=len(accepted_operations),
            added_count=accepted_additions,
            removed_count=accepted_removals,
            skipped=skipped,
            reason_summary=summarize_skips(skipped),
            change_set=change_set,
        )
