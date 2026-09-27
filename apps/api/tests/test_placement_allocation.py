import pytest
from shapely.geometry import box, mapping, Point, LineString
from shapely.ops import unary_union

from app.application import ProjectApplication
from app.contracts import FillPatternRequest, PlacementMaskRequest, GeometrySnapshot, Plan, PlantingZoneAssignment, Project
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.domain import PositionChecker
from app.planning.allocation import equal_zone_targets, spread_indices
from app.planning.patterns import ShapelyCandidateGenerator
from app.projects.adapters import InMemoryProjectRepository
from app.validation.adapters import RuleBasedPlanValidator


def application(block_east=False):
    features = [{"type": "Feature", "properties": {"kind": "site_border"}, "geometry": mapping(box(-10, -10, 510, 210))}]
    if block_east:
        features.append({"type": "Feature", "properties": {"kind": "water"}, "geometry": mapping(box(400, 0, 500, 100))})
    project = Project(name="Allocation", geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": features}), plan=Plan(), planting_zones=[
        PlantingZoneAssignment(id="west", label="West", geometry=mapping(box(0, 0, 200, 200))),
        PlantingZoneAssignment(id="east", label="East", geometry=mapping(box(400, 0, 500, 100))),
    ])
    repo = InMemoryProjectRepository()
    project = repo.create(project)
    app = ProjectApplication(repository=repo, operation_repository=None, history=None, dxf_reader=None,
        geometry=ShapelyGeometryEngine(), geometry_query=None, validator=RuleBasedPlanValidator(),
        writer=None, candidate_generator=ShapelyCandidateGenerator())
    return app, project


def test_equal_targets_have_a_stable_remainder_and_no_duplicates():
    assert equal_zone_targets(["a", "b", "a", "c"], 5) == {"a": 2, "b": 2, "c": 1}
    assert equal_zone_targets(["a", "b", "c"], 1) == {"a": 1, "b": 0, "c": 0}
    assert equal_zone_targets([], 5) == {}
    assert spread_indices([0, 1, 2, 3, 4], 2) == [0, 4]
    assert spread_indices([0, 1, 2], 0) == []


@pytest.mark.parametrize("layout", ["natural", "regular", "staggered", "mask"])
def test_equal_count_reaches_distant_unequal_zones_and_keeps_only_final_preview(layout, monkeypatch):
    app, project = application()
    request = dict(base_plan_version=project.plan.version, zone_ids=["east", "west"], placement_mode="count", target_count=5, zone_distribution="equal", spacing_m=6)
    request = PlacementMaskRequest(mask_id="regular_grid", **request) if layout == "mask" else FillPatternRequest(layout=layout, **request)
    refreshes = []
    original = app.validation.refresh
    monkeypatch.setattr(app.validation, "refresh", lambda *a, **kw: (refreshes.append(1), original(*a, **kw))[1])
    preview = app.preview_pattern(project.id, request)
    assert preview.accepted_count == 5
    assert [(z.zone_id, z.requested_count, z.accepted_count) for z in preview.zone_allocations] == [("west", 3, 3), ("east", 2, 2)]
    assert preview.change_set.can_apply
    assert len(app.changes._previews) == len(refreshes) == 1
    assert app.get(project.id).plan.objects == []
    assert all(item.status == "allowed" for item in preview.change_set.candidate_results)


def test_empty_zone_quota_is_not_silently_moved_to_another_zone():
    app, project = application(block_east=True)
    request = FillPatternRequest(base_plan_version=project.plan.version, zone_ids=["west", "east"], placement_mode="count", target_count=4, zone_distribution="equal", layout="natural")
    preview = app.preview_pattern(project.id, request)
    assert preview.accepted_count == 2
    assert [(z.requested_count, z.accepted_count) for z in preview.zone_allocations] == [(2, 2), (2, 0)]
    assert preview.requested_count == preview.accepted_count + preview.rejected_count + preview.capacity_shortfall
    available = app.preview_pattern(project.id, request.model_copy(update={"zone_distribution": "available"}))
    assert available.accepted_count == 4
    assert all(z.requested_count is None for z in available.zone_allocations)


def test_zero_quota_does_not_generate_unrequested_sites():
    app, project = application()
    preview = app.preview_pattern(project.id, FillPatternRequest(base_plan_version=project.plan.version, zone_ids=["west", "east"], placement_mode="count", target_count=1, zone_distribution="equal", layout="natural"))
    assert [(z.requested_count, z.accepted_count) for z in preview.zone_allocations] == [(1, 1), (0, 0)]


@pytest.mark.parametrize("obstacle", [box(65, 40, 5000, 55), LineString([(0, 50), (5000, 50)]), Point(49, 49), box(104, 104, 108, 108)])
def test_local_growth_buffer_matches_whole_obstacle_buffer(obstacle):
    area = box(0, 0, 100, 100)
    far = box(10000, 10000, 20000, 20000)
    geometry = unary_union([obstacle, far])
    project = Project(name="Buffer equivalence", geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"kind": "utility"}, "geometry": mapping(geometry)},
    ]}))
    checker = PositionChecker(project)
    reference = checker.hard_safe_area(area, 1.6, "tree").difference(geometry.buffer(8))
    actual = checker.automatic_safe_area(area, 1.6, "tree", growth_canopy_radius=6, growth_root_radius=8)
    assert actual.symmetric_difference(reference).area < 1e-7
