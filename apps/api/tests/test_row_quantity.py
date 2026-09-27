import pytest

from app.contracts import RowPatternRequest
from app.planning.patterns import generate_row


@pytest.mark.parametrize("count", [2, 5, 6])
def test_both_sides_use_total_quantity_and_full_axis(count):
    request = RowPatternRequest(
        type="row", base_plan_version=1, plant_kind="tree", zone_ids=["zone"],
        axis={"type": "LineString", "coordinates": [[0, 0], [100, 0]]},
        placement_mode="count", target_count=count, side="both", lateral_offset_m=3,
        start_offset_m=10, end_offset_m=10, spacing_m=6,
    )
    points = generate_row(request)
    assert len(points) == count
    assert {p.y for p in points} == {-3, 3}
    assert min(p.x for p in points) == (50 if count == 2 else 10)
    assert max(p.x for p in points) == (50 if count == 2 else 90)
