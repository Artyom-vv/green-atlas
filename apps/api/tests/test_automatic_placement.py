from __future__ import annotations

from typing import Literal

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box, mapping
from test_placement_allocation import application

from app.automatic_placement import (
    AutomaticPlacementRequest,
    preview_automatic_placement,
)
from app.composition import get_application
from app.main import app as http_app
from app.planning.changes.contracts import (
    PlanChangeSetApplyRequest,
    PlanChangeSetDraft,
    PlanObjectAddOperation,
)
from app.planning.contracts import PlanObjectCreate
from app.planning.domain import PlanVersionConflict
from app.planning.patterns.contracts import (
    FillPatternRequest,
    PatternPreview,
    PatternPreviewRequest,
    PlacementMaskRequest,
)
from app.planning.search.contracts import SearchDomain
from app.shared.failures import DomainFailure, FailureReason
from app.species.contracts import SpeciesShortlistItem


def domain_preview(*, pending: bool) -> PatternPreview:
    """Настоящий контракт незавершённой области без выдуманных кандидатов."""
    return PatternPreview(
        pattern_id="pending-pattern",
        type="fill",
        requested_count=8,
        accepted_count=0,
        search_domains=[
            SearchDomain(
                zone_id="west",
                revision="native-basis",
                geometry=mapping(box(0, 0, 200, 200)),
                unresolved_geometry=mapping(box(0, 0, 0, 0)),
                available_area_m2=0,
                excluded_area_m2=0,
                unresolved_area_m2=0,
                pending_area_m2=40000 if pending else 0,
                method="hybrid",
                final_check="prepared_geometry",
                measured_cells=0,
                processed_objects=256,
                total_objects=512 if pending else 256,
                elapsed_s=0.2,
                stop_reason="time_limit" if pending else "resolution",
            )
        ],
    )


@pytest.mark.parametrize("kind", ["tree", "shrub", "mixed"])
def test_automatic_keeps_pending_domain_instead_of_claiming_zero_capacity(
    kind: Literal["tree", "shrub", "mixed"], monkeypatch: pytest.MonkeyPatch
) -> None:
    service, project = application()
    assert project.plan is not None
    pending = domain_preview(pending=True)
    calls: list[PatternPreviewRequest] = []

    def waiting(_project_id: str, request: PatternPreviewRequest) -> PatternPreview:
        calls.append(request)
        return pending

    monkeypatch.setattr(service, "preview_pattern", waiting)
    result = preview_automatic_placement(
        service,
        project.id,
        AutomaticPlacementRequest(
            base_plan_version=project.plan.version, zone_id="west", plant_kind=kind
        ),
    )
    assert result.found == result.shortfall == 0
    assert result.search_domains == pending.search_domains
    assert result.change_set is None
    assert result.species_revision_ids == []
    assert len(calls) == 1
    unchanged = service.get(project.id)
    assert unchanged.plan is not None and unchanged.plan.objects == []


