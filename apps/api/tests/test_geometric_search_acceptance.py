"""Независимые задачи размещения с явным решением и обычным итоговым оценщиком.

Синтетические контуры проверяют полноту нескольких простых запросов, но не
сертифицируют максимальную вместимость или точность исходного CAD.
"""

from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import combinations
from math import hypot
from typing import Literal
from weakref import ReferenceType, ref

import pytest
from shapely.affinity import rotate
from shapely.geometry import LineString, MultiPolygon, Point, Polygon, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from test_placement_allocation import application

from app.application import ProjectApplication
from app.contracts import GeometrySnapshot, PlantingZoneAssignment, Project
from app.geometry.checking.domain import PositionAdvisory
from app.operations.progress import OperationCancelled, operation_cancellation
from app.planning.contracts import PlanObject, PlanObjectCreate
from app.planning.patterns.contracts import FillPatternRequest, PatternPreview
from app.planning.ports import PatternCandidate
from app.planning.rules import planting_zone_at
from app.planning.search import candidate_search, geometric_search
from app.planning.search.candidate_search import CandidateSearchResult
from app.planning.search.geometry_candidates import GeometryCandidateSearch
from app.planning.search.spacing_precheck import utility_assumption_codes
from app.regulations.trace_contracts import PlantingRuleTrace, RuleTraceEntry
from app.shared.failures import DomainFailure, FailureReason


def _application(
    area: BaseGeometry,
    *,
    site: BaseGeometry | None = None,
    nested: BaseGeometry | None = None,
    saved: list[PlanObject] | None = None,
) -> tuple[ProjectApplication, Project]:
    """Создаёт отдельный проект в памяти, без CAD, файлов и рабочего профиля."""
    app, project = application()
    project.planting_zones = [
        PlantingZoneAssignment(id="outer", label="Участок", geometry=mapping(area))
    ]
    if nested is not None:
        project.planting_zones.append(
            PlantingZoneAssignment(
                id="nested", label="Вложенная задача", geometry=mapping(nested)
            )
        )
    if site is None:
        minx, miny, maxx, maxy = area.bounds
        site = box(minx - 10, miny - 10, maxx + 10, maxy + 10)
    project.geometry = GeometrySnapshot(
        feature_collection={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": {"kind": "site_border"},
                    "geometry": mapping(site),
                }
            ],
        }
    )
    assert project.plan is not None
    project.plan.objects = list(saved or [])
    app.repository.save(project)
    return app, project


def _request(target: int, spacing: float = 6) -> FillPatternRequest:
    return FillPatternRequest(
        base_plan_version=1,
        zone_ids=["outer"],
        layout="natural",
        placement_mode="count",
        target_count=target,
        spacing_m=spacing,
        edge_offset_m=0,
        seed=47,
    )


def _assert_feasible_witness(
    app: ProjectApplication,
    project: Project,
    points: Sequence[tuple[float, float]],
    spacing: float,
) -> None:
    """Подтверждает достижимость запроса независимо от алгоритма генерации."""
    assert project.plan is not None
    working = project.plan.model_copy(deep=True)
    for x, y in points:
        candidate, issue = app.evaluation.preview_addition(
            project,
            working,
            PlanObjectCreate(kind="tree", x=x, y=y, group_ids=["witness"]),
        )
        assert issue is None
        assert candidate.planting_zone_id == "outer"
        working.objects.append(candidate)
    assert all(
        hypot(first[0] - second[0], first[1] - second[1]) >= spacing
        for first, second in combinations(points, 2)
    )


def _assert_valid_result(
    app: ProjectApplication,
    project: Project,
    preview: PatternPreview,
    target: int,
    spacing: float,
) -> None:
    assert preview.accepted_count == target
    assert preview.search_stop_reason == "target_reached"
    assert preview.change_set is not None
    assert preview.change_set.can_apply
    additions = preview.change_set.additions
    assert len(additions) == target
    assert project.plan is not None
    working = project.plan.model_copy(deep=True)
    for addition in additions:
        zone = planting_zone_at(project, addition.x, addition.y, addition.radius)
        assert zone is not None and zone.id == "outer"
        payload = PlanObjectCreate.model_validate(
            addition.model_dump(exclude={"id", "status", "planting_zone_id"})
        )
        checked, issue = app.evaluation.preview_addition(project, working, payload)
        assert issue is None
        working.objects.append(checked)
    assert all(
        hypot(first.x - second.x, first.y - second.y) >= spacing
        for first, second in combinations(additions, 2)
    )
    retained = app.get(project.id)
    assert retained.plan is not None
    assert retained.plan.model_dump() == project.plan.model_dump()


