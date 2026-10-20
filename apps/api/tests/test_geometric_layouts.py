"""Целевые раскладки по осям: длинные проходы, компоненты и строгие расстояния."""

from collections.abc import Callable, Sequence
from itertools import count
from math import hypot

import pytest
from shapely.affinity import rotate, translate
from shapely.geometry import MultiPolygon, Point, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry
from test_placement_allocation import application

from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.contracts import GeometrySnapshot
from app.planning.changes.evaluation import PlanEvaluation
from app.planning.contracts import Plan, PlanObjectCreate
from app.planning.patterns.contracts import FillPatternRequest
from app.planning.ports import PatternCandidate
from app.planning.search.geometric_layouts import propose_target_layouts
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project


def layouts(
    region: BaseGeometry,
    spacing: float,
    target: int,
    *,
    max_layouts: int = 4,
    max_segments: int = 256,
    check_cancelled: Callable[[], None] = lambda: None,
) -> list[list[PatternCandidate]]:
    return propose_target_layouts(
        region,
        spacing,
        target,
        max_layouts=max_layouts,
        max_segments=max_segments,
        check_cancelled=check_cancelled,
    )


def assert_valid(
    region: BaseGeometry, points: Sequence[PatternCandidate], spacing: float
) -> None:
    assert all(region.covers(Point(p.x, p.y)) for p in points)
    assert len({(p.x, p.y) for p in points}) == len(points)
    assert all(
        hypot(a.x - b.x, a.y - b.y) >= spacing
        for i, a in enumerate(points)
        for b in points[i + 1 :]
    )


def test_target_aware_axis_places_twelve_in_the_long_narrow_counterexample() -> None:
    # Это уже область центров после отступов от исходного участка103.4×3.4.
    region = box(1.6, 1.6, 101.8, 1.8)
    result = layouts(region, 8, 12)
    assert len(result[0]) == 12
    assert len(result[0]) <= 12
    assert_valid(region, result[0], 8)
    assert {round(p.y, 8) for p in result[0]} == {1.7}
    assert result[0][-1].x - result[0][0].x >= 88


@pytest.mark.parametrize("angle,offset", [(0, 0), (31, 0), (31, 10_000_000)])
def test_first_wide_natural_layout_uses_two_dimensions(angle: int, offset: int) -> None:
    region = translate(rotate(box(0, 0, 60, 40), angle), offset, -offset)
    first = layouts(region, 6, 4)[0]
    assert len(first) == 4
    assert_valid(region, first, 6)
    # «Без рядов» не должен превращать свободный широкий участок в один ряд.
    # Существенная площадь исключает и формальный субпиксельный сдвиг ряда.
    assert Polygon([(p.x, p.y) for p in first]).convex_hull.area > 6**2


def test_natural_wide_public_preview_keeps_count_checks_and_irregular_shape() -> None:
    app, project = application()
    area = box(0, 0, 60, 40)
    project.planting_zones = [
        PlantingZoneAssignment(id="wide", label="Участок", geometry=mapping(area))
    ]
    app.repository.save(project)
    assert project.plan is not None
    before = project.plan.model_dump()
    preview = app.preview_pattern(
        project.id,
        FillPatternRequest(
            base_plan_version=project.plan.version,
            zone_ids=["wide"],
            layout="natural",
            placement_mode="count",
            target_count=4,
            spacing_m=6,
            seed=47,
        ),
    )
    assert preview.accepted_count == 4
    assert preview.search_stop_reason == "target_reached"
    assert preview.change_set is not None and preview.change_set.can_apply
    additions = preview.change_set.additions
    assert Polygon([(p.x, p.y) for p in additions]).convex_hull.area > 6**2
    working = project.plan.model_copy(deep=True)
    for item in additions:
        checked, issue = app.evaluation.preview_addition(
            project,
            working,
            PlanObjectCreate(kind=item.kind, x=item.x, y=item.y),
        )
        assert issue is None
        working.objects.append(checked)
    assert project.plan.model_dump() == before
    retained = app.get(project.id)
    assert retained.plan is not None and retained.plan.model_dump() == before


