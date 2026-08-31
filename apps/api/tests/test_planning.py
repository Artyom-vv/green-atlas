from app.contracts import GeometrySnapshot, GrowthEnvelopeForecast, PlanObject, PlantingZoneAssignment, Project
from app.geometry.domain import PositionChecker
from app.planning.domain import PlantSpacingIndex


def test_spacing_index_never_misses_large_crowns_across_several_grid_cells() -> None:
    first = PlanObject(kind="tree", x=0, y=0, radius=25)
    candidate = PlanObject(kind="tree", x=40, y=0, radius=15)
    index = PlantSpacingIndex([first])

    assert index.respects(candidate) is False


def test_spacing_index_keeps_dense_manual_checks_local() -> None:
    objects = [
        PlanObject(kind="tree", x=(index % 100) * 8, y=(index // 100) * 8, radius=1.6)
        for index in range(10_000)
    ]
    index = PlantSpacingIndex(objects)

    nearby = index.nearby(PlanObject(kind="tree", x=400, y=400, radius=1.6))

    assert len(nearby) < 20


def forecast(radius: float) -> list[GrowthEnvelopeForecast]:
    return [GrowthEnvelopeForecast(horizon_year=20, radius_min_m=radius - 1, radius_max_m=radius, confidence="low", basis="test")]


def test_spacing_uses_mature_crowns_when_species_is_known() -> None:
    first = PlanObject(kind="tree", x=0, y=0, radius=1.6, canopy_forecast=forecast(6))
    index = PlantSpacingIndex([first])

    assert index.respects(PlanObject(kind="tree", x=12, y=0, radius=1.6, canopy_forecast=forecast(6))) is False
    assert index.respects(PlanObject(kind="tree", x=14, y=0, radius=1.6, canopy_forecast=forecast(6))) is True


def test_dense_group_can_close_canopies_without_disabling_external_spacing() -> None:
    first = PlanObject(kind="tree", x=0, y=0, radius=1.6, canopy_forecast=forecast(6), group_ids=["grove"], spacing_policy="canopy")
    index = PlantSpacingIndex([first])

    assert index.respects(PlanObject(kind="tree", x=7, y=0, radius=1.6, canopy_forecast=forecast(6), group_ids=["grove"], spacing_policy="canopy")) is True
    assert index.respects(PlanObject(kind="tree", x=7, y=0, radius=1.6, canopy_forecast=forecast(6), group_ids=["other"], spacing_policy="canopy")) is False


def test_growth_envelope_keeps_automatic_layout_inside_the_selected_area() -> None:
    area = {"type": "Polygon", "coordinates": [[[10, 10], [90, 10], [90, 90], [10, 90], [10, 10]]]}
    project = Project(
        name="Прогноз границы",
        geometry=GeometrySnapshot(feature_collection={
            "type": "FeatureCollection",
            "features": [{
                "type": "Feature",
                "properties": {"kind": "site_border"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]]},
            }],
        }),
        planting_zones=[PlantingZoneAssignment(label="Участок", geometry=area)],
    )

    advisory = PositionChecker(project).growth_advisory(14, 50, canopy_radius=6, root_radius=7)

    assert advisory is not None
    assert advisory.code == "GROWTH_PLANTING_ZONE"