@pytest.mark.parametrize("seed", [0, 47, 911])
def test_requested_pair_escapes_a_central_greedy_trap(seed: int) -> None:
    """Центральный первый выбор мешает паре, хотя оба конца проходят проверку."""
    app, project = _application(box(0, 0, 10.4, 3.6))
    witness = [(1.7, 1.8), (8.7, 1.8)]
    _assert_feasible_witness(app, project, witness, 6)
    preview = app.preview_pattern(
        project.id, _request(2).model_copy(update={"seed": seed})
    )
    _assert_valid_result(app, project, preview, 2, 6)


@pytest.mark.parametrize("angle", [0, 31, 90])
def test_long_narrow_corridor_reaches_requested_count(angle: int) -> None:
    area = rotate(box(0, 0, 103.4, 3.4), angle, origin=(0, 0))
    app, project = _application(area)
    witness_points = [
        rotate(Point(2 + 9 * index, 1.7), angle, origin=(0, 0)) for index in range(12)
    ]
    witness = [(point.x, point.y) for point in witness_points]
    _assert_feasible_witness(app, project, witness, 8)
    preview = app.preview_pattern(project.id, _request(12, 8))
    _assert_valid_result(app, project, preview, 12, 8)


def test_distant_small_components_are_not_lost_in_the_global_bbox() -> None:
    islands = [box(x, 0, x + 3.4, 3.4) for x in (0, 1_000, 100_000)]
    app, project = _application(MultiPolygon(islands))
    witness = [(x + 1.7, 1.7) for x in (0, 1_000, 100_000)]
    _assert_feasible_witness(app, project, witness, 6)
    preview = app.preview_pattern(project.id, _request(3))
    _assert_valid_result(app, project, preview, 3, 6)
    assert preview.change_set is not None
    assert all(
        sum(
            island.covers(Point(item.x, item.y))
            for item in preview.change_set.additions
        )
        == 1
        for island in islands
    )


def test_holes_remain_forbidden_while_the_surrounding_band_is_used() -> None:
    hole = box(10, 10, 70, 70)
    area = Polygon(box(0, 0, 80, 80).exterior.coords, [hole.exterior.coords])
    app, project = _application(area)
    witness = [
        (3, 3),
        (40, 3),
        (77, 3),
        (3, 40),
        (77, 40),
        (3, 77),
        (40, 77),
        (77, 77),
    ]
    _assert_feasible_witness(app, project, witness, 15)
    preview = app.preview_pattern(project.id, _request(8, 15))
    _assert_valid_result(app, project, preview, 8, 15)
    assert preview.change_set is not None
    # Общий covers допускает касание границы отверстия, но не общие внутренности.
    assert all(
        hole.relate_pattern(Point(item.x, item.y).buffer(item.radius), "F********")
        for item in preview.change_set.additions
    )


def test_saved_plants_are_preserved_and_the_remaining_corridor_is_searched() -> None:
    saved = [
        PlanObject(id=f"saved-{x}", kind="tree", x=x, y=1.7, radius=1.6)
        for x in (5, 17)
    ]
    app, project = _application(box(0, 0, 35, 3.4), saved=saved)
    _assert_feasible_witness(app, project, [(23, 1.7), (31, 1.7)], 6)
    preview = app.preview_pattern(project.id, _request(2))
    _assert_valid_result(app, project, preview, 2, 6)
    retained = app.get(project.id)
    assert retained.plan is not None and retained.plan.objects == saved


def test_nested_zone_ownership_uses_the_whole_footprint() -> None:
    """Центр внутри B, но габарит не помещается в B: точка принадлежит A."""
    app, project = _application(
        box(0, 0, 30, 30),
        site=box(3.4, 10, 7, 20),
        nested=box(5, 5, 25, 25),
    )
    _assert_feasible_witness(app, project, [(5.2, 15)], 6)
    preview = app.preview_pattern(project.id, _request(1))
    _assert_valid_result(app, project, preview, 1, 6)
    assert preview.change_set is not None
    assert all(item.x >= 5 for item in preview.change_set.additions)


