from unittest.mock import patch

from shapely.geometry import box, mapping, shape

from app.contracts import GeometrySnapshot, PlantingZoneAssignment, Project
from app.geometry.adapters import ShapelyGeometryEngine


def project_with_zone():
    return Project(name="Cache regression", geometry_version=1,
                   geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": []}),
                   planting_zones=[PlantingZoneAssignment(id="z", label="Work area", geometry=mapping(box(0, 0, 100, 100)))])


def test_safe_area_reuses_only_identical_inputs_and_bounds_cache():
    engine = ShapelyGeometryEngine()
    project = project_with_zone()
    checker = engine._position_checker(project)
    area = box(0, 0, 100, 100)
    with patch.object(checker, "_calculate_automatic_safe_area", wraps=checker._calculate_automatic_safe_area) as calculate:
        first = checker.automatic_safe_area(area, 1.6, "tree", 6, 7)
        assert checker.automatic_safe_area(shape(mapping(area)), 1.6, "tree", 6, 7) is first
        assert calculate.call_count == 1
        for radius in range(1, 45):
            checker.automatic_safe_area(area, float(radius), "tree", 6, 7)
        checker.automatic_safe_area(area, 1.6, "shrub", 6, 7)
        checker.automatic_safe_area(area, 1.6, "tree", 8, 9)
        checker.automatic_safe_area(box(0, 0, 50, 50), 1.6, "tree", 6, 7)
        assert calculate.call_count == 48
        assert len(checker._safe_areas) == 32


def test_zone_change_invalidates_checker_without_dxf_revision_change():
    engine = ShapelyGeometryEngine()
    project = project_with_zone()
    first = engine._position_checker(project)
    changed = project.model_copy(deep=True)
    changed.planting_zones[0].geometry = mapping(box(20, 20, 80, 80))
    second = engine._position_checker(changed)
    assert second is not first
    assert second.selected_area.equals(box(20, 20, 80, 80))
    assert engine._position_checker(changed) is second
    changed.geometry_version += 1
    assert engine._position_checker(changed) is not second
