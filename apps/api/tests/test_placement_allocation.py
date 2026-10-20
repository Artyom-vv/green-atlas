import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from hashlib import sha256
from itertools import count
from typing import Literal
from unittest.mock import create_autospec

import pytest
from shapely.geometry import LineString, MultiPolygon, Point, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.application import ProjectApplication
from app.contracts import (
    FillPatternRequest,
    GeometrySnapshot,
    PlacementMaskRequest,
    Plan,
    PlantingZoneAssignment,
    Project,
)
from app.dxf_import.ports import DxfReaderPort
from app.dxf_import.source.review_contracts import SourceReview
from app.exporting.ports import DxfWriterPort
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.checking.domain import PositionChecker
from app.geometry.ports import GeometryQueryPort
from app.history.adapters import SqliteProjectHistory
from app.operations.ports import OperationRepository
from app.planning.changes.contracts import (
    ChangeSetPreview,
    PlanChangeSetDraft,
)
from app.planning.changes.evaluation import PlanEvaluation
from app.planning.contracts import PlacementCheckRequest, PlanObject
from app.planning.domain import PlanVersionConflict
from app.planning.patterns import application as pattern_application
from app.planning.patterns.contracts import PatternPreviewRequest, RowPatternRequest
from app.planning.patterns.generators import ShapelyCandidateGenerator
from app.planning.ports import PatternCandidate
from app.planning.search import candidate_search, geometric_search
from app.planning.search.allocation import equal_zone_targets, spread_indices
from app.planning.search.geometric_search import GeometricSearchResult, OperationFactory
from app.projects.adapters import SqliteProjectRepository
from app.shared.failures import DomainFailure
from app.validation.adapters import RuleBasedPlanValidator


def application(block_east: bool = False) -> tuple[ProjectApplication, Project]:
    features = [
        {
            "type": "Feature",
            "properties": {"kind": "site_border"},
            "geometry": mapping(box(-10, -10, 510, 210)),
        }
    ]
    if block_east:
        features.append(
            {
                "type": "Feature",
                "properties": {"kind": "water"},
                "geometry": mapping(box(400, 0, 500, 100)),
            }
        )
    project = Project(
        name="Allocation",
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
        plan=Plan(),
        planting_zones=[
            PlantingZoneAssignment(
                id="west", label="West", geometry=mapping(box(0, 0, 200, 200))
            ),
            PlantingZoneAssignment(
                id="east", label="East", geometry=mapping(box(400, 0, 500, 100))
            ),
        ],
    )
    repo = SqliteProjectRepository(":memory:")
    project = repo.create(project)
    app = ProjectApplication(
        repository=repo,
        operation_repository=create_autospec(OperationRepository, instance=True),
        history=SqliteProjectHistory(repo),
        dxf_reader=create_autospec(DxfReaderPort, instance=True),
        geometry=ShapelyGeometryEngine(),
        geometry_query=create_autospec(GeometryQueryPort, instance=True),
        validator=RuleBasedPlanValidator(),
        writer=create_autospec(DxfWriterPort, instance=True),
        candidate_generator=ShapelyCandidateGenerator(),
    )
    return app, project


def test_equal_targets_have_a_stable_remainder_and_no_duplicates() -> None:
    assert equal_zone_targets(["a", "b", "a", "c"], 5) == {"a": 2, "b": 2, "c": 1}
    assert equal_zone_targets(["a", "b", "c"], 1) == {"a": 1, "b": 0, "c": 0}
    assert equal_zone_targets([], 5) == {}
    assert spread_indices([0, 1, 2, 3, 4], 2) == [0, 4]
    assert spread_indices([0, 1, 2], 0) == []