def test_unknown_component_does_not_consume_another_components_place(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    islands = [box(0, 0, 3.4, 3.4), box(100, 0, 103.4, 3.4)]
    app, project = _application(MultiPolygon(islands))
    _assert_feasible_witness(app, project, [(101.7, 1.7)], 6)

    def advisory(project: Project, x: float, *args: object) -> PositionAdvisory | None:
        return (
            PositionAdvisory(
                "NATIVE_LOCAL_UNKNOWN",
                "Проверка",
                "Локальная область не подтверждена",
                "Уточнить геометрию локальной области",
            )
            if x < 50
            else None
        )

    monkeypatch.setattr(app.geometry, "placement_advisory_detail", advisory)
    preview = app.preview_pattern(project.id, _request(1))
    _assert_valid_result(app, project, preview, 1, 6)
    assert preview.change_set is not None
    assert all(item.x >= 100 for item in preview.change_set.additions)


def test_natural_count_does_not_construct_a_seeded_random_probe_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_random(*args: object, **kwargs: object) -> None:
        raise AssertionError("Геометрический поиск не должен создавать случайный пул")

    app, project = _application(box(0, 0, 40, 40))
    monkeypatch.setattr("app.planning.search.sampling.Random", unexpected_random)
    preview = app.preview_pattern(project.id, _request(4))
    _assert_valid_result(app, project, preview, 4, 6)


def test_streaming_candidates_preserve_plant_kinds_for_network_trace_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пустой ещё не заполненный поток не означает отсутствие сетевых допущений."""
    app, project = _application(box(0, 0, 40, 40))
    observed_kinds: list[frozenset[str]] = []

    def assumption_codes(
        geometry: object, project: Project, plant_kinds: frozenset[str]
    ) -> frozenset[str]:
        observed_kinds.append(plant_kinds)
        return frozenset({"GEOMETRY_RULE_ASSUMPTION"})

    # Объявленная capability — явный тестовый провайдер верхней границы кодов.
    monkeypatch.setattr(
        type(app.geometry),
        "search_trace_assumption_codes",
        assumption_codes,
        raising=False,
    )
    preview = app.preview_pattern(project.id, _request(1))
    _assert_valid_result(app, project, preview, 1, 6)
    assert observed_kinds
    assert all(kinds == frozenset({"tree"}) for kinds in observed_kinds)


def test_time_budget_is_reported_as_an_unfinished_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 40, 40))
    _assert_feasible_witness(app, project, [(2, 2)], 6)
    clock = iter([0.0, 100.0])
    monkeypatch.setattr(geometric_search, "monotonic", lambda: next(clock, 100.0))
    with pytest.raises(DomainFailure) as error:
        app.preview_pattern(project.id, _request(1))
    assert error.value.reason == FailureReason.PATTERN_SEARCH_TIMEOUT
    assert not app.changes._previews
    assert app.get(project.id).plan == project.plan


def _small_work_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ограничивает именно вычислительную работу, не вместимость или правила."""
    config = geometric_search.PLACEMENT_CONFIG
    # Два шага подготовки расходуются на компонент и сечение, без интервалов.
    # Адаптивному поиску хватает фронта на начальный центр и одну ячейку.
    limits = config.geometry_search.model_copy(
        update={"maximum_cells": 1, "maximum_frontier": 2, "maximum_trials": 1}
    )
    monkeypatch.setattr(
        geometric_search,
        "PLACEMENT_CONFIG",
        config.model_copy(update={"geometry_search": limits}),
    )


def test_work_budget_does_not_claim_that_a_known_feasible_request_is_impossible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 40, 40))
    witness = [(2 + 8 * x, 2 + 8 * y) for x in range(5) for y in range(4)]
    _assert_feasible_witness(app, project, witness, 6)
    _small_work_budget(monkeypatch)
    preview = app.preview_pattern(project.id, _request(20))
    assert preview.accepted_count < 20
    assert preview.search_stop_reason == "candidate_limit"
    assert app.get(project.id).plan == project.plan


