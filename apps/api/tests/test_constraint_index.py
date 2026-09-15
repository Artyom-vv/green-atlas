"""Differential checks against the previous global-union spatial strategy."""

from collections.abc import Sequence
from dataclasses import asdict
from math import cos, pi, sin
from random import Random
from typing import Literal

import pytest
from shapely.geometry import LineString, Point, Polygon, box, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from app.contracts import GeometrySnapshot, PlantingZoneAssignment, Project
from app.geometry.constraint_index import ConstraintIndex
from app.geometry.domain import PositionChecker


class GlobalUnionChecker(PositionChecker):
    """Frozen search policy: all source objects, regardless of work extent."""

    def _constraint(self, kind: str, window: BaseGeometry) -> BaseGeometry | None:
        geometries = [geometry for _, geometry in self._features_of_kind(kind)]
        return unary_union(geometries) if geometries else None

    def _evidence(
        self,
        kind: str,
        center: Point,
        distance: float,
    ) -> tuple[str | None, tuple[str, ...]]:
        layers: set[str] = set()
        identifiers: list[str] = []
        for feature, geometry in self._features_of_kind(kind):
            if center.distance(geometry) > distance + 1e-6:
                continue
            source_layer = str(
                feature.get("properties", {}).get("source_layer", "")
            ).strip()
            if source_layer:
                layers.add(source_layer)
            if feature.get("id") is not None and len(identifiers) < 20:
                identifiers.append(str(feature["id"]))
        return ", ".join(sorted(layers)) or None, tuple(identifiers)


def feature(kind: str, geometry: BaseGeometry, identifier: str = "source") -> dict:
    return {
        "type": "Feature",
        "id": identifier,
        "geometry": mapping(geometry),
        "properties": {"kind": kind, "source_layer": f"layer-{identifier}"},
    }


def project_with(features: list[dict], areas: Sequence[BaseGeometry] = ()) -> Project:
    return Project(
        name="Spatial equivalence",
        geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": features,
            }
        ),
        planting_zones=[
            PlantingZoneAssignment(label=f"Area {index}", geometry=mapping(area))
            for index, area in enumerate(areas)
        ],
    )


@pytest.mark.parametrize("plant_kind", ["tree", "shrub"])
@pytest.mark.parametrize(
    "kind",
    [
        "building",
        "road",
        "existing_green",
        "water",
        "restricted",
        "utility",
        "unknown",
    ],
)
def test_local_queries_preserve_point_results_and_evidence(
    plant_kind: Literal["tree", "shrub"],
    kind: str,
) -> None:
    hollow = Polygon(
        box(25, 25, 55, 55).exterior.coords, [box(35, 35, 45, 45).exterior.coords]
    )
    project = project_with(
        [
            feature("site_border", box(-20, -20, 150, 150)),
            feature(kind, hollow, "hollow"),
            feature(kind, LineString([(70, 0), (70, 110)]), "line"),
            feature(kind, Point(10, 10), "point"),
            feature(kind, box(1000, 1000, 1100, 1100), "distant"),
        ],
        [box(-10, -10, 85, 120), box(95, 20, 130, 80)],
    )
    current, previous = PositionChecker(project), GlobalUnionChecker(project)
    random = Random(1543)
    points = [(random.uniform(-20, 140), random.uniform(-20, 140)) for _ in range(80)]
    points += [(10, 10), (25, 25), (30, 40), (40, 40), (35, 40), (70, 20), (90, 30)]
    points += [(20 + delta, 30) for delta in (-2e-6, 0, 0.5e-6, 2e-6)]
    for x, y in points:
        assert current.check(x, y, 1.6, plant_kind) == previous.check(
            x, y, 1.6, plant_kind
        )
        assert current.advisory(x, y, 1.6) == previous.advisory(x, y, 1.6)
        assert current.growth_advisory(x, y, 6, 7) == previous.growth_advisory(
            x, y, 6, 7
        )


