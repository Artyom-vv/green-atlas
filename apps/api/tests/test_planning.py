from app.contracts import PlanObject
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