def test_an_unknown_checker_result_is_not_admitted_by_the_geometric_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 40, 40))
    _assert_feasible_witness(app, project, [(2, 2)], 6)
    _small_work_budget(monkeypatch)
    monkeypatch.setattr(
        app.geometry,
        "placement_advisory_detail",
        lambda *args: PositionAdvisory(
            "NATIVE_LOCAL_UNKNOWN",
            "Проверка",
            "Геометрия позиции неизвестна",
            "Уточнить геометрию позиции",
        ),
    )
    preview = app.preview_pattern(project.id, _request(1))
    assert preview.accepted_count == 0
    assert preview.search_stop_reason == "candidate_limit"
    assert preview.change_set is None
    assert preview.search_full_checked_count is not None
    assert preview.search_full_checked_count >= 1
    assert any(item.code == "NATIVE_LOCAL_UNKNOWN" for item in preview.skipped)
    assert app.get(project.id).plan == project.plan


def test_a_provider_failure_does_not_publish_a_fallback_or_partial_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 40, 40))

    def unavailable(*args: object, **kwargs: object) -> None:
        raise RuntimeError("Расчётный снимок недоступен")

    monkeypatch.setattr(
        "app.planning.search.candidate_search.prepare_positions", unavailable
    )
    with pytest.raises(RuntimeError, match="Расчётный снимок недоступен"):
        app.preview_pattern(project.id, _request(4))
    assert not app.changes._previews
    assert app.get(project.id).plan == project.plan


def test_cancellation_is_propagated_without_publishing_a_partial_preview() -> None:
    app, project = _application(box(0, 0, 40, 40))

    def cancel() -> None:
        raise OperationCancelled()

    with operation_cancellation(cancel), pytest.raises(OperationCancelled):
        app.preview_pattern(project.id, _request(4))
    assert not app.changes._previews
    assert app.get(project.id).plan == project.plan


def test_unknown_positions_do_not_allocate_empty_accepted_spacing_cells(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 40, 40))
    _small_work_budget(monkeypatch)
    grids: list[defaultdict[tuple[int, int], list[tuple[float, float]]]] = []

    def grid(
        factory: Callable[[], list[tuple[float, float]]],
    ) -> defaultdict[tuple[int, int], list[tuple[float, float]]]:
        value: defaultdict[tuple[int, int], list[tuple[float, float]]] = defaultdict(
            factory
        )
        grids.append(value)
        return value

    monkeypatch.setattr(candidate_search, "defaultdict", grid)
    monkeypatch.setattr(
        app.geometry,
        "placement_advisory_detail",
        lambda *args: PositionAdvisory(
            "NATIVE_LOCAL_UNKNOWN", "Проверка", "Позиция неизвестна", "Уточнить позицию"
        ),
    )
    preview = app.preview_pattern(project.id, _request(1))
    assert preview.accepted_count == 0
    assert grids and all(not value for value in grids)


def test_discarded_trial_results_are_released_before_a_later_trial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 3.4, 3.4))
    config = geometric_search.PLACEMENT_CONFIG
    monkeypatch.setattr(
        geometric_search,
        "PLACEMENT_CONFIG",
        config.model_copy(
            update={
                "geometry_search": config.geometry_search.model_copy(
                    update={"maximum_cells": 10_000, "maximum_trials": 3}
                )
            }
        ),
    )
    references: list[ReferenceType[CandidateSearchResult]] = []
    prior_live_counts: list[int] = []

    @dataclass
    class ObservedResult(CandidateSearchResult):
        def __post_init__(self) -> None:
            prior_live_counts.append(sum(value() is not None for value in references))
            references.append(ref(self))

    monkeypatch.setattr(candidate_search, "CandidateSearchResult", ObservedResult)
    monkeypatch.setattr(
        app.geometry,
        "placement_advisory_detail",
        lambda *args: PositionAdvisory(
            "NATIVE_LOCAL_UNKNOWN", "Проверка", "Позиция неизвестна", "Уточнить позицию"
        ),
    )
    preview = app.preview_pattern(project.id, _request(40))
    assert preview.accepted_count == 0
    assert len(prior_live_counts) == 3
    # Одна лучшая раскладка нужна для результата; отвергнутые прошлые уже не нужны.
    assert max(prior_live_counts) <= 1