@pytest.mark.parametrize("plant_kind", ["tree", "shrub"])
def test_safe_areas_preserve_holes_and_separated_work_areas(
    plant_kind: Literal["tree", "shrub"],
) -> None:
    areas = [
        Polygon(
            box(0, 0, 100, 100).exterior.coords, [box(30, 30, 50, 50).exterior.coords]
        ),
        box(1000, 1000, 1100, 1100),
    ]
    kinds = ["building", "road", "existing_green", "water", "restricted", "utility"]
    sources = [
        feature(kind, box(60 + index * 5, 10, 62 + index * 5, 20), kind)
        for index, kind in enumerate(kinds)
    ]
    sources += [
        feature("building", box(1005, 1005, 1020, 1020)),
        feature("building", box(500, 500, 600, 600), "between-areas"),
        feature("building", box(34, 34, 40, 40), "inside-hole"),
    ]
    project = project_with(sources, areas)
    current, previous = PositionChecker(project), GlobalUnionChecker(project)
    area = unary_union(areas)
    for growth in ((), (6, 7)):
        result = current.automatic_safe_area(area, 1.6, plant_kind, *growth)
        baseline = previous.automatic_safe_area(area, 1.6, plant_kind, *growth)
        assert result.symmetric_difference(baseline).area < 1e-8
        assert result.hausdorff_distance(baseline) < 1e-8


def test_evidence_keeps_source_order_all_layers_and_twenty_id_limit() -> None:
    sources = [
        feature("building", Point(1 + index / 100, 0), str(index))
        for index in range(25)
    ]
    sources.append(feature("building", Point(5 + 0.5e-6, 0), "epsilon"))
    sources.append(feature("building", Point(5 + 2e-6, 0), "outside"))
    project = project_with(sources)
    violation = PositionChecker(project).check(0, 0, 1.6)
    assert violation == GlobalUnionChecker(project).check(0, 0, 1.6)
    assert violation is not None
    assert violation.source_feature_ids == tuple(str(index) for index in range(20))
    assert "layer-24" in violation.source_layer
    assert "layer-epsilon" in violation.source_layer
    assert "layer-outside" not in violation.source_layer


def test_occupied_actual_uses_nearest_object_even_outside_polygonal_footprint() -> None:
    # The closer point lies between buffer vertices and outside its polygon;
    # another point touches the buffer and therefore triggers the violation.
    radius, angle = 100.0, pi / 64
    nearest = Point(99.95 * cos(angle), 99.95 * sin(angle))
    assert not Point(0, 0).buffer(radius).intersects(nearest)
    project = project_with(
        [
            feature("water", nearest, "nearest"),
            feature("water", Point(radius, 0), "touching"),
        ]
    )
    current = PositionChecker(project).check(0, 0, radius)
    assert current == GlobalUnionChecker(project).check(0, 0, radius)
    assert asdict(current)["actual"] == 99.95


def test_no_geometry_and_untyped_sources_keep_their_existing_statuses() -> None:
    assert (
        PositionChecker(Project(name="No geometry")).check(0, 0, 1).code
        == "GEOMETRY_NOT_READY"
    )
    project = project_with(
        [feature("utility", Point(0, 0)), feature("unknown", box(-10, -10, 10, 10))]
    )
    checker = PositionChecker(project)
    assert checker.check(0, 0, 1) is None
    assert checker.advisory(0, 0, 1).code == "UNTYPED_UTILITY_REVIEW"


def test_index_excludes_remote_objects_before_union(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.geometry.constraint_index as module

    sizes: list[int] = []
    original = module.unary_union

    def record(geometries: list[BaseGeometry]) -> BaseGeometry:
        sizes.append(len(geometries))
        return original(geometries)

    monkeypatch.setattr(module, "unary_union", record)
    records = [
        (
            feature("building", box(index * 20, 0, index * 20 + 5, 5)),
            box(index * 20, 0, index * 20 + 5, 5),
        )
        for index in range(1000)
    ]
    index = ConstraintIndex(records)
    first = index.union(box(-1, -1, 6, 6))
    assert index.union(box(-2, -2, 7, 7)) is first
    assert index.union(box(-100, -100, -50, -50)) is None
    assert sizes == [1]
