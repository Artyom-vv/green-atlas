"""Свободный поиск не теряет допустимые центры и проверяет найденные промежутки."""

from itertools import count
from math import cos, inf, nextafter, pi, sin
from random import Random
from unittest.mock import Mock

import pytest
from shapely.errors import GEOSException
from shapely.geometry import Point, Polygon, box, mapping
from test_placement_allocation import application
from test_search_spacing_precheck import SearchGeometry

from app.dxf_import.contracts import SourceFile
from app.geometry.checking.domain import PositionAdvisory
from app.geometry.contracts import GeometrySnapshot
from app.operations.progress import operation_cancellation
from app.planning.changes.evaluation import PlanEvaluation
from app.planning.contracts import Plan, PlanObject, PlanObjectCreate
from app.planning.domain import PlantSpacingIndex, required_spacing
from app.planning.patterns.application import _pattern_id, _pattern_operations
from app.planning.patterns.contracts import (
    FillPatternRequest,
    PlacementMaskRequest,
    RowPatternRequest,
)
from app.planning.search import geometric_search, occupied_centers
from app.planning.search.contracts import SearchDomain
from app.planning.search.geometric_search import search_geometric_placement
from app.planning.search.occupied_centers import unoccupied_centers
from app.planning.search.spacing_precheck import spacing_template
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.contracts import Project
from app.regulations.network_summary import network_review_reason


def saved_project(*, x=0, y=0, horizon=0):
    return Project(
        name="Промежутки между посадками",
        plan=Plan(
            planning_horizon_year=horizon,
            objects=[
                PlanObject(
                    id="saved", kind="tree", x=x, y=y, radius=1.6, group_ids=["old"]
                )
            ],
        ),
    )


def payload(*, kind="tree", group="new", radius=1.6):
    return PlanObjectCreate(
        kind=kind,
        x=0,
        y=0,
        layout_radius_m=radius,
        species_revision_id=(
            "sorbus-aucuparia@2026-08-28.1"
            if kind == "tree"
            else "spiraea-japonica@2026-08-28.1"
        ),
        size_class="standard",
        group_ids=[group],
        spacing_policy="balanced",
    )


@pytest.mark.parametrize("horizon", [0, 20, 40])
@pytest.mark.parametrize("kind", ["tree", "shrub"])
@pytest.mark.parametrize("group", ["old", "new"])
def test_every_exact_allowed_boundary_and_sample_is_retained(horizon, kind, group):
    project = saved_project(horizon=horizon)
    candidate = payload(kind=kind, group=group)
    template = spacing_template(candidate, 1.6)
    usable = box(-50, -50, 50, 50)
    before = project.model_dump(mode="json")
    remaining = unoccupied_centers(project, candidate, usable)
    distance = required_spacing(template, project.plan.objects[0], horizon)
    exact = PlantSpacingIndex(project.plan.objects, planning_horizon_year=horizon)
    assert not remaining.covers(Point(0, 0))
    # Равенство разрешено общим правилом, вписанные круги его не поглощают.
    for x in [distance, nextafter(distance, inf)]:
        assert exact.respects(template.model_copy(update={"x": x}))
        assert remaining.covers(Point(x, 0))
    random = Random(31)
    samples = [(random.uniform(-40, 40), random.uniform(-40, 40)) for _ in range(200)]
    samples += [
        (distance * cos(i * pi / 127), distance * sin(i * pi / 127)) for i in range(254)
    ]
    for x, y in samples:
        if exact.respects(template.model_copy(update={"x": x, "y": y})):
            assert remaining.covers(Point(x, y))
    assert project.model_dump(mode="json") == before


def test_large_explicit_radius_uses_exact_pair_rule():
    project = saved_project()
    candidate = payload(radius=25)
    template = spacing_template(candidate, 25)
    distance = required_spacing(template, project.plan.objects[0], 0)
    remaining = unoccupied_centers(project, candidate, box(-100, -100, 100, 100))
    assert not remaining.covers(Point(distance / 2, 0))
    assert remaining.covers(Point(distance, 0))