def test_the_cell_work_budget_is_shared_by_all_trials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = _application(box(0, 0, 200, 200))
    config = geometric_search.PLACEMENT_CONFIG
    monkeypatch.setattr(
        geometric_search,
        "PLACEMENT_CONFIG",
        config.model_copy(
            update={
                "geometry_search": config.geometry_search.model_copy(
                    update={"maximum_cells": 200, "maximum_trials": 8}
                )
            }
        ),
    )
    sources: set[GeometryCandidateSearch] = set()

    class ObservedSearch(GeometryCandidateSearch):
        def next_batch(self, limit: int) -> list[PatternCandidate]:
            sources.add(self)
            return super().next_batch(limit)

    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", ObservedSearch)
    monkeypatch.setattr(
        app.geometry,
        "placement_advisory_detail",
        lambda *args: PositionAdvisory(
            "NATIVE_LOCAL_UNKNOWN", "Проверка", "Позиция неизвестна", "Уточнить позицию"
        ),
    )
    preview = app.preview_pattern(project.id, _request(40))
    assert preview.accepted_count == 0
    assert preview.search_stop_reason == "candidate_limit"
    assert sources
    used_cells = sum(int(value.metrics["cells_evaluated"]) for value in sources)
    assert 0 < used_cells <= 200


def test_completed_zone_quota_does_not_append_more_operations_on_later_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = application(block_east=True)
    batch = geometric_search._GeometryQueue.batch
    after_quota: list[int] = []

    def observed_batch(queue: geometric_search._GeometryQueue, limit: int) -> list[int]:
        saturated = queue.source.accepted_count >= 2
        before = len(queue.operations)
        result = batch(queue, limit)
        if saturated:
            after_quota.append(len(queue.operations) - before)
        return result

    monkeypatch.setattr(geometric_search._GeometryQueue, "batch", observed_batch)
    preview = app.preview_pattern(
        project.id,
        _request(4).model_copy(
            update={"zone_ids": ["west", "east"], "zone_distribution": "equal"}
        ),
    )
    assert preview.accepted_count == 2
    assert [
        (value.requested_count, value.accepted_count)
        for value in preview.zone_allocations
    ] == [(2, 2), (2, 0)]
    assert all(count == 0 for count in after_quota)
    assert app.get(project.id).plan == project.plan


@pytest.mark.parametrize("declared_bound", [False, True])
def test_rejected_trace_compaction_keeps_actual_network_witnesses_and_full_checks(
    monkeypatch: pytest.MonkeyPatch,
    declared_bound: bool,
) -> None:
    app, project = _application(box(0, 0, 200, 200))
    config = geometric_search.PLACEMENT_CONFIG
    monkeypatch.setattr(
        geometric_search,
        "PLACEMENT_CONFIG",
        config.model_copy(
            update={
                "geometry_search": config.geometry_search.model_copy(
                    update={"maximum_cells": 16, "maximum_trials": 1}
                )
            }
        ),
    )
    checked = 0
    trace_calls = 0
    retained: list[CandidateSearchResult] = []
    original_trace = app.geometry.position_rule_trace

    def unknown(*args: object) -> PositionAdvisory:
        nonlocal checked
        checked += 1
        return PositionAdvisory(
            "NATIVE_LOCAL_UNKNOWN",
            "Проверка",
            "Позиция неизвестна",
            "Уточнить позицию",
            source_layer="unknown-layer",
            source_feature_ids=("unknown-local-face",),
        )

    def bound(
        geometry: object,
        snapshot: Project,
        plant_kinds: frozenset[str],
    ) -> frozenset[str] | None:
        assert plant_kinds == frozenset({"tree"})
        return frozenset({"A", "B"}) if declared_bound else None

    def trace(
        snapshot: Project,
        x: float,
        y: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
        mature_crown_diameter_m: float | None = None,
    ) -> PlantingRuleTrace:
        nonlocal trace_calls
        value = original_trace(snapshot, x, y, plant_kind, mature_crown_diameter_m)
        assert value is not None
        trace_calls += 1
        code = "A" if trace_calls <= 2 else "B"
        return value.model_copy(
            update={
                "entries": [
                    RuleTraceEntry(
                        obstacle_kind="utility",
                        status="not_checked",
                        code=code,
                        note=code,
                    )
                ]
            }
        )

    @dataclass
    class ObservedResult(CandidateSearchResult):
        def __post_init__(self) -> None:
            retained.append(self)

    monkeypatch.setattr(app.geometry, "placement_advisory_detail", unknown)
    monkeypatch.setattr(
        type(app.geometry), "search_trace_assumption_codes", bound, raising=False
    )
    monkeypatch.setattr(app.geometry, "position_rule_trace", trace)
    monkeypatch.setattr(candidate_search, "CandidateSearchResult", ObservedResult)
    preview = app.preview_pattern(project.id, _request(40))
    assert preview.accepted_count == 0
    assert preview.search_full_checked_count == checked
    assert checked > 3
    results = [result for trial in retained for result in trial.candidate_results]
    assert all(value.code == "NATIVE_LOCAL_UNKNOWN" for value in results)
    assert all(value.source_layer == "unknown-layer" for value in results)
    assert all(value.source_feature_ids == ["unknown-local-face"] for value in results)
    actual = set().union(
        *(utility_assumption_codes(value.rule_trace) for value in results)
    )
    assert actual == {"A", "B"}
    assert sum(value.rule_trace is not None for value in results) == 2
    assert trace_calls == (3 if declared_bound else checked)
    assert app.get(project.id).plan == project.plan