def test_natural_displacement_preserves_every_original_spacing_reservation() -> None:
    # Несколько плотно занятых сечений: сдвиг нельзя проверять только против
    # уже перемещённых центров, забывая будущие станции соседнего сечения.
    region = Polygon(
        box(0, 0, 40, 40).exterior.coords,
        [box(17, 17, 23, 23).exterior.coords],
    )
    result = layouts(region, 6, 20)
    assert any(len(points) == 20 for points in result)
    for points in result:
        assert_valid(region, points, 6)


def test_natural_displacement_budget_keeps_the_already_calculated_target() -> None:
    region = box(0, 0, 60, 40)
    # Компонент, сечение и интервал уже рассчитали N. Истощение дополнительного
    # бюджета перестановки не должно удалять ни одну исходную станцию.
    first = layouts(region, 6, 4, max_layouts=1, max_segments=4)[0]
    assert len(first) == 4
    assert_valid(region, first, 6)


@pytest.mark.parametrize("angle", [17, 37, 83, 141])
def test_rotated_narrow_corridor_has_a_complete_layout(angle: int) -> None:
    region = rotate(box(1.6, 1.6, 101.8, 1.8), angle, origin=(0, 0))
    result = layouts(region, 8, 12)
    assert max(map(len, result)) == 12
    for points in result:
        assert_valid(region, points, 8)


def test_translated_cad_coordinates_keep_the_narrow_layout_and_spacing() -> None:
    region = translate(rotate(box(0, 0, 100.2, 0.2), 31, origin=(0, 0)), 1e7, -2e7)
    result = layouts(region, 8, 12)
    assert max(map(len, result)) == 12
    for points in result:
        assert_valid(region, points, 8)


def test_disconnected_islands_share_the_target_by_segment_capacity() -> None:
    parts = [box(0, 0, 16, 1), box(100, 0, 116, 1)]
    region = MultiPolygon(parts)
    result = layouts(region, 8, 6)
    complete = next(points for points in result if len(points) == 6)
    assert_valid(region, complete, 8)
    assert [sum(part.covers(Point(p.x, p.y)) for p in complete) for part in parts] == [
        3,
        3,
    ]


def test_hole_requires_another_geometric_phase_not_random_density() -> None:
    region = Polygon(
        [(0, 0), (20, 0), (20, 20), (0, 20)],
        holes=[[(3, 3), (17, 3), (17, 17), (3, 17)]],
    )
    result = layouts(region, 2, 12)
    assert len(result[0]) < 12
    assert max(map(len, result)) == 12
    for points in result:
        assert_valid(region, points, 2)


def test_close_components_do_not_create_cross_component_spacing_conflicts() -> None:
    region = MultiPolygon([box(0, 0, 2, 2), box(2.1, 0, 4.1, 2)])
    result = layouts(region, 3, 4)
    assert result
    assert all(len(points) < 4 for points in result)
    for points in result:
        assert_valid(region, points, 3)


def test_request_is_constructed_without_enumerating_a_dense_full_grid() -> None:
    region = box(0, 0, 1_000_000, 1_000_000)
    checks = 0

    def check() -> None:
        nonlocal checks
        checks += 1

    result = layouts(
        region, 0.001, 12, max_layouts=1, max_segments=16, check_cancelled=check
    )
    assert len(result) == 1 and len(result[0]) == 12
    assert_valid(region, result[0], 0.001)
    assert checks < 100


def test_output_count_is_bounded_by_target_and_segment_budget() -> None:
    region = box(0, 0, 100, 100)
    result = layouts(region, 1, 5000, max_layouts=2, max_segments=8)
    assert len(result) <= 2
    assert all(len(points) <= 5000 for points in result)
    for points in result:
        assert_valid(region, points, 1)


