"""Предпросмотр шаблона по выбранным зонам и версии проекта; принятие кандидатов
выполняет общий оценщик.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from time import monotonic
from typing import cast

from app.data_passport import build_data_passport
from app.dxf_import.evidence_contracts import DataPassport
from app.dxf_import.review.passport_policy import DATA_CLASSES
from app.geometry.ports import PlacementProjectionGeometryPort
from app.operations.progress import check_operation_cancelled
from app.planning.changes.contracts import (
    ChangeSetCandidateResult,
    ChangeSetPreview,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
)
from app.planning.changes.evaluation import (
    PlanEvaluation,
    informational_source_warning,
)
from app.planning.changes.results import rejected_category, summarize_skips
from app.planning.config import (
    MAX_PATTERN_ALTERNATIVE_PROBES,
    PATTERN_ALTERNATIVE_PROBES_PER_PLANT,
)
from app.planning.contracts import PlanObjectCreate
from app.planning.domain import PlanVersionConflict
from app.planning.patterns.contracts import (
    CandidateReasonSummary,
    FillPatternRequest,
    PatternPreview,
    PatternPreviewRequest,
    PatternSkippedCandidate,
    PatternZoneAllocation,
    PlacementMaskRequest,
)
from app.planning.patterns.generators import (
    generate_pattern_candidates,
)
from app.planning.patterns.guide_projection import read_mask_guides
from app.planning.patterns.horizon_witness import HorizonWitnesses
from app.planning.ports import (
    CandidateGeneratorPort,
    ChangeSetPreviewPort,
    PatternCandidate,
)
from app.planning.rules import (
    compiled_planting_zones,
    default_layout_radius,
    effective_pattern_spacing,
    pattern_growth_radii,
    planting_zone_at,
    road_growth_request,
)
from app.planning.scenario import scenario_project
from app.planning.search.allocation import equal_zone_targets, spread_indices
from app.planning.search.candidate_search import (
    CandidateSearchResult,
    search_candidates,
)
from app.planning.search.contracts import SearchDomain, search_domain_details
from app.planning.search.geometric_search import (
    GeometricSearchResult,
    search_geometric_placement,
)
from app.planning.search.horizon_candidates import HorizonCandidateLimit
from app.planning.search.horizon_family import HorizonFamilyTimeout
from app.planning.search.horizon_search import (
    HorizonSearchResult,
    HorizonSearchUnconfirmed,
    search_horizon_placement,
)
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.projects.ports import ProjectReader
from app.projects.read_projection import read_verified_project
from app.regulations.network_summary import network_review_reason
from app.shared.failures import DomainFailure, FailureReason
from app.shared.messages import message


@dataclass(frozen=True)
class _SelectedZones:
    requested_ids: set[str]
    selected_ids: list[str]
    targets: dict[str, int]
    requested_count: int | None


@dataclass(frozen=True)
class _PatternGeneration:
    planning_horizon_year: int
    request: PatternPreviewRequest
    zones: list[PlantingZoneAssignment]
    effective_spacing: float
    free_search: bool
    horizon_family: bool = False
    family_zones: list[PlantingZoneAssignment] | None = None


@dataclass(frozen=True)
class _CandidateCoverage:
    candidates: list[PatternCandidate]
    skipped: list[PatternSkippedCandidate]
    generated_count: int


@dataclass(frozen=True)
class _CandidateSelection:
    accepted_indices: list[int]
    allowed_by_zone: dict[str, list[int]]
    network_limitation: str | None


def _select_zones(project: Project, request: PatternPreviewRequest) -> _SelectedZones:
    """Проверяет выбранные зоны и назначает квоты в порядке зон проекта."""
    requested_zone_ids = set(request.zone_ids)
    known_zone_ids = {zone.id for zone in project.planting_zones}
    if requested_zone_ids - known_zone_ids:
        raise DomainFailure(
            FailureReason.ZONE_MISSING,
            {"zone_ids": sorted(requested_zone_ids - known_zone_ids)},
        )

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
    return _SelectedZones(
        requested_zone_ids, selected_zone_ids, zone_targets, requested_target
    )


def _pattern_id(request: PatternPreviewRequest) -> str:
    pattern_digest = sha256(
        json.dumps(
            request.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        ).encode()
    ).hexdigest()
    return f"pattern-{pattern_digest[:16]}"


def _candidate_coverage(
    project: Project,
    request: PatternPreviewRequest,
    zones: _SelectedZones,
    generated_candidates: list[PatternCandidate],
) -> _CandidateCoverage:
    """Оставляет позиции внутри выбранных зон; порядок задаёт индексы состава."""
    layout_radius = request.layout_radius_m or default_layout_radius(request.plant_kind)
    compiled_zones = compiled_planting_zones(project)
    candidates: list[PatternCandidate] = []
    skipped: list[PatternSkippedCandidate] = []
    for candidate in generated_candidates:
        check_operation_cancelled()
        candidate_zone = planting_zone_at(
            project, candidate.x, candidate.y, layout_radius, compiled_zones
        )
        if candidate_zone is not None and candidate_zone.id in zones.requested_ids:
            candidates.append(candidate)
            continue
        skipped.append(
            PatternSkippedCandidate(
                x=candidate.x,
                y=candidate.y,
                status="blocked",
                code="OUTSIDE_SELECTED_ZONE",
                category="constraint",
                reason=message("pattern.outside_selected_zone"),
                rule_id="selected-planting-zone",
                suggested_action=message("pattern.outside_selected_zone_action"),
                zone_id=candidate_zone.id if candidate_zone else None,
            )
        )
    return _CandidateCoverage(candidates, skipped, len(generated_candidates))


def _pattern_label(request: PatternPreviewRequest) -> str:
    if isinstance(request, PlacementMaskRequest):
        return message(f"pattern.label.{request.mask_id}")
    return message(
        "pattern.label.row" if request.type == "row" else "pattern.label.fill"
    )


def _pattern_operations(
    request: PatternPreviewRequest,
    pattern_id: str,
    candidates: list[PatternCandidate],
    *,
    index_offset: int = 0,
) -> list[PlanObjectAddOperation]:
    """Задаёт вид, сорт и группу по отфильтрованному индексу и исходным координатам."""
    operations: list[PlanObjectAddOperation] = []
    for index, candidate in enumerate(candidates, start=index_offset):
        candidate_kind = request.plant_kind
        species_revision_id = request.species_revision_id
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
        candidate_pattern_id = (
            f"{pattern_id}-{candidate.group_key}"
            if isinstance(request, PlacementMaskRequest)
            and request.mask_id == "building_screen"
            and candidate.group_key
            else pattern_id
        )
        operations.append(
            PlanObjectAddOperation(
                type="add",
                object=PlanObjectCreate(
                    kind=candidate_kind,
                    x=candidate.x,
                    y=candidate.y,
                    layout_radius_m=request.layout_radius_m,
                    size_class=request.size_class,
                    species_revision_id=species_revision_id,
                    pattern_id=candidate_pattern_id,
                    group_ids=[candidate_pattern_id],
                    spacing_policy=request.spacing_policy,
                ),
            )
        )
    return operations


def _select_candidates(
    project: Project,
    request: PatternPreviewRequest,
    pattern_id: str,
    zones: _SelectedZones,
    coverage: _CandidateCoverage,
    operations: list[PlanObjectAddOperation],
    results: list[ChangeSetCandidateResult],
) -> _CandidateSelection:
    """Принимает результаты общего оценщика и выбирает квоты, сохраняя индексы операций."""
    allowed_indices: list[int] = []
    network_limitation = network_review_reason(item.rule_trace for item in results)
    allowed_by_zone: dict[str, list[int]] = {
        zone_id: [] for zone_id in zones.selected_ids
    }
    for item in results:
        if item.status == "allowed" or informational_source_warning(
            project, status=item.status, code=item.code
        ):
            allowed_indices.append(item.operation_index)
            if item.zone_id in allowed_by_zone:
                allowed_by_zone[item.zone_id].append(item.operation_index)
            continue
        candidate = coverage.candidates[item.operation_index]
        coverage.skipped.append(
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
                candidate=operations[item.operation_index].object,
                actual_distance_m=item.actual_distance_m,
                required_distance_m=item.required_distance_m,
                suggested_action=item.suggested_action,
                zone_id=item.zone_id,
            )
        )
    if zones.targets:
        accepted_indices = [
            index
            for zone_id, target in zones.targets.items()
            for index in spread_indices(allowed_by_zone[zone_id], target)
        ]
    elif zones.requested_count is not None:
        accepted_indices = spread_indices(allowed_indices, zones.requested_count)
    else:
        accepted_indices = allowed_indices
    if (
        isinstance(request, PlacementMaskRequest)
        and request.mask_id == "building_screen"
    ):
        # Одиночная оставшаяся посадка не образует группу; лимит принимает
        # только целые группы минимум из двух допущенных кандидатов.
        groves: dict[str, list[int]] = {}
        for index in allowed_indices:
            groves.setdefault(
                operations[index].object.pattern_id or pattern_id, []
            ).append(index)
        accepted_indices = []
        for indices in groves.values():
            if len(indices) >= 2 and (
                zones.requested_count is None
                or len(accepted_indices) + len(indices) <= zones.requested_count
            ):
                accepted_indices.extend(indices)
    return _CandidateSelection(accepted_indices, allowed_by_zone, network_limitation)


def _empty_preview(
    request: PatternPreviewRequest,
    pattern_id: str,
    generation: _PatternGeneration,
    zones: _SelectedZones,
    coverage: _CandidateCoverage,
    domains: list[SearchDomain],
    passport: DataPassport,
) -> PatternPreview:
    """Объясняет отсутствие операций без оценки полной вместимости участка."""
    unverified_data = list(passport.gaps)
    empty_reasons = summarize_skips(coverage.skipped)
    if not coverage.generated_count:
        unresolved = any(domain.unresolved_area_m2 > 0 for domain in domains)
        empty_reasons.append(
            CandidateReasonSummary(
                status="unknown" if unresolved else "blocked",
                code="SEARCH_DOMAIN_UNCONFIRMED"
                if unresolved
                else "SAFE_CAPACITY_REACHED",
                category="data" if unresolved else "constraint",
                count=zones.requested_count or 1,
                message=message("pattern.search_domain_unconfirmed")
                if unresolved
                else message("pattern.no_generated_positions"),
            )
        )
    return PatternPreview(
        planning_horizon_year=generation.planning_horizon_year,
        pattern_id=pattern_id,
        type=request.type,
        mask_id=request.mask_id if isinstance(request, PlacementMaskRequest) else None,
        requested_count=zones.requested_count or 0,
        generated_count=coverage.generated_count,
        search_domains=search_domain_details(generation.zones),
        search_stop_reason="domain_exhausted" if generation.free_search else None,
        accepted_count=0,
        rejected_count=min(
            len(coverage.skipped), zones.requested_count or len(coverage.skipped)
        ),
        capacity_shortfall=max(0, (zones.requested_count or 0) - len(coverage.skipped)),
        effective_spacing_m=generation.effective_spacing,
        zone_allocations=[
            PatternZoneAllocation(
                zone_id=zone_id,
                requested_count=zones.targets.get(zone_id),
                accepted_count=0,
            )
            for zone_id in zones.selected_ids
        ],
        skipped=coverage.skipped,
        reason_summary=empty_reasons,
        unverified_data=unverified_data,
        data_confidence=passport.mass_placement_status,
        data_confidence_reasons=list(passport.gaps),
    )


def _completed_preview(
    request: PatternPreviewRequest,
    pattern_id: str,
    generation: _PatternGeneration,
    zones: _SelectedZones,
    coverage: _CandidateCoverage,
    selection: _CandidateSelection,
    initial: ChangeSetPreview | CandidateSearchResult,
    search_result: CandidateSearchResult | None,
    change_set: ChangeSetPreview | None,
    passport: DataPassport,
) -> PatternPreview:
    """Собирает счётчики, причины и доказательства уже принятого предложения."""
    unverified_data = list(passport.gaps)
    if selection.network_limitation:
        unverified_data.append(selection.network_limitation)
    reason_summary = summarize_skips(coverage.skipped)
    if (
        zones.requested_count is not None
        and len(coverage.candidates) < zones.requested_count
    ):
        reason_summary.append(
            CandidateReasonSummary(
                status="blocked",
                code="SAFE_CAPACITY_REACHED",
                category="constraint",
                count=zones.requested_count - len(coverage.candidates),
                message=message("pattern.generated_capacity_limit"),
            )
        )
    requested_total = (
        zones.requested_count
        if zones.requested_count is not None
        else len(coverage.candidates)
    )
    rejected_count = min(
        len(coverage.skipped),
        max(0, requested_total - len(selection.accepted_indices)),
    )
    capacity_shortfall = max(
        0, requested_total - len(selection.accepted_indices) - rejected_count
    )
    accepted_index_set = set(selection.accepted_indices)
    return PatternPreview(
        planning_horizon_year=generation.planning_horizon_year,
        pattern_id=pattern_id,
        type=request.type,
        mask_id=request.mask_id if isinstance(request, PlacementMaskRequest) else None,
        requested_count=requested_total,
        generated_count=search_result.considered_count
        if search_result is not None and search_result.considered_count is not None
        else (
            len(initial.candidate_results)
            + sum(item.code == "OUTSIDE_SELECTED_ZONE" for item in coverage.skipped)
        )
        if generation.free_search
        else coverage.generated_count,
        search_stop_reason=search_result.stop_reason
        if search_result is not None
        else None,
        search_candidate_limit=None,
        search_full_checked_count=search_result.full_checked_count
        if search_result is not None
        else None,
        search_elapsed_s=search_result.elapsed_s if search_result is not None else None,
        search_domains=search_domain_details(generation.zones),
        accepted_count=len(selection.accepted_indices),
        rejected_count=rejected_count,
        capacity_shortfall=capacity_shortfall,
        effective_spacing_m=generation.effective_spacing,
        zone_allocations=[
            PatternZoneAllocation(
                zone_id=zone_id,
                requested_count=zones.targets.get(zone_id),
                accepted_count=sum(
                    index in accepted_index_set
                    for index in selection.allowed_by_zone[zone_id]
                ),
            )
            for zone_id in zones.selected_ids
        ],
        skipped=coverage.skipped,
        reason_summary=reason_summary,
        unverified_data=unverified_data,
        data_confidence="limited"
        if selection.network_limitation and passport.mass_placement_status == "verified"
        else passport.mass_placement_status,
        data_confidence_reasons=unverified_data,
        change_set=change_set,
    )


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
        self._horizon_witnesses = HorizonWitnesses()

    def preview_pattern(
        self, project_id: str, request: PatternPreviewRequest
    ) -> PatternPreview:
        project = self._checked_project(project_id, request)
        passport = self._data_passport(project)
        zones = _select_zones(project, request)
        generation = self._prepare_generation(project, request, zones)
        domains = search_domain_details(generation.zones)
        # Группа одной семьи одинакова во всех годах. Это сохраняет те же
        # правила расстояний в том числе относительно сохранённого плана.
        pattern_id = _pattern_id(
            request.model_copy(update={"scenario_horizon_year": 0})
            if generation.horizon_family
            else request
        )
        if any(domain.stop_reason != "resolution" for domain in domains):
            # Незавершённая область публикует прогресс без кандидатов и операций.
            return PatternPreview(
                planning_horizon_year=generation.planning_horizon_year,
                pattern_id=pattern_id,
                type=request.type,
                mask_id=request.mask_id
                if isinstance(request, PlacementMaskRequest)
                else None,
                requested_count=zones.requested_count or 0,
                accepted_count=0,
                search_domains=domains,
                effective_spacing_m=generation.effective_spacing,
                unverified_data=list(passport.gaps),
                data_confidence=passport.mass_placement_status,
            )
        label = _pattern_label(request)
        search_result = None
        initial: ChangeSetPreview | CandidateSearchResult
        if generation.free_search:
            assert isinstance(request, FillPatternRequest)

            def factory(
                points: list[PatternCandidate], offset: int
            ) -> list[PlanObjectAddOperation]:
                return _pattern_operations(
                    request, pattern_id, points, index_offset=offset
                )

            geometric: HorizonSearchResult | GeometricSearchResult
            if generation.horizon_family:
                try:
                    geometric = search_horizon_placement(
                        project,
                        request,
                        generation.family_zones or generation.zones,
                        self.evaluation,
                        zones.targets,
                        factory,
                    )
                except HorizonFamilyTimeout as error:
                    raise DomainFailure(FailureReason.PATTERN_SEARCH_TIMEOUT) from error
                except (HorizonSearchUnconfirmed, HorizonCandidateLimit) as error:
                    raise DomainFailure(
                        FailureReason.PATTERN_SEARCH_UNCONFIRMED
                    ) from error
            else:
                geometric = search_geometric_placement(
                    project,
                    request,
                    generation.zones,
                    self.evaluation,
                    generation.effective_spacing,
                    zones.targets,
                    factory,
                )
                if geometric.search.stop_reason == "time_limit":
                    raise DomainFailure(FailureReason.PATTERN_SEARCH_TIMEOUT)
            coverage = _CandidateCoverage(
                geometric.candidates,
                geometric.skipped,
                geometric.search.considered_count or 0,
            )
            operations = geometric.operations
            initial = search_result = geometric.search
        else:
            generated = self._generate_candidates(project, request, generation, zones)
            coverage = _candidate_coverage(project, request, zones, generated)
            operations = _pattern_operations(request, pattern_id, coverage.candidates)
        selection = _CandidateSelection(
            [], {zone_id: [] for zone_id in zones.selected_ids}, None
        )
        if operations:
            if search_result is None:
                initial = self._evaluate_candidates(project, request, label, operations)
            selection = _select_candidates(
                project,
                request,
                pattern_id,
                zones,
                coverage,
                operations,
                initial.candidate_results,
            )
        # Независимый подбор может получить менее плотную раскладку даже при
        # ослаблении ограничений. Прежний результат — только ещё один вариант:
        # заново проверяем координаты, состав, квоты и текущий горизонт.
        previous = self._horizon_witnesses.better_positions(
            project, request, len(selection.accepted_indices)
        )
        if previous:
            recheck_started = monotonic()
            previous_coverage = _candidate_coverage(project, request, zones, previous)
            previous_operations = _pattern_operations(
                request, pattern_id, previous_coverage.candidates
            )
            previous_initial = search_candidates(
                project,
                previous_operations,
                self.evaluation,
                zones.requested_count or len(previous_operations),
                zones.targets,
                generation.effective_spacing,
                plant_kinds=frozenset({request.plant_kind}),
            )
            check_operation_cancelled()
            previous_selection = _select_candidates(
                project,
                request,
                pattern_id,
                zones,
                previous_coverage,
                previous_operations,
                previous_initial.candidate_results,
            )
            if len(previous_selection.accepted_indices) > len(
                selection.accepted_indices
            ):
                coverage, operations, initial, selection = (
                    previous_coverage,
                    previous_operations,
                    previous_initial,
                    previous_selection,
                )
            if search_result is not None:
                # Причины относятся только к выбранной раскладке; метрики
                # учитывают также работу по повторной проверке.
                search_result.elapsed_s += monotonic() - recheck_started
                search_result.considered_count = (
                    search_result.considered_count or 0
                ) + len(previous)
                search_result.full_checked_count += previous_initial.full_checked_count
                if len(selection.accepted_indices) >= (zones.requested_count or 0):
                    search_result.stop_reason = "target_reached"
        if not operations:
            empty = _empty_preview(
                request, pattern_id, generation, zones, coverage, domains, passport
            )
            if search_result is not None:
                empty = empty.model_copy(
                    update={
                        "generated_count": search_result.considered_count or 0,
                        "search_stop_reason": search_result.stop_reason,
                        "search_elapsed_s": search_result.elapsed_s,
                        "search_full_checked_count": search_result.full_checked_count,
                    }
                )
            return empty
        accepted_operations = [
            operations[index] for index in selection.accepted_indices
        ]
        change_set = None
        if accepted_operations:
            check_operation_cancelled()
            change_set = self.previews.preview_on_snapshot(
                project,
                PlanChangeSetDraft(
                    scenario_horizon_year=request.scenario_horizon_year,
                    base_plan_version=request.base_plan_version,
                    source="pattern",
                    label=message(
                        "pattern.accepted_label",
                        {"label": label, "count": len(accepted_operations)},
                    ),
                    operations=[*accepted_operations],
                ),
            )
        completed = _completed_preview(
            request,
            pattern_id,
            generation,
            zones,
            coverage,
            selection,
            initial,
            search_result,
            change_set,
            passport,
        )
        check_operation_cancelled()
        if generation.horizon_family and (
            completed.change_set is not None and not completed.change_set.can_apply
        ):
            raise DomainFailure(FailureReason.PATTERN_SEARCH_UNCONFIRMED)
        self._horizon_witnesses.remember(project, request, completed)
        return completed

    def _checked_project(
        self, project_id: str, request: PatternPreviewRequest
    ) -> Project:
        metadata = self.repository.get(project_id, lightweight=True)
        geometry = self.evaluation.geometry
        supports_projection = getattr(
            type(geometry), "supports_placement_projection", None
        )
        project = (
            read_verified_project(self.repository, project_id, metadata=metadata)
            if metadata.geometry is not None
            and callable(getattr(self.repository, "read_display_counts", None))
            and callable(supports_projection)
            and cast(
                PlacementProjectionGeometryPort, geometry
            ).supports_placement_projection(metadata)
            else self.repository.get(project_id)
        )
        if project.source_review is not None:
            raise DomainFailure(FailureReason.AUTOMATIC_PREPARATION_REQUIRED)
        if project.plan is None:
            raise DomainFailure(FailureReason.PLAN_MISSING)
        if project.plan.version != request.base_plan_version:
            raise PlanVersionConflict(request.base_plan_version, project.plan.version)
        return scenario_project(project, request.scenario_horizon_year)

    def _data_passport(self, project: Project) -> DataPassport:
        """Точные сводки того же снимка без повторной сборки всей карты."""
        counts = None
        read_counts = getattr(self.repository, "read_display_counts", None)
        if (
            project.source_file is not None
            and project.source_file.native_session is not None
            and (project.geometry is not None or project.source_geometry is not None)
            and callable(read_counts)
        ):
            counts = read_counts(
                project.id,
                expected_version=project.state_version,
                geometry_field="geometry"
                if project.geometry is not None
                else "source_geometry",
                kinds=set(DATA_CLASSES),
            )
        return build_data_passport(
            project, geometry=self.evaluation.geometry, display_counts=counts
        )

    def _prepare_generation(
        self, project: Project, request: PatternPreviewRequest, zones: _SelectedZones
    ) -> _PatternGeneration:
        if project.plan is None:
            raise DomainFailure(FailureReason.PLAN_MISSING)
        horizon = project.plan.planning_horizon_year
        planning_request = road_growth_request(request, horizon)
        effective_spacing = effective_pattern_spacing(planning_request, horizon)
        generation_request = planning_request.model_copy(
            update={"spacing_m": effective_spacing}
        )
        horizon_family = (
            isinstance(request, FillPatternRequest)
            and request.layout == "natural"
            and bool(
                request.species_revision_id
                or request.tree_species_revision_id
                or request.shrub_species_revision_id
                or project.plan.objects
            )
        )
        free_search = (
            isinstance(request, FillPatternRequest)
            and request.layout == "natural"
            and (
                horizon_family
                or (
                    request.placement_mode == "count"
                    and zones.requested_count is not None
                )
            )
        )
        if (
            zones.requested_count is not None
            and request.type in {"fill", "mask"}
            and not free_search
        ):
            # Альтернативы дают общему оценщику резерв позиций; требуемое
            # количество не ограничивает число попыток генерации.
            generation_request = generation_request.model_copy(
                update={
                    "target_count": min(
                        MAX_PATTERN_ALTERNATIVE_PROBES,
                        max(
                            zones.requested_count,
                            zones.requested_count
                            * PATTERN_ALTERNATIVE_PROBES_PER_PLANT,
                        ),
                    ),
                }
            )
        # Ряд использует ось без эрозии полигонов. Допуск каждой позиции
        # по зоне, расстояниям и росту всё равно выполняет общий оценщик.
        generation_zones = (
            [zone for zone in project.planting_zones if zone.id in zones.requested_ids]
            if request.type == "row"
            else self.evaluation.automatic_generation_zones(
                project,
                request.plant_kind,
                request.layout_radius_m,
                zones.requested_ids,
                growth_radii=pattern_growth_radii(request, horizon),
            )
        )
        family_zones = None
        if horizon_family:
            family_zones = generation_zones
            if horizon and all(
                domain.stop_reason == "resolution"
                for domain in search_domain_details(generation_zones)
            ):
                family_zones = self.evaluation.automatic_generation_zones(
                    scenario_project(project, 0),
                    request.plant_kind,
                    request.layout_radius_m,
                    zones.requested_ids,
                    growth_radii=pattern_growth_radii(request, 0),
                )
                if any(
                    domain.stop_reason != "resolution"
                    for domain in search_domain_details(family_zones)
                ):
                    # Пока общая база не завершена, публикуем только её прогресс.
                    generation_zones = family_zones
        return _PatternGeneration(
            horizon,
            generation_request,
            generation_zones,
            effective_spacing,
            free_search,
            horizon_family,
            family_zones,
        )

    def _generate_candidates(
        self,
        project: Project,
        request: PatternPreviewRequest,
        generation: _PatternGeneration,
        zones: _SelectedZones,
    ) -> list[PatternCandidate]:
        generation_request = generation.request
        generation_zones = generation.zones
        guide_geometries = (
            read_mask_guides(self.repository, project, request)
            if isinstance(request, PlacementMaskRequest)
            else None
        )
        if (
            isinstance(request, PlacementMaskRequest)
            and request.mask_id == "road_edges"
            and not guide_geometries
        ):
            raise DomainFailure(FailureReason.ROAD_LAYERS_MISSING)
        return generate_pattern_candidates(
            self.candidate_generator,
            generation_request,
            generation_zones,
            zones.targets,
            guide_geometries,
            alternatives=False,
        )

    def _evaluate_candidates(
        self,
        project: Project,
        request: PatternPreviewRequest,
        label: str,
        operations: list[PlanObjectAddOperation],
    ) -> ChangeSetPreview:
        return self.previews.preview_on_snapshot(
            project,
            PlanChangeSetDraft(
                scenario_horizon_year=request.scenario_horizon_year,
                base_plan_version=request.base_plan_version,
                source="pattern",
                label=label,
                operations=[*operations],
            ),
            cache_preview=False,
        )