def test_polygon_cap_leaves_unprocessed_collision_for_final_checker(monkeypatch):
    project = saved_project()
    project.plan.objects.append(
        PlanObject(id="second", kind="tree", x=20, y=0, radius=1.6)
    )
    monkeypatch.setattr(occupied_centers, "MAX_OCCUPANCY_POLYGONS", 1)
    remaining = unoccupied_centers(project, payload(), box(-10, -10, 30, 10))
    assert not remaining.covers(Point(0, 0))
    assert remaining.covers(Point(20, 0))
    template = spacing_template(payload(), 1.6).model_copy(update={"x": 20})
    assert not PlantSpacingIndex(
        project.plan.objects, planning_horizon_year=0
    ).respects(template)


def test_unsupported_geometry_and_excess_complexity_keep_original_path(monkeypatch):
    project = saved_project()
    assert (
        unoccupied_centers(
            project, payload(), Polygon([(0, 0), (5, 5), (0, 5), (5, 0)])
        )
        is None
    )
    monkeypatch.setattr(occupied_centers, "MAX_OCCUPANCY_COORDINATES", 3)
    assert unoccupied_centers(project, payload(), box(-10, -10, 10, 10)) is None


def test_derived_geos_failure_keeps_original_path(monkeypatch):
    monkeypatch.setattr(
        occupied_centers, "unary_union", Mock(side_effect=GEOSException("test"))
    )
    assert unoccupied_centers(saved_project(), payload(), box(-10, -10, 10, 10)) is None


def test_cancellation_inside_geometry_loop_is_not_swallowed():
    checks = 0

    def cancel():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ValueError("cancelled")

    with operation_cancellation(cancel), pytest.raises(ValueError, match="cancelled"):
        unoccupied_centers(saved_project(), payload(), box(-10, -10, 10, 10))


def test_invalid_species_preserves_original_evaluator_path():
    invalid = payload().model_copy(
        update={"species_revision_id": "not-a-known-revision"}
    )
    assert unoccupied_centers(saved_project(), invalid, box(-10, -10, 10, 10)) is None


def request_and_zone(layout="natural", composition="trees", placement_mode="count"):
    request = FillPatternRequest(
        base_plan_version=1,
        zone_ids=["work"],
        layout=layout,
        composition=composition,
        placement_mode=placement_mode,
        edge_offset_m=2,
        target_count=40,
    )
    geometry = dict(
        mapping(box(0, 0, 40, 40)),
        ga_work_zone=mapping(box(0, 0, 40, 40)),
        ga_search_domain=SearchDomain(
            zone_id="work",
            revision="fixture-basis",
            geometry=mapping(box(0, 0, 40, 40)),
            unresolved_geometry=mapping(Polygon()),
            available_area_m2=1600,
            excluded_area_m2=0,
            unresolved_area_m2=0,
            method="hybrid",
            final_check="prepared_geometry",
            measured_cells=0,
            processed_objects=1,
            total_objects=1,
            source_issues=[{"reason": "source_object", "detail": "preserved"}],
            elapsed_s=0,
            stop_reason="resolution",
        ).model_dump(mode="json", exclude={"zone_id"}),
    )
    zone = PlantingZoneAssignment(id="work", label="Участок", geometry=geometry)
    return request, zone


def geometric_placement(project, request, zone, geometry=None):
    ids = count()
    return search_geometric_placement(
        project,
        request,
        [zone],
        PlanEvaluation(geometry or SearchGeometry(), lambda: f"not-saved-{next(ids)}"),
        request.spacing_m,
        {},
        lambda points, offset: _pattern_operations(
            request, _pattern_id(request), points, index_offset=offset
        ),
    )