def test_mixed_small_plants_cannot_be_accepted_in_an_unselected_nested_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Владение фактического кустарника отличается от шаблонного дерева."""
    app, project = _application(
        box(0, 0, 30, 30), site=box(4.4, 10, 8, 20), nested=box(5, 5, 25, 25)
    )
    assert project.plan is not None
    witness, issue = app.evaluation.preview_addition(
        project, project.plan, PlanObjectCreate(kind="shrub", x=6.2, y=15)
    )
    assert issue is None and witness.planting_zone_id == "nested"
    config = geometric_search.PLACEMENT_CONFIG
    limits = config.geometry_search.model_copy(
        update={"maximum_cells": 100, "maximum_trials": 1}
    )
    monkeypatch.setattr(
        geometric_search,
        "PLACEMENT_CONFIG",
        config.model_copy(update={"geometry_search": limits}),
    )
    preview = app.preview_pattern(
        project.id,
        _request(1).model_copy(update={"composition": "mixed", "tree_share": 0.0}),
    )
    assert preview.accepted_count == 0
    assert preview.change_set is None
    assert app.get(project.id).plan == project.plan


def test_mixed_small_plants_reach_the_target_in_the_selected_boundary_strip() -> None:
    app, project = _application(
        box(0, 0, 30, 30), site=box(3.4, 10, 7, 20), nested=box(5, 5, 25, 25)
    )
    assert project.plan is not None
    witness, issue = app.evaluation.preview_addition(
        project, project.plan, PlanObjectCreate(kind="shrub", x=5.2, y=15)
    )
    assert issue is None and witness.planting_zone_id == "outer"
    preview = app.preview_pattern(
        project.id,
        _request(1).model_copy(update={"composition": "mixed", "tree_share": 0.0}),
    )
    _assert_valid_result(app, project, preview, 1, 6)
    assert preview.change_set is not None
    assert preview.change_set.additions[0].kind == "shrub"


def test_another_constructive_layout_is_checked_before_adaptive_refinement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неудачная первая раскладка не тратит ячейки до другой раскладки N."""
    app, project = _application(box(0, 0, 60, 40))
    # Первый нерегулярный вариант попадает в четыре адресные неопределённые
    # области. Достижимый второй набор подтверждается обычным оценщиком ниже,
    # независимо от генератора и его точных координат.
    unknown_centers = [(21, 20), (27, 12.64), (33, 27.36), (39, 8.96)]
    sources: list[GeometryCandidateSearch] = []
    results: list[CandidateSearchResult] = []
    checks: list[tuple[float, float, bool]] = []

    class ObservedSearch(GeometryCandidateSearch):
        def next_batch(self, limit: int) -> list[PatternCandidate]:
            if self not in sources:
                sources.append(self)
            return super().next_batch(limit)

    @dataclass
    class ObservedResult(CandidateSearchResult):
        def __post_init__(self) -> None:
            results.append(self)

    def advisory(
        snapshot: Project,
        x: float,
        y: float,
        radius: float,
        plant_kind: Literal["tree", "shrub"] = "tree",
    ) -> PositionAdvisory | None:
        unknown = any(hypot(x - cx, y - cy) < 0.1 for cx, cy in unknown_centers)
        checks.append((x, y, unknown))
        return (
            PositionAdvisory(
                "NATIVE_LOCAL_UNKNOWN",
                "Проверка",
                "Локальные области не подтверждены",
                "Уточнить области",
                source_layer="unknown-patches",
                source_feature_ids=("unknown-local-face",),
            )
            if unknown
            else None
        )

    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", ObservedSearch)
    monkeypatch.setattr(candidate_search, "CandidateSearchResult", ObservedResult)
    monkeypatch.setattr(app.geometry, "placement_advisory_detail", advisory)
    _assert_feasible_witness(app, project, [(21, 24), (27, 16), (33, 31), (39, 7)], 6)
    checks.clear()
    preview = app.preview_pattern(project.id, _request(4))
    _assert_valid_result(app, project, preview, 4, 6)
    assert len(sources) == len(results) == 2
    assert [source.metrics["refinement_enabled"] for source in sources] == [0, 0]
    assert all(source.metrics["cells_evaluated"] == 0 for source in sources)
    assert all(unknown for _, _, unknown in checks[:4])
    assert all(not unknown for _, _, unknown in checks[4:8])
    assert preview.search_full_checked_count == 8
    assert results[0].full_checked_count == 4
    assert all(
        value.code == "NATIVE_LOCAL_UNKNOWN" for value in results[0].candidate_results
    )
    assert all(
        value.source_layer == "unknown-patches"
        for value in results[0].candidate_results
    )
    assert all(
        value.source_feature_ids == ["unknown-local-face"]
        for value in results[0].candidate_results
    )


