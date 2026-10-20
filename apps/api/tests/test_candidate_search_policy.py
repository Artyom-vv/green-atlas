"""Границы статических предложений и геометрического поиска без CAD и записи плана."""

from unittest.mock import patch

import pytest
from test_placement_allocation import application

from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.planning.changes.evaluation import PlanEvaluation
from app.planning.patterns.contracts import FillPatternRequest, PlacementMaskRequest
from app.planning.search.candidate_search import (
    CandidateSearchResult,
    search_budget,
    search_candidates,
)
from app.planning.search.geometric_search import GeometricSearchResult
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason


@pytest.mark.parametrize(
    ("target", "expected"),
    [(1, 2048), (16, 2048), (17, 2176), (39, 4992), (40, 5000), (5000, 5000)],
)
def test_legacy_finite_queue_keeps_its_probe_budget(target: int, expected: int) -> None:
    assert search_budget(target) == expected


@pytest.mark.parametrize("mode", ["regular", "mask"])
@pytest.mark.parametrize(
    ("target", "alternative_probes"),
    [(1, 8), (17, 136), (40, 320), (626, 5000)],
)
def test_static_pattern_generation_keeps_its_alternative_budget(
    mode: str, target: int, alternative_probes: int
) -> None:
    app, project = application()
    request = (
        PlacementMaskRequest(
            base_plan_version=1,
            zone_ids=["west"],
            mask_id="regular_grid",
            placement_mode="count",
            target_count=target,
            seed=47,
        )
        if mode == "mask"
        else FillPatternRequest(
            base_plan_version=1,
            zone_ids=["west"],
            layout="regular",
            placement_mode="count",
            target_count=target,
            seed=47,
        )
    )
    with patch.object(
        app.patterns.candidate_generator, "generate", return_value=[]
    ) as generate:
        preview = app.preview_pattern(project.id, request)

    generate.assert_called_once()
    generated_request = generate.call_args.args[0]
    assert generated_request.target_count == alternative_probes
    assert generated_request.seed == 47
    assert preview.requested_count == target
    assert preview.accepted_count == 0
    assert request.target_count == target
    retained = app.get(project.id)
    assert retained.plan is not None
    assert retained.plan.objects == []


@pytest.mark.parametrize("target", [1, 17, 40, 626])
def test_natural_count_passes_the_requested_goal_to_geometric_search(
    target: int,
) -> None:
    app, project = application()
    request = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["west"],
        layout="natural",
        placement_mode="count",
        target_count=target,
        seed=47,
    )
    before = app.get(project.id).model_dump(mode="json")
    empty = GeometricSearchResult(
        [], [], [], CandidateSearchResult([], "domain_exhausted", 0), 0
    )
    with (
        patch.object(
            app.patterns.candidate_generator,
            "generate",
            side_effect=AssertionError("Свободный поиск не создаёт статический пул"),
        ),
        patch(
            "app.planning.patterns.application.search_geometric_placement",
            return_value=empty,
        ) as search,
    ):
        preview = app.preview_pattern(project.id, request)

    search.assert_called_once()
    searched_project, searched_request, zones = search.call_args.args[:3]
    assert searched_project.plan.model_dump(mode="json") == before["plan"]
    assert searched_request.model_dump() == request.model_dump()
    assert [zone.id for zone in zones] == ["west"]
    assert preview.requested_count == target
    assert preview.accepted_count == 0
    assert request.target_count == target
    assert app.get(project.id).model_dump(mode="json") == before


def test_search_requires_a_created_plan_before_geometry_is_read() -> None:
    evaluation = PlanEvaluation(ShapelyGeometryEngine(), new_id=lambda: "unused")

    with patch("app.planning.search.candidate_search.prepare_positions") as prepare:
        with pytest.raises(DomainFailure) as failure:
            search_candidates(Project(name="No plan"), [], evaluation, 1, {}, 6)

    assert failure.value.reason is FailureReason.PLAN_MISSING
    assert failure.value.params == {}
    prepare.assert_not_called()