def test_geometric_search_conditions_only_ephemeral_geometry_and_insets_work_once(
    monkeypatch,
):
    project = saved_project(x=20, y=20)
    request, zone = request_and_zone()
    request = request.model_copy(update={"target_count": 1})
    project.planting_zones = [zone]
    before = project.model_dump(mode="json")
    condition = Mock(wraps=unoccupied_centers)
    monkeypatch.setattr(geometric_search, "unoccupied_centers", condition)
    sources = Mock(wraps=geometric_search.GeometryCandidateSearch)
    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", sources)
    result = geometric_placement(project, request, zone)

    condition.assert_called_once()
    usable = condition.call_args.args[2]
    assert usable.bounds == (2, 2, 38, 38)
    remaining = sources.call_args.args[0]
    assert remaining.bounds == usable.bounds
    assert not remaining.covers(Point(20, 20))
    assert remaining.covers(Point(2, 20))
    assert result.search.stop_reason == "target_reached"
    assert len(result.search.candidate_results) == 1
    assert result.search.candidate_results[0].status == "allowed"
    assert project.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "scheme", ["regular", "staggered", "natural_spacing", "row", "mask"]
)
def test_other_layouts_keep_the_public_static_generator_path(monkeypatch, scheme):
    request, zone = request_and_zone()
    if scheme in {"regular", "staggered"}:
        request = request.model_copy(update={"layout": scheme})
    elif scheme == "natural_spacing":
        request = request.model_copy(update={"placement_mode": "spacing"})
    elif scheme == "row":
        request = RowPatternRequest(
            base_plan_version=1,
            zone_ids=["work"],
            axis={"type": "LineString", "coordinates": [[0, 0], [40, 40]]},
        )
    elif scheme == "mask":
        request = PlacementMaskRequest(
            base_plan_version=1, zone_ids=["work"], mask_id="regular_grid"
        )
    app, project = application()
    project.planting_zones = [zone]
    app.repository.save(project)
    before = app.get(project.id).model_dump(mode="json")
    # Подготовленная область — отдельная граница; проверяем её передачу выбранной
    # статической схеме, а не повторяем расчёт CAD в проверке маршрутизации.
    prepared = Mock(return_value=[zone])
    monkeypatch.setattr(app.evaluation, "automatic_generation_zones", prepared)
    search = Mock(side_effect=AssertionError("Схема не использует свободный поиск"))
    monkeypatch.setattr(
        "app.planning.patterns.application.search_geometric_placement", search
    )
    generator = Mock(return_value=[])
    monkeypatch.setattr(app.patterns.candidate_generator, "generate", generator)
    preview = app.preview_pattern(project.id, request)

    generator.assert_called_once()
    forwarded = generator.call_args.args[1][0]
    assert forwarded.model_dump(mode="json") == zone.model_dump(mode="json")
    if scheme == "row":
        prepared.assert_not_called()
    else:
        prepared.assert_called_once()
        assert forwarded is zone
    search.assert_not_called()
    assert preview.accepted_count == 0
    assert preview.search_domains[0].source_issues == [
        {"reason": "source_object", "detail": "preserved"}
    ]
    assert app.get(project.id).model_dump(mode="json") == before


def test_mixed_geometric_search_does_not_use_homogeneous_occupancy(monkeypatch):
    project = saved_project(x=20, y=20)
    request, zone = request_and_zone(composition="mixed")
    request = request.model_copy(update={"target_count": 1})
    project.planting_zones = [zone]
    before = project.model_dump(mode="json")
    condition = Mock(side_effect=AssertionError("Смесь проверяется по каждому виду"))
    monkeypatch.setattr(geometric_search, "unoccupied_centers", condition)
    result = geometric_placement(project, request, zone)

    condition.assert_not_called()
    assert result.search.stop_reason == "target_reached"
    assert any(item.status == "allowed" for item in result.search.candidate_results)
    assert project.model_dump(mode="json") == before


def test_no_nearby_saved_plants_retains_the_single_work_inset(monkeypatch):
    project = saved_project(x=1000, y=1000)
    request, zone = request_and_zone()
    request = request.model_copy(update={"target_count": 1})
    project.planting_zones = [zone]
    before = project.model_dump(mode="json")
    sources = Mock(wraps=geometric_search.GeometryCandidateSearch)
    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", sources)
    result = geometric_placement(project, request, zone)

    searched = sources.call_args.args[0]
    assert searched.equals(box(2, 2, 38, 38))
    assert result.search.stop_reason == "target_reached"
    assert project.model_dump(mode="json") == before


