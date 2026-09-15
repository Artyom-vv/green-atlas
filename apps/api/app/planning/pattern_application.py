from __future__ import annotations

import json
from hashlib import sha256

from app.data_passport import build_data_passport
from app.planning.allocation import equal_zone_targets, spread_indices
from app.planning.change_contracts import PlanChangeSetDraft, PlanObjectAddOperation
from app.planning.domain import PlanVersionConflict
from app.planning.evaluation import PlanEvaluation
from app.planning.pattern_contracts import (
    CandidateReasonSummary,
    FillPatternRequest,
    PatternPreview,
    PatternPreviewRequest,
    PatternSkippedCandidate,
    PatternZoneAllocation,
    PlacementMaskRequest,
)
from app.planning.ports import CandidateGeneratorPort, ChangeSetPreviewPort
from app.planning.results import rejected_category, summarize_skips
from app.planning.rules import (
    compiled_planting_zones,
    default_layout_radius,
    effective_pattern_spacing,
    mask_guide_geometries,
    pattern_growth_radii,
    planting_zone_at,
)
from app.projects.ports import ProjectReader
from app.regulations.network_summary import network_review_reason


class PatternApplication:
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

    def preview_pattern(
        self, project_id: str, request: PatternPreviewRequest
    ) -> PatternPreview:
        project = self.repository.get(project_id)
        if project.plan is None:
            raise ValueError("План ещё не создан")
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)

        passport = build_data_passport(project)
        unverified_data = list(passport.gaps)
        requested_zone_ids = set(request.zone_ids)
        known_zone_ids = {zone.id for zone in project.planting_zones}
        if requested_zone_ids - known_zone_ids:
            raise ValueError("Один из выбранных участков больше не существует")

        requested_target = (
            request.target_count if request.placement_mode == "count" else None
        )
        selected_zone_ids = [
            zone.id for zone in project.planting_zones if zone.id in requested_zone_ids
        ]
        zone_targets = (
            equal_zone_targets(selected_zone_ids, requested_target)
            if (
                requested_target is not None
                and getattr(request, "zone_distribution", "available") == "equal"
            )
            else {}
        )
        # Road-edge placement must be generated outside the mature canopy
        # envelope as well as the statutory carriageway setback. The UI preset
        # starts at a small visual offset, but a species with a wider forecast
        # crown would otherwise produce zero candidates before the final
        # validator ever gets a chance to assess them.
        planning_request = request
        if (
            isinstance(request, PlacementMaskRequest)
            and request.mask_id == "road_edges"
        ):
            growth_radii = pattern_growth_radii(request)
            if growth_radii:
                planning_request = request.model_copy(
                    update={
                        "road_offset_m": min(
                            30,
                            max(
                                request.road_offset_m,
                                growth_radii[0] + request.edge_offset_m + 0.1,
                            ),
                        ),
                    }
                )
        effective_spacing = effective_pattern_spacing(planning_request)
        generation_request = planning_request.model_copy(
            update={"spacing_m": effective_spacing}
        )
        if requested_target is not None and request.type in {"fill", "mask"}:
            # Generate alternatives as well as the requested positions. Hard
            # constraints are project-specific and are applied below; a
            # requested count must not mean merely "number of attempts".
            generation_request = generation_request.model_copy(
                update={
                    "target_count": min(
                        5000, max(requested_target, requested_target * 8)
                    ),
                }
            )
        # A row is sampled along its axis; its generator never consumes safe
        # polygon masks. Per-candidate zone, spacing and growth validation
        # below remains authoritative. Avoid eroding every selected polygon
        # merely to draw a line, especially on a cold, large DXF project.
        generation_zones = (
            [zone for zone in project.planting_zones if zone.id in requested_zone_ids]
            if request.type == "row"
            else self.evaluation.automatic_generation_zones(
                project,
                request.plant_kind,
                request.layout_radius_m,
                requested_zone_ids,
                growth_radii=pattern_growth_radii(request),
            )
        )
        guide_geometries = (
            mask_guide_geometries(project, request)
            if isinstance(request, PlacementMaskRequest)
            else None
        )
        if (
            isinstance(request, PlacementMaskRequest)
            and request.mask_id == "road_edges"
            and not guide_geometries
        ):
            raise ValueError("В DXF нет слоёв, распознанных как дороги")
        if zone_targets:
            # Give each requested quota its own alternatives. Sampling the
            # union first can starve small/distant zones before validation.
            budget = generation_request.target_count
            remaining_extra = budget - sum(zone_targets.values())
            extra_targets = equal_zone_targets(
                [zone_id for zone_id, target in zone_targets.items() if target],
                remaining_extra,
            )
            generated_candidates = []
            for zone in generation_zones:
                target = zone_targets[zone.id]
                if not target:
                    continue
                local = generation_request.model_copy(
                    update={
                        "zone_ids": [zone.id],
                        "target_count": target + extra_targets.get(zone.id, 0),
                    }
                )
                points = (
                    self.candidate_generator.generate(local, [zone], guide_geometries)
                    if isinstance(request, PlacementMaskRequest)
                    else self.candidate_generator.generate(local, [zone])
                )
                generated_candidates.extend(points)
        else:
            generated_candidates = (
                self.candidate_generator.generate(
                    generation_request, generation_zones, guide_geometries
                )
                if isinstance(request, PlacementMaskRequest)
                else self.candidate_generator.generate(
                    generation_request, generation_zones
                )
            )
        layout_radius = request.layout_radius_m or default_layout_radius(
            request.plant_kind
        )
        compiled_zones = compiled_planting_zones(project)
        candidates = []
        skipped: list[PatternSkippedCandidate] = []
        for candidate in generated_candidates:
            candidate_zone = planting_zone_at(
                project, candidate.x, candidate.y, layout_radius, compiled_zones
            )
            if candidate_zone is not None and candidate_zone.id in requested_zone_ids:
                candidates.append(candidate)
                continue
            skipped.append(
                PatternSkippedCandidate(
                    x=candidate.x,
                    y=candidate.y,
                    status="blocked",
                    code="OUTSIDE_SELECTED_ZONE",
                    category="constraint",
                    reason="Позиция находится вне выбранных рабочих участков",
                    rule_id="selected-planting-zone",
                    suggested_action="Выберите другой участок или сократите ось",
                    zone_id=candidate_zone.id if candidate_zone else None,
                )
            )
        pattern_digest = sha256(
            json.dumps(
                request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()
        pattern_id = f"pattern-{pattern_digest[:16]}"
        if isinstance(request, PlacementMaskRequest):
            label = {
                "road_edges": "Аллеи вдоль проездов",
                "regular_grid": "Регулярная сетка",
                "cluster_groves": "Куртины",
                "building_screen": "Группы вдоль зданий",
                "building_contour": "Посадки вдоль контуров зданий",
            }[request.mask_id]
        else:
            label = "Ряд посадок" if request.type == "row" else "Заполнение участков"
        operations: list[PlanObjectAddOperation] = []
        for index, candidate in enumerate(candidates):
            candidate_kind = request.plant_kind
            species_revision_id = getattr(request, "species_revision_id", None)
            if (
                isinstance(request, (FillPatternRequest, PlacementMaskRequest))
                and request.composition == "mixed"
            ):
                sample = (
                    int(
                        sha256(
                            f"{request.seed}:{index}:{candidate.x}:{candidate.y}".encode()
                        ).hexdigest()[:8],
                        16,
                    )
                    / 0xFFFFFFFF
                )
                candidate_kind = "tree" if sample < request.tree_share else "shrub"
                species_revision_id = (
                    request.tree_species_revision_id
                    if candidate_kind == "tree"
                    else request.shrub_species_revision_id
                )
            operations.append(
                PlanObjectAddOperation.model_validate(
                    {
                        "type": "add",
                        "object": {
                            "kind": candidate_kind,
                            "x": candidate.x,
                            "y": candidate.y,
                            "layout_radius_m": request.layout_radius_m,
                            "size_class": request.size_class,
                            "species_revision_id": species_revision_id,
                            "pattern_id": f"{pattern_id}-{candidate.group_key}"
                            if isinstance(request, PlacementMaskRequest)
                            and request.mask_id == "building_screen"
                            and candidate.group_key
                            else pattern_id,
                            "group_ids": [
                                f"{pattern_id}-{candidate.group_key}"
                                if isinstance(request, PlacementMaskRequest)
                                and request.mask_id == "building_screen"
                                and candidate.group_key
                                else pattern_id
                            ],
                            "spacing_policy": request.spacing_policy,
                        },
                    }
                )
            )
        if not operations:
            return PatternPreview(
                pattern_id=pattern_id,
                type=request.type,
                mask_id=request.mask_id
                if isinstance(request, PlacementMaskRequest)
                else None,
                requested_count=requested_target or 0,
                generated_count=len(generated_candidates),
                accepted_count=0,
                rejected_count=min(len(skipped), requested_target or len(skipped)),
                capacity_shortfall=max(0, (requested_target or 0) - len(skipped)),
                effective_spacing_m=effective_spacing,
                zone_allocations=[
                    PatternZoneAllocation.model_validate(
                        {
                            "zone_id": zone_id,
                            "requested_count": zone_targets.get(zone_id),
                            "accepted_count": 0,
                        }
                    )
                    for zone_id in selected_zone_ids
                ],
                skipped=skipped,
                reason_summary=summarize_skips(skipped),
                unverified_data=unverified_data,
                data_confidence=passport.mass_placement_status,
                data_confidence_reasons=list(passport.gaps),
            )

        initial = self.previews.preview_on_snapshot(
            project,
            PlanChangeSetDraft(
                base_plan_version=request.base_plan_version,
                source="pattern",
                label=label,
                operations=[*operations],
            ),
            cache_preview=False,
        )
        allowed_indices: list[int] = []
        network_limitation = network_review_reason(
            item.rule_trace for item in initial.candidate_results
        )
        if network_limitation:
            unverified_data.append(network_limitation)
        allowed_by_zone: dict[str, list[int]] = {
            zone_id: [] for zone_id in selected_zone_ids
        }
        for item in initial.candidate_results:
            # Automatic placement is conservative: unresolved evidence is a
            # reason to skip a candidate, not permission to silently include
            # it in a bulk operation. Manual correction can still accept an
            # explicitly reviewed warning later.
            if item.status == "allowed":
                allowed_indices.append(item.operation_index)
                if item.zone_id in allowed_by_zone:
                    allowed_by_zone[item.zone_id].append(item.operation_index)
                continue
            candidate = candidates[item.operation_index]
            skipped.append(
                PatternSkippedCandidate(
                    x=candidate.x,
                    y=candidate.y,
                    status=item.status,
                    code=item.code,
                    category=rejected_category(item.category),
                    reason=item.reason,
                    rule_id=item.rule_id,
                    source_layer=item.source_layer,
                    source_feature_ids=item.source_feature_ids,
                    actual_distance_m=item.actual_distance_m,
                    required_distance_m=item.required_distance_m,
                    suggested_action=item.suggested_action,
                    zone_id=item.zone_id,
                )
            )
        if zone_targets:
            accepted_indices = [
                index
                for zone_id, target in zone_targets.items()
                for index in spread_indices(allowed_by_zone[zone_id], target)
            ]
        elif requested_target is not None:
            accepted_indices = spread_indices(allowed_indices, requested_target)
        else:
            accepted_indices = allowed_indices
        if (
            isinstance(request, PlacementMaskRequest)
            and request.mask_id == "building_screen"
        ):
            # Never label an isolated surviving tree as a group. Preserve
            # complete accepted groves when enforcing an optional user cap.
            groves: dict[str, list[int]] = {}
            for index in allowed_indices:
                groves.setdefault(
                    operations[index].object.pattern_id or pattern_id, []
                ).append(index)
            accepted_indices = []
            for indices in groves.values():
                if len(indices) >= 2 and (
                    requested_target is None
                    or len(accepted_indices) + len(indices) <= requested_target
                ):
                    accepted_indices.extend(indices)
        accepted_operations = [operations[index] for index in accepted_indices]
        change_set = None
        if accepted_operations:
            change_set = self.previews.preview_on_snapshot(
                project,
                PlanChangeSetDraft(
                    base_plan_version=request.base_plan_version,
                    source="pattern",
                    label=f"{label}: {len(accepted_operations)}",
                    operations=[*accepted_operations],
                ),
            )
        reason_summary = summarize_skips(skipped)
        if requested_target is not None and len(candidates) < requested_target:
            reason_summary.append(
                CandidateReasonSummary.model_validate(
                    {
                        "status": "blocked",
                        "code": "SAFE_CAPACITY_REACHED",
                        "category": "constraint",
                        "count": requested_target - len(candidates),
                        "message": "В проверенной части участка больше безопасных позиций не найдено",
                    }
                )
            )
        requested_total = (
            requested_target if requested_target is not None else len(candidates)
        )
        rejected_count = min(
            len(skipped), max(0, requested_total - len(accepted_operations))
        )
        capacity_shortfall = max(
            0, requested_total - len(accepted_operations) - rejected_count
        )
        accepted_index_set = set(accepted_indices)
        return PatternPreview(
            pattern_id=pattern_id,
            type=request.type,
            mask_id=request.mask_id
            if isinstance(request, PlacementMaskRequest)
            else None,
            requested_count=requested_total,
            generated_count=len(generated_candidates),
            accepted_count=len(accepted_operations),
            rejected_count=rejected_count,
            capacity_shortfall=capacity_shortfall,
            effective_spacing_m=effective_spacing,
            zone_allocations=[
                PatternZoneAllocation.model_validate(
                    {
                        "zone_id": zone_id,
                        "requested_count": zone_targets.get(zone_id),
                        "accepted_count": sum(
                            index in accepted_index_set
                            for index in allowed_by_zone[zone_id]
                        ),
                    }
                )
                for zone_id in selected_zone_ids
            ],
            skipped=skipped,
            reason_summary=reason_summary,
            unverified_data=unverified_data,
            data_confidence="limited"
            if network_limitation and passport.mass_placement_status == "verified"
            else passport.mass_placement_status,
            data_confidence_reasons=unverified_data,
            change_set=change_set,
        )
