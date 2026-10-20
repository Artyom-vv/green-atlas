from collections import Counter
from typing import Literal

import pytest
from shapely.geometry import Point, box, mapping, shape
from shapely.geometry.base import BaseGeometry
from test_placement_allocation import application

from app.building_screen import generation_features, preview, targets
from app.contracts import BuildingScreenRequest, PlacementMaskRequest, PlanObject
from app.planning.domain import required_spacing
from app.planning.patterns.generators import (
    generate_building_contour,
    generate_building_screen,
)
from app.shared.error_messages import public_failure_message
from app.shared.failures import DomainFailure, FailureReason
from app.species.catalog import get_species, growth_forecasts


def feature(kind: str, geometry: BaseGeometry) -> dict[str, object]:
    return {
        "type": "Feature",
        "properties": {"kind": kind},
        "geometry": mapping(geometry),
    }


def test_groups_are_in_building_band_and_keep_independent_identity() -> None:
    building = box(80, 80, 120, 120)
    points = generate_building_screen(
        PlacementMaskRequest(
            mask_id="building_screen",
            base_plan_version=1,
            zone_ids=["west"],
            placement_mode="spacing",
            spacing_m=7,
            cluster_size=3,
            cluster_gap_m=24,
        ),
        box(0, 0, 200, 200),
        [feature("building", building)],
    )
    assert len(points) > 3
    assert all(0 < Point(p.x, p.y).distance(building) <= 24.00001 for p in points)
    groups = Counter(p.group_key for p in points)
    assert len(groups) > 1 and None not in groups
    assert max(groups.values()) == 3


def test_road_facing_is_a_subset_not_a_different_full_area_fill() -> None:
    request = PlacementMaskRequest(
        mask_id="building_screen",
        base_plan_version=1,
        zone_ids=["west"],
        placement_mode="spacing",
        spacing_m=7,
        cluster_size=3,
    )
    guides = [
        feature("building", box(80, 80, 120, 120)),
        feature("road", box(155, 0, 160, 200)),
    ]
    road_request = request.model_copy()
    road_request.screen_side = "roads"
    all_points = generate_building_screen(request, box(0, 0, 200, 200), guides)
    road_points = generate_building_screen(road_request, box(0, 0, 200, 200), guides)
    assert 0 < len(road_points) < len(all_points)
    assert set(road_points) <= set(all_points)
    assert all(p.x > 80 for p in road_points)
    with pytest.raises(DomainFailure) as failure:
        generate_building_screen(
            road_request,
            box(0, 0, 200, 200),
            guides[:1],
        )
    assert failure.value.reason is FailureReason.BUILDING_ROW_ROADS_MISSING


def test_proposal_uses_selected_zones_safety_growth_and_does_not_apply() -> None:
    app, project = application()
    assert project.geometry is not None
    project.geometry.feature_collection["features"] += [
        feature("building", box(75, 75, 125, 125)),
        feature("building", box(425, 25, 450, 50)),
    ]
    context = targets(project, ["west", "east"])
    assert context.geometry is not None
    assert context.geometry["type"] == "MultiPolygon"
    project = app.repository.save(project)
    before = project.model_dump_json()
    proposal = preview(
        app,
        project.id,
        BuildingScreenRequest(base_plan_version=1, zone_ids=["west", "east"]),
    )
    assert proposal.arrangement == "building_screen"
    assert proposal.evidence.note == (
        "Группы расположены рядом со зданиями. Проверены ограничения чертежа и рост посадок. "
        "Видимость фасадов из конкретных точек не рассчитывалась."
    )
    assert proposal.change_set and proposal.change_set.can_apply
    additions = proposal.change_set.additions
    assert any(p.x < 200 for p in additions) and any(p.x > 400 for p in additions)
    assert all(c.status == "allowed" for c in proposal.change_set.candidate_results)
    assert all(p.canopy_forecast and p.root_forecast for p in additions)
    assert len({tuple(p.group_ids) for p in additions}) > 1
    assert min(Counter(tuple(p.group_ids) for p in additions).values()) >= 2
    assert app.get(project.id).model_dump_json() == before

    limited = preview(
        app,
        project.id,
        BuildingScreenRequest(
            base_plan_version=1, zone_ids=["west", "east"], max_sites=4
        ),
    )
    assert limited.change_set is not None
    assert 2 <= len(limited.change_set.additions) <= 4
    assert (
        min(Counter(tuple(p.group_ids) for p in limited.change_set.additions).values())
        >= 2
    )


def test_missing_buildings_and_unknown_zones_do_not_become_generic_fills() -> None:
    app, project = application()
    before = project.model_dump_json()
    with pytest.raises(DomainFailure) as buildings_failure:
        preview(
            app,
            project.id,
            BuildingScreenRequest(base_plan_version=1, zone_ids=["west"]),
        )
    assert (
        buildings_failure.value.reason
        is FailureReason.BUILDING_SCREEN_BUILDINGS_MISSING
    )
    assert public_failure_message(buildings_failure.value) == (
        "Рядом с выбранными участками не найдены контуры зданий. Выберите другие участки."
    )
    with pytest.raises(DomainFailure) as zones_failure:
        targets(project, ["missing"])
    assert zones_failure.value.reason is FailureReason.BUILDING_SCREEN_ZONES_INVALID
    assert public_failure_message(zones_failure.value) == (
        "Выберите существующие рабочие участки."
    )
    assert project.model_dump_json() == before


