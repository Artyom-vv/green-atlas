import pytest
from app.agent_tools import execute_tool
from app.contracts import GrowthEnvelopeForecast
from test_agent_planning import existing_application


def test_growth_reads_exact_selection_and_never_substitutes_layout_radius():
    app, project = existing_application()
    project.plan.objects[0].canopy_forecast = [GrowthEnvelopeForecast(horizon_year=20, radius_min_m=2, radius_max_m=3,
                                                                    confidence="medium", basis="test")]
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    data = execute_tool(app, project.id, "growth_objects", {"horizon_year": 20, "object_ids": ["one", "two"]})["result"]
    assert data["total"] == 2
    assert data["items"][0]["canopy"]["diameter_max_m"] == 6
    assert data["items"][0]["roots"] is None
    assert data["items"][1]["canopy"] is None
    assert app.get(project.id).model_dump_json() == before


def test_growth_zone_pagination_and_invalid_references():
    app, project = existing_application()
    first = execute_tool(app, project.id, "growth_objects", {"horizon_year": 20, "zone_ids": ["west"], "limit": 1})["result"]
    assert first["total"] == 2 and first["next_offset"] == 1
    second = execute_tool(app, project.id, "growth_objects", {"horizon_year": 20, "zone_ids": ["west"], "offset": 1})["result"]
    assert second["items"][0]["object_id"] == "two" and second["next_offset"] is None
    for invalid in ({"object_ids": ["missing"]}, {"zone_ids": ["missing"]}, {"horizon_year": 41}):
        with pytest.raises(ValueError):
            execute_tool(app, project.id, "growth_objects", {"horizon_year": 20, **invalid})