def test_enough_components_use_internal_witnesses_without_building_layouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    islands = MultiPolygon([box(x, 0, x + 3.4, 3.4) for x in (0, 100, 300)])
    app, project = _application(islands)
    _assert_feasible_witness(app, project, [(1.7, 1.7), (101.7, 1.7)], 6)
    sources: list[GeometryCandidateSearch] = []

    class ObservedSearch(GeometryCandidateSearch):
        def next_batch(self, limit: int) -> list[PatternCandidate]:
            if self not in sources:
                sources.append(self)
            return super().next_batch(limit)

    def unnecessary_layouts(
        *args: object, **kwargs: object
    ) -> list[list[PatternCandidate]]:
        raise AssertionError("Достаточно внутренних точек фрагментов для запроса N")

    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", ObservedSearch)
    monkeypatch.setattr(geometric_search, "propose_target_layouts", unnecessary_layouts)
    preview = app.preview_pattern(project.id, _request(2))
    _assert_valid_result(app, project, preview, 2, 6)
    assert len(sources) == 1
    assert sources[0].metrics["refinement_enabled"] == 1
    assert sources[0].metrics["components"] == 3


def test_a_partial_geometric_layout_is_completed_without_discarding_accepted_points(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Односвязный X имеет три допустимых места, но осевые сечения предлагают два.
    area = unary_union(
        [
            LineString([(-12, -12), (12, 12)]).buffer(1.8, cap_style="flat"),
            LineString([(-12, 12), (12, -12)]).buffer(1.8, cap_style="flat"),
        ]
    )
    app, project = _application(area)
    _assert_feasible_witness(app, project, [(-8, -8), (8, 8), (8, -8)], 6)
    original = geometric_search.propose_target_layouts
    layouts: list[list[PatternCandidate]] = []
    sources: list[GeometryCandidateSearch] = []

    def observed_layouts(
        geometry: BaseGeometry,
        spacing_m: float,
        target_count: int,
        *,
        max_layouts: int,
        max_segments: int,
        check_cancelled: Callable[[], None],
    ) -> list[list[PatternCandidate]]:
        result = original(
            geometry,
            spacing_m,
            target_count,
            max_layouts=max_layouts,
            max_segments=max_segments,
            check_cancelled=check_cancelled,
        )
        layouts.extend(result)
        return result

    class ObservedSearch(GeometryCandidateSearch):
        def next_batch(self, limit: int) -> list[PatternCandidate]:
            if self not in sources:
                sources.append(self)
            return super().next_batch(limit)

    monkeypatch.setattr(geometric_search, "propose_target_layouts", observed_layouts)
    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", ObservedSearch)
    preview = app.preview_pattern(project.id, _request(3))
    _assert_valid_result(app, project, preview, 3, 6)
    assert max(map(len, layouts)) == 2
    assert len(sources) == 1
    assert sources[0].metrics["refinement_enabled"] == 1
    assert sources[0].accepted_count == 3
    best_partial = max(layouts, key=len)
    assert preview.change_set is not None
    accepted_points = {(value.x, value.y) for value in preview.change_set.additions}
    assert {(value.x, value.y) for value in best_partial} <= accepted_points
    assert preview.search_full_checked_count == 3