@pytest.mark.parametrize("zone_ids", [[], ["west", "missing"]])
def test_invalid_building_zone_selection_remains_an_addressed_refusal(
    zone_ids: list[str],
) -> None:
    _app, project = application()
    with pytest.raises(DomainFailure) as failure:
        targets(project, zone_ids)
    assert failure.value.reason is FailureReason.BUILDING_SCREEN_ZONES_INVALID


@pytest.mark.parametrize(
    "kind,layout_radius_m,clearance_m",
    [
        ("tree", None, 3.26),
        ("shrub", None, 1.66),
        ("tree", 2.3, 3.96),
        ("shrub", 1.2, 2.21),
    ],
)
def test_building_search_uses_the_same_plant_footprint_as_the_plan(
    kind: Literal["tree", "shrub"],
    layout_radius_m: float | None,
    clearance_m: float,
) -> None:
    _app, project = application()
    assert project.plan is not None
    project.plan.objects = [PlanObject(kind="shrub", x=50, y=50, radius=0.65)]
    before = project.model_dump_json()
    features = generation_features(
        project,
        PlacementMaskRequest(
            mask_id="building_contour",
            base_plan_version=1,
            zone_ids=["west"],
            plant_kind=kind,
            layout_radius_m=layout_radius_m,
        ),
    )
    occupied = shape(features[-1]["geometry"])
    assert occupied.bounds == pytest.approx(
        (50 - clearance_m, 50 - clearance_m, 50 + clearance_m, 50 + clearance_m)
    )
    # Двухметровый отступ свободен для малого кустарника и занят для дерева.
    assert occupied.contains(Point(52, 50)) is (clearance_m > 2)
    assert project.model_dump_json() == before


def test_planned_trees_shape_the_candidate_search_before_validation() -> None:
    app, project = application()
    assert project.geometry is not None
    assert project.plan is not None
    project.geometry.feature_collection["features"].append(
        feature("building", box(75, 75, 125, 125))
    )
    existing = PlanObject(kind="tree", x=145, y=100, radius=8)
    project.plan.objects = [existing]
    species_id = "tilia-cordata@2026-08-28.1"
    request = PlacementMaskRequest(
        mask_id="building_screen",
        base_plan_version=1,
        zone_ids=["west"],
        species_revision_id=species_id,
        placement_mode="spacing",
        cluster_size=3,
        spacing_m=12,
    )
    guides = generation_features(project, request)
    points = generate_building_screen(request, box(0, 0, 200, 200), guides)
    prototype = PlanObject(kind="tree", x=0, y=0, radius=1.6)
    prototype.canopy_forecast, prototype.root_forecast = growth_forecasts(
        get_species(species_id), "standard"
    )
    assert points
    assert all(
        Point(p.x, p.y).distance(Point(existing.x, existing.y))
        >= required_spacing(prototype, existing)
        for p in points
    )


def test_contour_tracks_fixed_offset_instead_of_filling_band() -> None:
    building = box(80, 80, 120, 120)
    request = PlacementMaskRequest(
        mask_id="building_contour",
        base_plan_version=1,
        zone_ids=["west"],
        building_offset_m=10,
        spacing_m=7,
    )
    points = generate_building_contour(
        request,
        box(0, 0, 200, 200).difference(building.buffer(8)),
        [feature("building", building)],
    )
    assert len(points) > 10
    assert all(abs(Point(p.x, p.y).distance(building) - 10) < 0.01 for p in points)
    assert (
        generate_building_contour(
            request,
            box(0, 0, 200, 200).difference(building.buffer(12)),
            [feature("building", building)],
        )
        == []
    )


def test_contour_uses_oak_growth_and_preserves_real_requested_species() -> None:
    app, project = application()
    assert project.geometry is not None
    building = box(75, 75, 125, 125)
    project.geometry.feature_collection["features"].append(
        feature("building", building)
    )
    project = app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    proposal = preview(
        app,
        project.id,
        BuildingScreenRequest(
            base_plan_version=1,
            zone_ids=["west"],
            arrangement="contour",
            species_revision_id="quercus-robur@2026-08-28.1",
            max_sites=100,
        ),
    )
    assert proposal.change_set and proposal.change_set.can_apply
    assert 0 < len(proposal.change_set.additions) <= 100
    assert proposal.evidence.note == (
        "Посадки следуют контурам зданий. Проверены ограничения чертежа и рост посадок. "
        "Видимость фасадов из конкретных точек не рассчитывалась."
    )
    assert all(
        p.species_revision_id == "quercus-robur@2026-08-28.1"
        for p in proposal.change_set.additions
    )
    distances = [
        Point(p.x, p.y).distance(building) for p in proposal.change_set.additions
    ]
    assert max(distances) - min(distances) < 0.03
    assert all(
        p.canopy_forecast and p.root_forecast for p in proposal.change_set.additions
    )
    assert app.get(project.id).model_dump_json() == before