@pytest.mark.parametrize("layout", ["natural", "regular", "staggered", "mask"])
def test_equal_count_reaches_distant_unequal_zones_and_keeps_only_final_preview(
    layout: Literal["natural", "regular", "staggered", "mask"],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = application()
    assert project.plan is not None
    request: PlacementMaskRequest | FillPatternRequest
    if layout == "mask":
        request = PlacementMaskRequest(
            mask_id="regular_grid",
            base_plan_version=project.plan.version,
            zone_ids=["east", "west"],
            placement_mode="count",
            target_count=5,
            zone_distribution="equal",
            spacing_m=6,
        )
    else:
        request = FillPatternRequest(
            layout=layout,
            base_plan_version=project.plan.version,
            zone_ids=["east", "west"],
            placement_mode="count",
            target_count=5,
            zone_distribution="equal",
            spacing_m=6,
        )
    refreshes: list[int] = []
    original = app.validation.refresh

    def refresh(
        project: Project, plan: Plan, *, increment_version: bool = False
    ) -> None:
        refreshes.append(1)
        original(project, plan, increment_version=increment_version)

    monkeypatch.setattr(app.validation, "refresh", refresh)
    preview = app.preview_pattern(project.id, request)
    assert preview.accepted_count == 5
    assert [
        (z.zone_id, z.requested_count, z.accepted_count)
        for z in preview.zone_allocations
    ] == [("west", 3, 3), ("east", 2, 2)]
    assert preview.change_set is not None
    assert preview.change_set.can_apply
    assert len(app.changes._previews) == len(refreshes) == 1
    retained = app.get(project.id)
    assert retained.plan is not None
    assert retained.plan.objects == []
    assert all(
        item.status == "allowed" for item in preview.change_set.candidate_results
    )


def test_empty_zone_quota_is_not_silently_moved_to_another_zone() -> None:
    app, project = application(block_east=True)
    assert project.plan is not None
    request = FillPatternRequest(
        base_plan_version=project.plan.version,
        zone_ids=["west", "east"],
        placement_mode="count",
        target_count=4,
        zone_distribution="equal",
        layout="natural",
    )
    preview = app.preview_pattern(project.id, request)
    assert preview.accepted_count == 2
    assert [
        (z.requested_count, z.accepted_count) for z in preview.zone_allocations
    ] == [(2, 2), (2, 0)]
    assert (
        preview.requested_count
        == preview.accepted_count + preview.rejected_count + preview.capacity_shortfall
    )
    available = app.preview_pattern(
        project.id, request.model_copy(update={"zone_distribution": "available"})
    )
    assert available.accepted_count == 4
    assert all(z.requested_count is None for z in available.zone_allocations)


def test_zero_quota_does_not_generate_unrequested_sites() -> None:
    app, project = application()
    assert project.plan is not None
    preview = app.preview_pattern(
        project.id,
        FillPatternRequest(
            base_plan_version=project.plan.version,
            zone_ids=["west", "east"],
            placement_mode="count",
            target_count=1,
            zone_distribution="equal",
            layout="natural",
        ),
    )
    assert [
        (z.requested_count, z.accepted_count) for z in preview.zone_allocations
    ] == [(1, 1), (0, 0)]


@pytest.mark.parametrize(
    "obstacle",
    [
        box(65, 40, 5000, 55),
        LineString([(0, 50), (5000, 50)]),
        Point(49, 49),
        box(104, 104, 108, 108),
    ],
)
def test_local_growth_buffer_matches_whole_obstacle_buffer(
    obstacle: BaseGeometry,
) -> None:
    area = box(0, 0, 100, 100)
    far = box(10000, 10000, 20000, 20000)
    geometry = unary_union([obstacle, far])
    project = Project(
        name="Buffer equivalence",
        geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"kind": "utility"},
                        "geometry": mapping(geometry),
                    },
                ],
            }
        ),
    )
    checker = PositionChecker(project)
    reference = checker.hard_safe_area(area, 1.6, "tree").difference(geometry.buffer(8))
    actual = checker.automatic_safe_area(
        area, 1.6, "tree", growth_canopy_radius=6, growth_root_radius=8
    )
    assert actual.symmetric_difference(reference).area < 1e-7