def test_partial_anchors_remain_available_when_requested_count_does_not_fit() -> None:
    region = box(0, 0, 1, 1)
    result = layouts(region, 8, 12)
    assert result and all(0 < len(points) < 12 for points in result)
    for points in result:
        assert_valid(region, points, 8)


def test_no_second_normative_offset_is_applied_to_a_thin_center_region() -> None:
    region = box(0, 0, 100, 0.000001)
    result = layouts(region, 8, 12)
    assert max(map(len, result)) == 12
    for points in result:
        assert_valid(region, points, 8)


def test_same_geometry_and_target_produce_identical_layouts_without_mutation() -> None:
    region = Polygon([(0, 0), (20, 0), (20, 4), (8, 4), (8, 12), (0, 12)])
    before = region.wkb
    first = layouts(region, 2, 12)
    assert first == layouts(region, 2, 12)
    assert region.wkb == before


def test_cancellation_is_not_hidden_as_an_empty_region() -> None:
    def cancel() -> None:
        raise RuntimeError("Отмена")

    with pytest.raises(RuntimeError, match="Отмена"):
        layouts(box(0, 0, 20, 20), 2, 12, check_cancelled=cancel)


def test_empty_region_returns_no_layout() -> None:
    assert layouts(Polygon(), 2, 12) == []


@pytest.mark.parametrize(
    "spacing,target", [(0, 12), (float("nan"), 12), (1, 0), (1, 5001)]
)
def test_invalid_request_is_rejected(spacing: float, target: int) -> None:
    with pytest.raises(ValueError):
        layouts(box(0, 0, 10, 10), spacing, target)


def test_split_interval_centering_keeps_a_tight_cross_gap_pair() -> None:
    region = Polygon(
        [(0, 0), (10, 0), (10, 2), (0, 2)],
        holes=[[(9, 0.1), (9.1, 0.1), (9.1, 1.9), (9, 1.9)]],
    )
    # Середины двух интервалов нарушили бы шаг10. Допустимая пара требует
    # крайних координат: дополнительного обязательного отступа не вводим.
    first = layouts(region, 10, 2)[0]
    assert len(first) == 2
    assert_valid(region, first, 10)
    assert [point.x for point in first] == [0, 10]


def test_first_split_section_layout_keeps_a_valid_pair_inside_buffer_boundaries() -> (
    None
):
    area = box(0, 0, 30, 20)
    building = box(14.8, 9.2, 15.2, 9.6)
    project = Project(
        name="Робастность станций сечения",
        plan=Plan(),
        planting_zones=[
            PlantingZoneAssignment(id="zone", label="Участок", geometry=mapping(area))
        ],
        geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"kind": "site_border"},
                        "geometry": mapping(box(-10, -10, 40, 30)),
                    },
                    {
                        "type": "Feature",
                        "properties": {"kind": "building"},
                        "geometry": mapping(building),
                    },
                ],
            }
        ),
    )
    assert project.plan is not None
    before = project.model_dump()
    engine = ShapelyGeometryEngine()
    ordinal = count()
    evaluation = PlanEvaluation(engine, lambda: f"test-{next(ordinal)}")
    region = shape(engine.automatic_safe_geometry(project, mapping(area), 1.6, "tree"))
    # Достижимость N подтверждается независимо обычным итоговым оценщиком.
    witness = project.plan.model_copy(deep=True)
    for x, y in [(6, 10), (24, 10)]:
        checked, issue = evaluation.preview_addition(
            project, witness, PlanObjectCreate(kind="tree", x=x, y=y)
        )
        assert issue is None
        witness.objects.append(checked)
    assert len(witness.objects) == 2
    first = layouts(region, 12, 2)[0]
    assert len(first) == 2
    assert_valid(region, first, 12)
    checked_plan = project.plan.model_copy(deep=True)
    for point in first:
        checked, issue = evaluation.preview_addition(
            project,
            checked_plan,
            PlanObjectCreate(kind="tree", x=point.x, y=point.y),
        )
        assert issue is None
        checked_plan.objects.append(checked)
    assert len(checked_plan.objects) == 2
    assert project.model_dump() == before