@pytest.mark.parametrize("fallback", ["unsupported", "geos_failure"])
def test_conditioning_fallback_retains_the_full_checker_and_work_offset(
    monkeypatch,
    fallback,
):
    project = saved_project(x=20, y=20)
    request, zone = request_and_zone()
    request = request.model_copy(update={"target_count": 1})
    project.planting_zones = [zone]
    before = project.model_dump(mode="json")
    if fallback == "unsupported":
        monkeypatch.setattr(geometric_search, "unoccupied_centers", lambda *args: None)
    else:
        monkeypatch.setattr(
            occupied_centers, "unary_union", Mock(side_effect=GEOSException("test"))
        )
    sources = Mock(wraps=geometric_search.GeometryCandidateSearch)
    monkeypatch.setattr(geometric_search, "GeometryCandidateSearch", sources)

    class FullGeometry(SearchGeometry):
        # Провайдер без доказанной сводки сохраняет обычную проверку каждой пробы.
        search_trace_assumption_codes = None

        def __init__(self):
            super().__init__()
            self.checked_positions = []

        def position_violation(self, project, x, y, radius, plant_kind="tree"):
            self.checked_positions.append((x, y))
            return super().position_violation(project, x, y, radius, plant_kind)

    geometry = FullGeometry()
    result = geometric_placement(project, request, zone, geometry)

    assert sources.call_args.args[0].equals(box(2, 2, 38, 38))
    # Возвращается лучшая раскладка. Проверка отклонённого первого варианта
    # подтверждается вызовом оценщика, а не присутствием его отказа в лучшей.
    assert (20, 20) in geometry.checked_positions
    assert any(item.status == "allowed" for item in result.search.candidate_results)
    for item in result.search.candidate_results:
        if item.status == "allowed":
            accepted = result.operations[item.operation_index].object
            assert (accepted.x, accepted.y) != (20, 20)
    assert geometry.geometry_checks >= 2
    assert result.search.full_checked_count == geometry.geometry_checks
    assert result.search.stop_reason == "target_reached"
    assert project.model_dump(mode="json") == before


def test_geometric_tiny_gap_is_repeatable_and_full_checker_keeps_source_warning():
    project = saved_project()
    project.planting_zones = [
        PlantingZoneAssignment(
            id="work", label="Участок", geometry=mapping(box(-20, -20, 20, 20))
        )
    ]
    project.geometry = GeometrySnapshot(
        feature_collection={"type": "FeatureCollection", "features": []},
        calculation_scope="available_data",
    )
    project.source_file = SourceFile(
        name="legacy.dxf",
        size=1,
        imported_at="now",
        dxf_version="test",
        units="м",
        entity_count=1,
        accept_partial_geometry=True,
    )
    candidate = payload()
    template = spacing_template(candidate, 1.6)
    distance = required_spacing(template, project.plan.objects[0], 0)
    usable = box(distance - 0.001, -0.002, distance + 0.001, 0.002)
    zone = project.planting_zones[0].model_copy(
        update={
            "geometry": {
                **mapping(usable),
                "ga_work_zone": project.planting_zones[0].geometry,
            }
        }
    )
    request = FillPatternRequest(
        base_plan_version=project.plan.version,
        zone_ids=["work"],
        layout="natural",
        placement_mode="count",
        target_count=40,
        edge_offset_m=0,
        spacing_m=3.5,
        seed=47,
        species_revision_id=candidate.species_revision_id,
        size_class=candidate.size_class,
        layout_radius_m=1.6,
    )

    class PartialGeometry(SearchGeometry):
        def placement_advisory_detail(self, *args):
            return PositionAdvisory(
                "SOURCE_GEOMETRY_PARTIAL", "Исходник", "Полнота не подтверждена", None
            )

    geometry = PartialGeometry(
        frozenset({"GEOMETRY_RULE_ASSUMPTION"}),
        (frozenset({"GEOMETRY_RULE_ASSUMPTION"}),),
    )
    before = project.model_dump(mode="json")
    result = geometric_placement(project, request, zone, geometry)
    repeated = geometric_placement(project, request, zone)
    accepted = result.search.candidate_results[0]
    checked_point = result.operations[accepted.operation_index].object
    assert (
        checked_point.x,
        checked_point.y,
    ) == (
        repeated.operations[
            repeated.search.candidate_results[0].operation_index
        ].object.x,
        repeated.operations[
            repeated.search.candidate_results[0].operation_index
        ].object.y,
    )
    assert PlantSpacingIndex(project.plan.objects, planning_horizon_year=0).respects(
        template.model_copy(update={"x": checked_point.x, "y": checked_point.y})
    )
    assert accepted.code == "SOURCE_GEOMETRY_PARTIAL" and accepted.status == "unknown"
    assert accepted.rule_trace is not None
    assert network_review_reason([accepted.rule_trace])
    assert geometry.geometry_checks >= 1 and geometry.trace_calls >= 1
    assert result.search.full_checked_count == geometry.geometry_checks
    assert project.model_dump(mode="json") == before