PATTERN_BASELINE_DIGESTS: dict[str, str] = {
    "regular": "885b0f61b1a6c1b24351894ab4204e02ee9d220868a5467fbd2d6d0a0aa2aa5f",
    "staggered_spacing": "840c5ec3f7db185ae6891a2b1b6adf205724f83d5f927247d328676b9ce45849",
    "mask_mixed": "e9e96bc31403bddc21ad5ccc2aacb083216aa733d83b28565ffb58e213be913c",
    "row": "e9f44a7a97c5846f23eea5f7fa50bf92adbb33264e8ea0fcf1ed2f7e01464d9c",
    "blocked_row": "b62279828655d13f2dd236f4e3ff7907170ee8792168272ff797a1359887773d",
    "building_groups": "ce8c4177c8d3e514f920ea34b095adcffe57d9430fea8408ef896aea0b93b0ed",
    "outside": "310b6abf44c9687e0ddc34c7e471d42f99835631e0568daaeb036b42b80a5959",
    "progress": "a5967249ee1f910f14cb02c8487245883d6be243d14faf1beac8fb7cc391a212",
    "missing_zone": "837421a64c3dc0a836a9299ccea644d7de22f52cbd933cd4e39d28899443fc66",
    "version_conflict": "c993b4899950f3a575fdfd811a1a511ab629bb53caeda9edb488627cf624b911",
    "plan_missing": "45b1f0432a04af2f38b211f89913f93f3b40db0d994b10fc027ef87208ce37fe",
    "roads_missing": "efb8345f12c8be5e9d3b42f8b38fb22bc407c9bf9b803be79a40a8021f2a83ce",
    "source_pending": "0f91b6b7251185822634752ed8776fd121e40fa60b7f058fb92eddb0177f5485",
    "mixed_filtered": "4d7e8e6b73e74a0de72f29bc9e3397b0b8b01b802908e25a9ac9743c2cfef363",
    "building_contour": "a26f349eb49c8998cba4835429b873461ff0c0eab20b05e29cbe8359d4079dc2",
    "roads": "971dd6f716c020007a30169cdc3ead532e442b62b95577d9894bbee0fd7513c8",
    "cluster": "00f89aff18d8f297e679fe02c869b75d9889a9f4827114f2d80a6e56e7963a8e",
}


