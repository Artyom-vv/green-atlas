import pytest
from pydantic import ValidationError

from app.agent_tools import execute_tool, tool_schemas
from app.contracts import PlanObject
from test_placement_allocation import application


def test_registry_has_only_concrete_read_and_preview_tools():
    schemas = tool_schemas()
    assert len(schemas) == 26
    assert len({s["name"] for s in schemas}) == len(schemas)
    assert {s["effect"] for s in schemas} == {"read", "preview"}
    assert all(s["parameters"]["type"] == "object" for s in schemas)


def test_find_inspect_and_catalog_use_project_data():
    app, project = application()
    project.plan.objects = [PlanObject(id="oak", kind="tree", x=10, y=10, radius=1.6, locked=True,
                                      species_revision_id="quercus-robur@2026-08-28.1", planting_zone_id="west", group_ids=["grove"]),
                            PlanObject(id="shrub", kind="shrub", x=30, y=30, radius=.65, planting_zone_id="west")]
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    result = execute_tool(app, project.id, "find_plantings", {"text": "Дуб", "locked": True, "bounds": [0, 0, 20, 20], "zone_ids": ["west"]})
    assert result["result"]["total"] == 1
    assert result["result"]["items"][0]["id"] == "oak"
    assert execute_tool(app, project.id, "inspect_plantings", {"object_ids": ["oak"]})["result"][0]["locked"]
    assert execute_tool(app, project.id, "species_catalog", {"text": "дуб"})["result"][0]["id"] == "quercus-robur@2026-08-28.1"
    assert execute_tool(app, project.id, "project_context", {})["result"]["planting_count"] == 2
    assert app.get(project.id).model_dump_json() == before


def test_zone_summary_is_compact_but_full_geometry_remains_accessible():
    app, project = application()
    summary = execute_tool(app, project.id, "project_context", {})["result"]
    assert all("geometry" not in zone for zone in summary["zones"])
    zones = execute_tool(app, project.id, "inspect_zones", {"zone_ids": ["west"]})["result"]
    original = next(zone for zone in project.planting_zones if zone.id == "west")
    assert zones == [original.model_dump(mode="json")]
    with pytest.raises(ValueError):
        execute_tool(app, project.id, "inspect_zones", {"zone_ids": ["unknown"]})


def test_preview_fill_and_object_change_use_domain_without_committing():
    app, project = application()
    before = app.get(project.id).model_dump_json()
    result = execute_tool(app, project.id, "preview_fill", {"base_plan_version": 1, "zone_ids": ["west"], "placement_mode": "count", "target_count": 3})
    assert result["effect"] == "preview"
    assert result["result"]["accepted_count"] == 3
    assert result["result"]["change_set"]["can_apply"]
    change = execute_tool(app, project.id, "preview_changes", {"base_plan_version": 1, "label": "Одиночная посадка", "operations": [{"type": "add", "object": {"kind": "tree", "x": 15, "y": 15}}]})
    assert len(change["result"]["additions"]) == 1
    assert app.get(project.id).model_dump_json() == before


def test_tool_cannot_override_project_or_silently_drop_constraints():
    app, project = application()
    with pytest.raises(ValueError):
        execute_tool(app, project.id, "apply_change_set", {})
    with pytest.raises(ValueError):
        execute_tool(app, project.id, "project_context", {"project_id": "another"})
    with pytest.raises(ValidationError):
        execute_tool(app, project.id, "preview_changes", {"base_plan_version": 1, "label": "Нельзя игнорировать", "operations": [{"type": "add", "object": {"kind": "tree", "x": 15, "y": 15, "imaginary_condition": True}}]})
    with pytest.raises(ValueError):
        execute_tool(app, project.id, "inspect_plantings", {"object_ids": ["missing"]})
    with pytest.raises(ValidationError):
        execute_tool(app, project.id, "find_plantings", {"bounds": [0, 0, float("inf"), 10]})
