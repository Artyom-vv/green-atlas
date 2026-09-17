"""Bounded two-order search using the existing per-kind placement and validator."""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from hashlib import sha256
from typing import Literal

from app.data_passport import build_data_passport
from app.planning.allocation import equal_zone_targets
from app.planning.change_contracts import PlanChangeSetDraft, PlanObjectAddOperation
from app.planning.contracts import PlanObject, PlanObjectCreate
from app.planning.pattern_contracts import (
    CandidateReasonSummary,
    CompositionKindResult,
    CompositionTrial,
    FillPatternRequest,
    MixedCompositionSummary,
    PatternPreview,
    PatternPreviewRequest,
    PatternZoneAllocation,
    PlacementMaskRequest,
)
from app.planning.ports import ChangeSetPreviewPort
from app.projects.contracts import Project

PlantKind = Literal["tree", "shrub"]
MixedRequest = FillPatternRequest | PlacementMaskRequest
SinglePreview = Callable[[Project, PatternPreviewRequest], PatternPreview]
ORDERS: tuple[tuple[PlantKind, PlantKind], ...] = (
    ("tree", "shrub"),
    ("shrub", "tree"),
)


def composition_targets(total: int, tree_share: float) -> dict[PlantKind, int]:
    """User ratio, rounded half up once for the whole request; never a norm."""
    trees = int(
        (Decimal(total) * Decimal(str(tree_share))).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
    )
    return {"tree": trees, "shrub": total - trees}


@dataclass
class _Trial:
    order: tuple[PlantKind, PlantKind]
    additions: list[PlanObject]
    previews: list[PatternPreview]
    spacing: dict[PlantKind, float] = field(default_factory=dict)

    def count(self, kind: PlantKind) -> int:
        return sum(p.kind == kind for p in self.additions)

    def rank(self, targets: dict[PlantKind, int]) -> tuple[Fraction, int]:
        # Protect representation of both requested layers, then fill capacity.
        return (
            min(Fraction(self.count(k), n) for k, n in targets.items() if n),
            len(self.additions),
        )


def _trial(
    project: Project,
    request: MixedRequest,
    order: tuple[PlantKind, PlantKind],
    targets: dict[PlantKind, int],
    single: SinglePreview,
) -> _Trial:
    working = project.model_copy(deep=True)
    assert working.plan is not None
    result = _Trial(order, [], [])
    zone_ids = [z.id for z in project.planting_zones if z.id in request.zone_ids]
    zone_totals = equal_zone_targets(zone_ids, request.target_count)
    zone_trees = equal_zone_targets(zone_ids, targets["tree"])
    for kind in order:
        # Complementary per-zone quotas preserve both total and kind counts.
        jobs = (
            [
                (
                    [z],
                    zone_trees[z] if kind == "tree" else zone_totals[z] - zone_trees[z],
                )
                for z in zone_ids
            ]
            if request.zone_distribution == "equal"
            else [(zone_ids, targets[kind])]
        )
        for zones, count in jobs:
            if count == 0:
                continue
            values = request.model_dump(mode="json")
            values.update(
                composition="trees" if kind == "tree" else "shrubs",
                plant_kind=kind,
                species_revision_id=request.tree_species_revision_id
                if kind == "tree"
                else request.shrub_species_revision_id,
                target_count=count,
                zone_ids=zones,
                zone_distribution="available",
            )
            component = type(request).model_validate(values)
            preview = single(working, component)
            result.previews.append(preview)
            if preview.effective_spacing_m is not None:
                result.spacing[kind] = preview.effective_spacing_m
            if preview.change_set is not None and preview.change_set.can_apply:
                additions = [
                    p.model_copy(deep=True) for p in preview.change_set.additions
                ]
                working.plan.objects.extend(additions)
                result.additions.extend(additions)
    return result