def pattern_baseline(case: str, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Полный результат и входы проверок; координаты и порядок не нормализуются."""
    app, original = application(block_east=case in {"blocked_equal", "blocked_row"})
    fixed_time = datetime(2026, 10, 8, tzinfo=UTC)
    project = original.model_copy(
        update={
            "id": "pattern-baseline",
            "created_at": fixed_time.isoformat(),
            "updated_at": fixed_time.isoformat(),
        }
    )
    assert project.plan is not None
    project.plan.id = "baseline-plan"
    app.repository.create(project)
    ids = count()

    def new_id() -> str:
        return f"baseline-{next(ids)}"

    monkeypatch.setattr(app.evaluation, "new_id", new_id)
    monkeypatch.setattr(app.changes, "new_id", new_id)
    monkeypatch.setattr(app.changes, "now", lambda: fixed_time)
    monkeypatch.setattr(candidate_search, "monotonic", lambda: 0.0)
    monkeypatch.setattr(geometric_search, "monotonic", lambda: 0.0)
    calls: dict[str, list] = {"generation": [], "previews": [], "search": []}
    generator = app.patterns.candidate_generator.generate

    def generate(
        request: PatternPreviewRequest,
        zones: list[PlantingZoneAssignment],
        guide_geometries: list[dict] | None = None,
        *,
        alternatives: bool = False,
    ) -> list[PatternCandidate]:
        if case in {"empty", "empty_unconfirmed"}:
            points = []
        elif case == "outside":
            points = [PatternCandidate(-100, -100)]
        elif case == "building_groups":
            points = [
                PatternCandidate(20, 20, group_key="a"),
                PatternCandidate(40, 20, group_key="a"),
                PatternCandidate(60, 20, group_key="singleton"),
                PatternCandidate(80, 20, group_key="b"),
                PatternCandidate(100, 20, group_key="b"),
                PatternCandidate(120, 20, group_key="b"),
            ]
        elif case == "mixed_filtered":
            points = [
                PatternCandidate(-100, -100),
                PatternCandidate(20, 20),
                PatternCandidate(40, 20),
            ]
        else:
            points = generator(
                request, zones, guide_geometries, alternatives=alternatives
            )
        calls["generation"].append(
            {
                "request": request.model_dump(mode="json"),
                "zones": [zone.model_dump(mode="json") for zone in zones],
                "guides": guide_geometries,
                "alternatives": alternatives,
                "candidates": [asdict(point) for point in points],
            }
        )
        return points

    monkeypatch.setattr(app.patterns.candidate_generator, "generate", generate)
    preview_on_snapshot = app.changes.preview_on_snapshot

    def preview(
        snapshot: Project, draft: PlanChangeSetDraft, *, cache_preview: bool = True
    ) -> ChangeSetPreview:
        result = preview_on_snapshot(snapshot, draft, cache_preview=cache_preview)
        calls["previews"].append(
            {
                "draft": draft.model_dump(mode="json"),
                "cache": cache_preview,
                "result": result.model_dump(mode="json"),
            }
        )
        return result

    monkeypatch.setattr(app.changes, "preview_on_snapshot", preview)
    search = pattern_application.search_geometric_placement

    def search_geometry(
        snapshot: Project,
        request: FillPatternRequest,
        generation_zones: list[PlantingZoneAssignment],
        evaluation: PlanEvaluation,
        spacing: float,
        zone_targets: dict[str, int],
        operation_factory: OperationFactory,
    ) -> GeometricSearchResult:
        result = search(
            snapshot,
            request,
            generation_zones,
            evaluation,
            spacing,
            zone_targets,
            operation_factory,
        )
        calls["search"].append(
            {
                "operations": [op.model_dump(mode="json") for op in result.operations],
                "target": request.target_count,
                "zone_targets": zone_targets,
                "spacing": spacing,
                "result": {
                    "candidate_results": [
                        item.model_dump(mode="json")
                        for item in result.search.candidate_results
                    ],
                    "stop_reason": result.search.stop_reason,
                    "elapsed_s": result.search.elapsed_s,
                    "spacing_pruned": result.search.spacing_pruned,
                    "full_checked_count": result.search.full_checked_count,
                },
            }
        )
        return result

    monkeypatch.setattr(
        pattern_application, "search_geometric_placement", search_geometry
    )
    request: PatternPreviewRequest = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["east", "west"],
        placement_mode="count",
        target_count=5,
        zone_distribution="equal",
        layout="natural",
        seed=47,
    )
    if case == "blocked_equal":
        request = request.model_copy(update={"target_count": 4})
    elif case == "natural_available":
        request = request.model_copy(update={"zone_distribution": "available"})
    elif case == "regular":
        request = request.model_copy(update={"layout": "regular"})
    elif case == "empty":
        # Пустой статический генератор не управляет геометрическим свободным поиском.
        request = request.model_copy(update={"layout": "regular"})
    elif case == "staggered_spacing":
        request = request.model_copy(
            update={"layout": "staggered", "placement_mode": "spacing", "spacing_m": 30}
        )
    elif case in {
        "mask_mixed",
        "mixed_filtered",
        "building_groups",
        "building_contour",
        "roads",
        "roads_missing",
        "cluster",
    }:
        masks: dict[
            str,
            Literal[
                "road_edges",
                "regular_grid",
                "cluster_groves",
                "building_screen",
                "building_contour",
            ],
        ] = {
            "building_groups": "building_screen",
            "building_contour": "building_contour",
            "roads": "road_edges",
            "roads_missing": "road_edges",
            "cluster": "cluster_groves",
        }
        request = PlacementMaskRequest(
            base_plan_version=1,
            zone_ids=["west"],
            placement_mode="count",
            target_count=4,
            mask_id=masks.get(case, "regular_grid"),
            seed=47,
            composition="mixed"
            if case in {"mask_mixed", "mixed_filtered"}
            else "trees",
            tree_share=0.45,
            tree_species_revision_id="tilia-cordata@2026-08-28.1",
            shrub_species_revision_id="spiraea-japonica@2026-08-28.1",
        )
        if case in {"building_contour", "roads"}:
            assert project.geometry is not None
            project.geometry.feature_collection["features"].append(
                {
                    "type": "Feature",
                    "properties": {
                        "kind": "building" if case == "building_contour" else "road"
                    },
                    "geometry": mapping(
                        box(80, 80, 120, 120)
                        if case == "building_contour"
                        else box(80, 0, 90, 200)
                    ),
                }
            )
            app.repository.save(project)
    elif case in {"row", "blocked_row", "outside"}:
        request = RowPatternRequest(
            base_plan_version=1,
            zone_ids=["east"] if case == "blocked_row" else ["west"],
            placement_mode="count",
            target_count=6,
            side="both",
            lateral_offset_m=10,
            axis=mapping(
                LineString(
                    [(420, 50), (480, 50)]
                    if case == "blocked_row"
                    else [(-20, 100), (220, 100)]
                )
            ),
        )
    elif case == "missing_zone":
        request = request.model_copy(update={"zone_ids": ["missing-b", "missing-a"]})
    elif case == "version_conflict":
        request = request.model_copy(update={"base_plan_version": 2})
    elif case == "plan_missing":
        project.plan = None
        app.repository.save(project)
    elif case == "source_pending":
        project.source_review = SourceReview()
        app.repository.save(project)
    elif case in {"progress", "empty_unconfirmed"}:
        if case == "empty_unconfirmed":
            request = request.model_copy(update={"layout": "regular"})
        zone = project.planting_zones[0].model_copy(deep=True)
        zone.geometry["ga_search_domain"] = {
            "revision": "baseline-domain",
            "geometry": mapping(box(0, 0, 20, 20)),
            "unresolved_geometry": mapping(box(20, 0, 40, 20)),
            "available_area_m2": 400,
            "excluded_area_m2": 0,
            "unresolved_area_m2": 400,
            "measured_cells": 2,
            "elapsed_s": 0,
            "stop_reason": "probe_limit" if case == "progress" else "resolution",
        }
        monkeypatch.setattr(
            app.evaluation, "automatic_generation_zones", lambda *args, **kwargs: [zone]
        )
    request = request.model_copy(update={"scenario_horizon_year": 20})
    before = app.repository.get(project.id).model_dump(mode="json")
    request_before = request.model_dump(mode="json")
    try:
        result: dict = {
            "preview": app.preview_pattern(project.id, request).model_dump(mode="json")
        }
    except DomainFailure as error:
        result = {
            "error": {
                "reason": error.reason.value,
                "params": error.params,
                "message": str(error),
            }
        }
    except PlanVersionConflict as error:
        result = {
            "error": {
                "expected": error.expected_version,
                "current": error.current_version,
                "message": str(error),
            }
        }
    retained_plan = app.repository.get(project.id).plan
    assert app.repository.get(project.id).model_dump(mode="json") == before
    assert request.model_dump(mode="json") == request_before
    return {
        "request": request.model_dump(mode="json"),
        "calls": calls,
        "result": result,
        "cached_previews": len(app.changes._previews),
        "retained_plan": retained_plan.model_dump(mode="json")
        if retained_plan
        else None,
    }


@pytest.mark.parametrize("case", list(PATTERN_BASELINE_DIGESTS))
def test_предпросмотр_сохраняет_полные_координаты_порядок_проверки_и_причины(
    case: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = pattern_baseline(case, monkeypatch)

    # Горизонт проверяем отдельно; эталон защищает координаты, порядок измерений
    # и причины решений при рабочем корневом габарите на горизонте 20 лет.
    # Идентичность очередей progress_key отдельно защищена phase-progress тестами;
    # поле отчётности не меняет прежний эталон расчётных данных.
    def previous_contract(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: previous_contract(item)
                for key, item in value.items()
                if key not in {"planning_horizon_year", "progress_key"}
                and not (key == "search_full_checked_count" and item is None)
            }
        if isinstance(value, list):
            return [previous_contract(item) for item in value]
        return value

    if "preview" in payload["result"]:
        assert payload["result"]["preview"]["planning_horizon_year"] == 20
    if case == "progress":
        preview = payload["result"]["preview"]
        assert [domain["progress_key"] for domain in preview["search_domains"]] == [
            f"{preview['pattern_id']}:0"
        ]
    normalized_payload = previous_contract(payload)
    encoded = json.dumps(
        normalized_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    expected = PATTERN_BASELINE_DIGESTS[case]
    if sys.platform == "win32" and case == "building_contour":
        # Полный побайтовый diff Windows x64 и Mac ARM64 выявил только четыре
        # вершины входного buffer-кольца с отличием в 1 ULP (до 1.43e-14 м).
        # Результат посадок, порядок и причины совпали. Оба SHA остаются точными:
        # округление координат или допуск в производственной геометрии не нужны.
        expected = "36c9a654583113eb7c00dbb0958998b60518dab06a0282633617ef87b696fc93"
    assert sha256(encoded).hexdigest() == expected


@pytest.mark.parametrize(
    ("case", "accepted", "allocations"),
    [
        ("natural_equal", 5, [("west", 3, 3), ("east", 2, 2)]),
        ("natural_available", 5, None),
        ("blocked_equal", 2, [("west", 2, 2), ("east", 2, 0)]),
    ],
)
def test_geometric_public_preview_preserves_quotas_checks_and_exact_repeat(
    case: str,
    accepted: int,
    allocations: list[tuple[str, int, int]] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Новый поиск намеренно меняет точки и порядок проб. Вместо прежнего random
    # digest защищаем публичный результат, общий checker и точный повтор.
    with monkeypatch.context() as first_patch:
        payload = pattern_baseline(case, first_patch)
    with monkeypatch.context() as repeat_patch:
        repeated = pattern_baseline(case, repeat_patch)
    assert payload == repeated
    preview = payload["result"]["preview"]
    assert preview["planning_horizon_year"] == 20
    assert preview["accepted_count"] == accepted
    assert preview["search_full_checked_count"] >= accepted
    assert not payload["calls"]["generation"]
    assert len(payload["calls"]["search"]) == 1
    assert payload["calls"]["search"][0]["target"] == payload["request"]["target_count"]
    if allocations is not None:
        assert [
            (zone["zone_id"], zone["requested_count"], zone["accepted_count"])
            for zone in preview["zone_allocations"]
        ] == allocations
    else:
        assert all(
            zone["requested_count"] is None for zone in preview["zone_allocations"]
        )
        assert (
            sum(zone["accepted_count"] for zone in preview["zone_allocations"])
            == accepted
        )
    assert preview["requested_count"] == (
        accepted + preview["rejected_count"] + preview["capacity_shortfall"]
    )
    change_set = preview["change_set"]
    assert change_set is not None and change_set["can_apply"]
    assert (
        len(change_set["additions"]) == len(change_set["candidate_results"]) == accepted
    )
    assert all(item["status"] == "allowed" for item in change_set["candidate_results"])
    assert payload["cached_previews"] == 1
    assert len(payload["calls"]["previews"]) == 1
    assert payload["calls"]["previews"][0]["cache"]
    assert payload["calls"]["previews"][0]["result"] == change_set
    objects = change_set["additions"]
    for obj in objects:
        work = (
            box(0, 0, 200, 200)
            if obj["planting_zone_id"] == "west"
            else box(400, 0, 500, 100)
        )
        assert work.covers(Point(obj["x"], obj["y"]).buffer(obj["radius"]))
    for index, obj in enumerate(objects):
        for previous in objects[:index]:
            assert (
                Point(obj["x"], obj["y"]).distance(Point(previous["x"], previous["y"]))
                >= preview["effective_spacing_m"]
            )
    assert payload["retained_plan"]["version"] == 1
    assert payload["retained_plan"]["objects"] == []


@pytest.mark.parametrize("unresolved", [False, True])
def test_empty_static_proposals_preserve_unknown_domain_and_do_not_cache(
    unresolved: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = pattern_baseline(
        "empty_unconfirmed" if unresolved else "empty", monkeypatch
    )
    preview = payload["result"]["preview"]
    assert payload["request"]["layout"] == "regular"
    assert payload["calls"]["generation"]
    assert all(not call["candidates"] for call in payload["calls"]["generation"])
    assert not payload["calls"]["search"]
    assert not payload["calls"]["previews"]
    assert payload["cached_previews"] == 0
    assert preview["generated_count"] == preview["accepted_count"] == 0
    assert preview["change_set"] is None
    assert preview["reason_summary"] == [
        {
            "status": "unknown" if unresolved else "blocked",
            "code": "SEARCH_DOMAIN_UNCONFIRMED"
            if unresolved
            else "SAFE_CAPACITY_REACHED",
            "category": "data" if unresolved else "constraint",
            "count": payload["request"]["target_count"],
            "message": "Предрасчёт не подтвердил область для выбранного растения"
            if unresolved
            else "В подготовленной области не сформированы позиции для выбранных параметров",
        }
    ]
    assert bool(preview["search_domains"]) == unresolved
    if unresolved:
        assert preview["search_domains"][0]["unresolved_area_m2"] == 400
    assert payload["retained_plan"]["objects"] == []


def test_public_geometric_search_finds_two_checker_verified_sides_of_narrow_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, project = application()
    assert project.geometry is not None
    assert project.plan is not None
    project.geometry.feature_collection["features"].append(
        {
            "type": "Feature",
            "properties": {"kind": "water"},
            "geometry": mapping(box(0, 4.2, 4.2, 55)),
        }
    )
    project.planting_zones = [
        PlantingZoneAssignment(
            id="narrow",
            label="Два узких фрагмента",
            geometry=mapping(
                MultiPolygon([box(0, 0, 4.2, 4.2), box(0, 55, 4.2, 59.2)])
            ),
        ),
        PlantingZoneAssignment(
            id="saved-zone",
            label="Существующая посадка",
            geometry=mapping(box(15, 15, 25, 25)),
        ),
    ]
    project.plan.objects = [PlanObject(id="saved", kind="tree", x=20, y=20, radius=1.6)]
    app.repository.save(project)
    before = app.get(project.id).model_dump(mode="json")
    # После рабочего отступа у каждого фрагмента остаётся квадрат 0,2 × 0,2 м.
    # Его диагональ меньше шага 6 м: две посадки требуют обоих фрагментов.
    # Независимые свидетели проходят общий checker до запуска генератора.
    for y in (2.1, 57.1):
        checked = app.check_placement(
            project.id, PlacementCheckRequest(kind="tree", x=2.1, y=y, radius=1.6)
        )
        assert checked.allowed
    monkeypatch.setattr(
        app.patterns.candidate_generator,
        "generate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Свободный поиск не использует статический пул")
        ),
    )
    preview = app.preview_pattern(
        project.id,
        FillPatternRequest(
            base_plan_version=project.plan.version,
            zone_ids=["narrow"],
            layout="natural",
            placement_mode="count",
            target_count=2,
            spacing_m=6,
            edge_offset_m=2,
        ),
    )
    assert preview.accepted_count == 2
    assert preview.search_stop_reason == "target_reached"
    assert preview.change_set is not None and preview.change_set.can_apply
    assert all(
        result.status == "allowed" for result in preview.change_set.candidate_results
    )
    assert {obj.y < 4.2 for obj in preview.change_set.additions} == {True, False}
    assert app.get(project.id).model_dump(mode="json") == before