@pytest.mark.parametrize("pending_kind", ["tree", "shrub"])
@pytest.mark.parametrize("same_revision", [False, True])
def test_automatic_waits_for_pending_alternative_even_with_usable_completed_places(
    pending_kind: Literal["tree", "shrub"],
    same_revision: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project = application()
    assert project.plan is not None
    pending = domain_preview(pending=True)
    pending.search_domains[0].revision = (
        "shared-native-basis" if same_revision else f"{pending_kind}-basis"
    )
    preview = service.preview_pattern
    calls: list[str] = []
    observed: list[SearchDomain] = []

    def alternative(project_id: str, request: PatternPreviewRequest) -> PatternPreview:
        calls.append(request.plant_kind)
        if request.plant_kind == pending_kind:
            observed.extend(pending.search_domains)
            return pending
        # Дерево-предшественник не заполняет запрос: второй вид всё ещё нужен.
        complete_request = request.model_copy(
            update={"target_count": 1 if pending_kind == "shrub" else 8}
        )
        result = preview(project_id, complete_request)
        assert result.change_set is not None and result.change_set.can_apply
        result.search_domains = domain_preview(pending=False).search_domains
        result.search_domains[0].revision = (
            "shared-native-basis" if same_revision else f"{request.plant_kind}-basis"
        )
        result.search_domains[0].processed_objects = 512
        result.search_domains[0].total_objects = 512
        observed.extend(result.search_domains)
        return result

    monkeypatch.setattr(service, "preview_pattern", alternative)
    result = preview_automatic_placement(
        service,
        project.id,
        AutomaticPlacementRequest(
            base_plan_version=project.plan.version, zone_id="west", plant_kind="auto"
        ),
    )
    assert calls == ["tree", "shrub"]
    assert result.change_set is None and result.species_revision_ids == []
    assert result.found == result.shortfall == 0
    assert result.search_domains == observed
    assert [domain.revision for domain in result.search_domains] == (
        ["shared-native-basis", "shared-native-basis"]
        if same_revision
        else ["tree-basis", "shrub-basis"]
    )
    assert sorted(domain.processed_objects for domain in result.search_domains) == [
        256,
        512,
    ]
    unchanged = service.get(project.id)
    assert unchanged.plan is not None and unchanged.plan.objects == []


def test_automatic_completed_empty_domain_stays_a_final_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project = application()
    assert project.plan is not None
    completed = domain_preview(pending=False)
    monkeypatch.setattr(service, "preview_pattern", lambda *_args: completed)
    result = preview_automatic_placement(
        service,
        project.id,
        AutomaticPlacementRequest(
            base_plan_version=project.plan.version, zone_id="west", plant_kind="shrub"
        ),
    )
    assert result.search_domains == completed.search_domains
    assert result.found == 0 and result.shortfall == 8
    assert result.change_set is None


def test_building_tree_automatic_keeps_pending_domain_through_recommendation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project = application()
    assert project.geometry is not None and project.plan is not None
    project.geometry.feature_collection["features"].append(
        {
            "type": "Feature",
            "properties": {"kind": "building"},
            "geometry": mapping(box(75, 75, 125, 125)),
        }
    )
    service.repository.save(project)
    pending = domain_preview(pending=True)
    monkeypatch.setattr(service, "preview_pattern", lambda *_args: pending)
    result = preview_automatic_placement(
        service,
        project.id,
        AutomaticPlacementRequest(
            base_plan_version=project.plan.version,
            zone_id="west",
            near="building",
            plant_kind="tree",
        ),
    )
    assert result.found == result.shortfall == 0
    assert result.search_domains == pending.search_domains
    assert result.change_set is None


def test_http_automatic_pending_reply_keeps_the_real_queue_and_no_apply_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project = application()
    assert project.plan is not None
    pending = domain_preview(pending=True)
    monkeypatch.setattr(service, "preview_pattern", lambda *_args: pending)
    http_app.dependency_overrides[get_application] = lambda: service
    try:
        response = TestClient(http_app).post(
            f"/api/projects/{project.id}/plan/automatic/preview",
            json={
                "base_plan_version": project.plan.version,
                "zone_id": "west",
                "plant_kind": "shrub",
            },
        )
    finally:
        http_app.dependency_overrides.pop(get_application, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["search_domains"] == [
        domain.model_dump(mode="json") for domain in pending.search_domains
    ]
    assert body["found"] == body["shortfall"] == 0
    assert body["change_set"] is None and body["species_revision_ids"] == []
    unchanged = service.get(project.id)
    assert unchanged.plan is not None and unchanged.plan.objects == []


def test_automatic_building_placement_chooses_species_and_requires_apply() -> None:
    service, project = application()
    assert project.geometry is not None
    assert project.plan is not None
    project.geometry.feature_collection["features"].append(
        {
            "type": "Feature",
            "properties": {"kind": "building"},
            "geometry": mapping(box(75, 75, 125, 125)),
        }
    )
    service.repository.save(project)
    request = AutomaticPlacementRequest(
        base_plan_version=project.plan.version,
        zone_id="west",
        near="building",
        plant_kind="auto",
        target_count=8,
    )
    result = preview_automatic_placement(service, project.id, request)
    assert result.selected_kind == "shrub"
    assert result.found == 8
    assert result.species_revision_ids
    assert result.change_set is not None
    assert result.change_set.can_apply
    unchanged = service.get(project.id)
    assert unchanged.plan is not None
    assert unchanged.plan.objects == []

    change = result.change_set
    service.apply_change_set(
        project.id,
        PlanChangeSetApplyRequest(
            preview_id=change.id,
            digest=change.digest,
            base_plan_version=change.base_plan_version,
        ),
    )
    applied = service.get(project.id)
    assert applied.plan is not None
    assert len(applied.plan.objects) == 8


def test_automatic_preview_rejects_stale_plan_and_missing_zone() -> None:
    service, project = application()
    assert project.plan is not None
    with pytest.raises(PlanVersionConflict):
        preview_automatic_placement(
            service,
            project.id,
            AutomaticPlacementRequest(
                base_plan_version=project.plan.version + 1,
                zone_id="west",
            ),
        )
    with pytest.raises(DomainFailure) as failure:
        preview_automatic_placement(
            service,
            project.id,
            AutomaticPlacementRequest(
                base_plan_version=project.plan.version,
                zone_id="missing",
            ),
        )
    assert failure.value.reason is FailureReason.ZONE_MISSING
    assert failure.value.params == {"zone_ids": ["missing"]}


def test_http_auto_preview_uses_the_same_service_without_saving() -> None:
    service, project = application()
    assert project.plan is not None
    http_app.dependency_overrides[get_application] = lambda: service
    try:
        response = TestClient(http_app).post(
            f"/api/projects/{project.id}/plan/automatic/preview",
            json={
                "base_plan_version": project.plan.version,
                "zone_id": "west",
                "near": "area",
                "plant_kind": "auto",
                "target_count": 3,
            },
        )
    finally:
        http_app.dependency_overrides.pop(get_application, None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["found"] == 3
    assert body["selected_kind"] == "tree"
    assert body["change_set"]["can_apply"] is True
    unchanged = service.get(project.id)
    assert unchanged.plan is not None
    assert unchanged.plan.objects == []


@pytest.mark.parametrize(
    "size,near,kind,selected,found,first_position",
    [
        (200, "area", "auto", "tree", 8, (163.5, 100.0)),
        (200, "area", "tree", "tree", 8, (163.5, 100.0)),
        (200, "area", "shrub", "shrub", 8, (37.2, 100.0)),
        (200, "area", "mixed", "mixed", 8, (163.5, 100.0)),
        (200, "road", "auto", "tree", 8, (5.504642, 4.1)),
        (200, "road", "tree", "tree", 8, (5.504642, 4.1)),
        (200, "road", "shrub", "shrub", 8, (3.904007, 3.0)),
        (200, "road", "mixed", "mixed", 8, (5.504642, 4.1)),
        (200, "building", "auto", "shrub", 8, (123.919954, 72.451807)),
        (200, "building", "tree", "tree", 8, (122.535223, 69.956023)),
        (200, "building", "shrub", "shrub", 8, (123.919954, 72.451807)),
        (200, "building", "mixed", "mixed", 8, (122.521129, 68.956023)),
        (22, "area", "auto", "shrub", 30, (18.175, 11.0)),
        (22, "area", "tree", "tree", 0, None),
        (22, "area", "shrub", "shrub", 30, (18.175, 11.0)),
        (22, "area", "mixed", "mixed", 0, None),
        (22, "road", "auto", "shrub", 6, (3.454007, 3.0)),
        (22, "road", "tree", "tree", 0, None),
        (22, "road", "shrub", "shrub", 6, (3.454007, 3.0)),
        (22, "road", "mixed", "mixed", 0, None),
        (22, "building", "auto", "shrub", 17, (12.632083, 5.701807)),
        (22, "building", "tree", "tree", 10, (11.065547, 3.206023)),
        (22, "building", "shrub", "shrub", 17, (12.632083, 5.701807)),
        (22, "building", "mixed", "mixed", 0, None),
    ],
)
def test_automatic_context_capacity_and_coordinate_baseline(
    size: int,
    near: Literal["area", "road", "building"],
    kind: Literal["auto", "tree", "shrub", "mixed"],
    selected: Literal["tree", "shrub", "mixed"],
    found: int,
    first_position: tuple[float, float] | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сохраняет статические схемы и проверяет новый поиск по количеству.

    У естественной раскладки прежнее найденное число остаётся нижней границей,
    а старые координаты и число проб смешанного состава не являются контрактом.
    Для дорожной и пристенной схем прежние точные ожидания сохраняются.
    """
    service, project = application()
    assert project.geometry is not None
    assert project.plan is not None
    project.planting_zones[0].geometry = mapping(box(0, 0, size, size))
    project.geometry.feature_collection["features"].extend(
        [
            {
                "type": "Feature",
                "properties": {"kind": "building"},
                "geometry": mapping(
                    box(size * 0.375, size * 0.375, size * 0.625, size * 0.625)
                ),
            },
            {
                "type": "Feature",
                "properties": {"kind": "road"},
                "geometry": mapping(box(-10, -5, size + 10, 0)),
            },
        ]
    )
    service.repository.save(project)
    before = service.get(project.id).model_dump()
    seeds: list[int] = []
    preview = service.preview_pattern

    def record_pattern(
        project_id: str, request: PatternPreviewRequest
    ) -> PatternPreview:
        assert isinstance(request, (FillPatternRequest, PlacementMaskRequest))
        seeds.append(request.seed)
        return preview(project_id, request)

    monkeypatch.setattr(service, "preview_pattern", record_pattern)
    quantity = 8 if size == 200 else 30
    request = AutomaticPlacementRequest(
        scenario_horizon_year=20,
        base_plan_version=project.plan.version,
        zone_id="west",
        near=near,
        plant_kind=kind,
        target_count=quantity,
    )
    result = preview_automatic_placement(service, project.id, request)
    if near == "area":
        assert result.found >= found, "Новый поиск потерял ранее найденные места"
        assert result.requested == quantity
        assert 0 <= result.found <= quantity
        assert result.shortfall == quantity - result.found
        assert result.planning_horizon_year == 20
        assert result.selected_kind == selected
        first_seeds = list(seeds)
        assert first_seeds == (
            list(range(1, len(first_seeds) + 1))
            if kind == "mixed"
            else [1] * len(first_seeds)
        )
        assert len(first_seeds) <= (8 if kind == "mixed" else 2)
        seeds.clear()
        repeated = preview_automatic_placement(service, project.id, request)
        assert seeds == first_seeds
        assert (
            repeated.selected_kind,
            repeated.requested,
            repeated.found,
            repeated.shortfall,
            repeated.species_revision_ids,
        ) == (
            result.selected_kind,
            result.requested,
            result.found,
            result.shortfall,
            result.species_revision_ids,
        )
        repeated_additions = (
            repeated.change_set.additions if repeated.change_set else []
        )
        first_additions = result.change_set.additions if result.change_set else []
        assert [
            item.model_dump(exclude={"id", "pattern_id", "group_ids"})
            for item in repeated_additions
        ] == [
            item.model_dump(exclude={"id", "pattern_id", "group_ids"})
            for item in first_additions
        ]
    else:
        assert (result.selected_kind, result.found, result.shortfall) == (
            selected,
            found,
            quantity - found,
        )
    unchanged = service.get(project.id)
    assert unchanged.plan is not None
    assert unchanged.plan.objects == []
    if kind == "mixed" and near != "area":
        expected_seeds = (
            [1, 2]
            if (size, near) == (200, "area")
            else [1]
            if found
            else list(range(1, 9))
        )
        assert seeds == expected_seeds
    assert unchanged.model_dump() == before
    if not result.found:
        assert result.change_set is None
        assert result.species_revision_ids == []
        return
    assert result.change_set is not None
    assert result.change_set.can_apply
    additions = result.change_set.additions
    assert len(additions) == result.found
    if near != "area":
        assert (additions[0].x, additions[0].y) == first_position
    expected_species = {
        "tree": "sorbus-aucuparia@2026-08-28.1",
        "shrub": "spiraea-japonica@2026-08-28.1",
    }
    assert all(
        item.species_revision_id == expected_species[item.kind] for item in additions
    )
    assert all(item.planting_zone_id == "west" for item in additions)
    for candidate in result.change_set.candidate_results:
        assert candidate.rule_trace is not None
        assert candidate.rule_trace.basis.state_version == project.state_version
        assert any(
            entry.code == "NO_NETWORK_FEATURES" and entry.status == "not_checked"
            for entry in candidate.rule_trace.entries
        )
    if near == "area":
        # Повторяем общий итоговый допуск всего набора, включая взаимные
        # расстояния и прогнозный горизонт; предложения генератора не доверяем.
        checked = service.preview_change_set(
            project.id,
            PlanChangeSetDraft(
                scenario_horizon_year=20,
                base_plan_version=project.plan.version,
                source="pattern",
                label="Независимая проверка естественной раскладки",
                operations=[
                    PlanObjectAddOperation(
                        object=PlanObjectCreate.model_validate(item.model_dump())
                    )
                    for item in additions
                ],
            ),
        )
        assert checked.can_apply
        assert len(checked.additions) == result.found
        assert len(checked.candidate_results) == result.found
        assert service.get(project.id).model_dump() == before


def test_automatic_preview_rejects_project_change_during_species_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, project = application()
    assert project.plan is not None
    shortlist = service.shortlist_species

    def concurrent_selection(
        project_id: str,
        object_ids: list[str],
        zone_ids: list[str] | None = None,
        kind: str | None = None,
    ) -> list[SpeciesShortlistItem]:
        options = shortlist(project_id, object_ids, zone_ids, kind)
        changed = service.get(project_id)
        changed.name = "Changed while choosing species"
        service.repository.save(changed)
        return options

    monkeypatch.setattr(service, "shortlist_species", concurrent_selection)
    with pytest.raises(ValueError, match="Проект изменился во время расчёта"):
        preview_automatic_placement(
            service,
            project.id,
            AutomaticPlacementRequest(
                base_plan_version=project.plan.version, zone_id="west"
            ),
        )
    unchanged = service.get(project.id)
    assert unchanged.plan is not None
    assert unchanged.plan.objects == []