def preview_mixed_composition(
    project: Project,
    request: MixedRequest,
    single: SinglePreview,
    previews: ChangeSetPreviewPort,
    *,
    cache_preview: bool,
) -> PatternPreview:
    targets = composition_targets(request.target_count, request.tree_share)
    orders = ORDERS if all(targets.values()) else ORDERS[:1]
    trials = []
    for order in orders:
        trial = _trial(project, request, order, targets, single)
        trials.append(trial)
        if len(trial.additions) == request.target_count:
            # Both bounded quotas are met: no later trial can improve the
            # declared objective, so avoid another geometry/validation pass.
            break
    winner = max(trials, key=lambda trial: trial.rank(targets))
    pattern_id = (
        "pattern-"
        + sha256(
            json.dumps(
                request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
            ).encode()
        ).hexdigest()[:16]
    )
    operations = [
        PlanObjectAddOperation(object=PlanObjectCreate.model_validate(p.model_dump()))
        for p in winner.additions
    ]
    # Keep subgroup identity for groves and add a stable composition group.
    for operation in operations:
        operation.object.group_ids = list(
            dict.fromkeys([*operation.object.group_ids, pattern_id])
        )
    change_set = None
    if operations:
        draft = PlanChangeSetDraft(
            base_plan_version=request.base_plan_version,
            source="pattern",
            label="Смешанная посадка: деревья и кустарники",
            operations=[*operations],
        )
        joint = previews.preview_on_snapshot(project, draft, cache_preview=False)
        if not joint.can_apply:
            raise ValueError(
                "Совместная проверка смешанной схемы не пройдена; план не изменён"
            )
        change_set = (
            previews.preview_on_snapshot(project, draft, cache_preview=cache_preview)
            if cache_preview
            else joint
        )
    components = [
        CompositionKindResult(
            kind=kind,
            requested_count=target,
            accepted_count=winner.count(kind),
            shortfall=target - winner.count(kind),
            effective_spacing_m=winner.spacing.get(kind),
        )
        for kind, target in targets.items()
    ]
    gaps = list(
        dict.fromkeys(gap for p in winner.previews for gap in p.unverified_data)
    )
    summaries = [
        s
        for p in winner.previews
        for s in p.reason_summary
        if s.code != "SAFE_CAPACITY_REACHED"
    ]
    for component in components:
        if component.shortfall:
            summaries.append(
                CandidateReasonSummary(
                    status="blocked",
                    code="COMPOSITION_TARGET_SHORTFALL",
                    category="constraint",
                    count=component.shortfall,
                    message=f"{'Деревья' if component.kind == 'tree' else 'Кустарники'}: найдено {component.accepted_count} из {component.requested_count}. Проверенные схемы не достигли цели; другой тип не подставлен вместо недостающего.",
                )
            )
    passport = build_data_passport(project)
    zone_ids = [z.id for z in project.planting_zones if z.id in request.zone_ids]
    zone_totals = (
        equal_zone_targets(zone_ids, request.target_count)
        if request.zone_distribution == "equal"
        else {}
    )
    return PatternPreview(
        pattern_id=pattern_id,
        type=request.type,
        mask_id=request.mask_id if isinstance(request, PlacementMaskRequest) else None,
        requested_count=request.target_count,
        generated_count=sum(p.generated_count for p in winner.previews),
        accepted_count=len(winner.additions),
        capacity_shortfall=request.target_count - len(winner.additions),
        # Legacy consumers show the tree step for a mixed request. The new
        # component summary exposes each actual step separately.
        effective_spacing_m=winner.spacing.get("tree", winner.spacing.get("shrub")),
        zone_allocations=[
            PatternZoneAllocation(
                zone_id=z,
                requested_count=zone_totals.get(z),
                accepted_count=sum(p.planting_zone_id == z for p in winner.additions),
            )
            for z in zone_ids
        ],
        skipped=[s for p in winner.previews for s in p.skipped],
        reason_summary=summaries,
        unverified_data=gaps,
        data_confidence="blocked"
        if any(p.data_confidence == "blocked" for p in winner.previews)
        else "limited"
        if any(p.data_confidence == "limited" for p in winner.previews)
        else passport.mass_placement_status,
        data_confidence_reasons=gaps,
        change_set=change_set,
        composition_summary=MixedCompositionSummary(
            components=components,
            trials=[
                CompositionTrial(
                    order=list(t.order), trees=t.count("tree"), shrubs=t.count("shrub")
                )
                for t in trials
            ],
            selected_order=list(winner.order),
            reason="Проверяется до двух порядков расчёта: приоритет минимальной доле выполнения целей типов, затем общему числу посадок. При выполнении обеих целей поиск прекращается. Доли заданы пользователем; это не норматив и не доказательство глобального оптимума.",
        ),
    )
