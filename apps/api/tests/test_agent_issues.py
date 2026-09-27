import pytest
from app.agent_tools import execute_tool
from app.contracts import ValidationIssue
from test_agent_planning import existing_application


def test_issues_include_related_objects_but_not_unrelated_or_global_for_selection():
    app, project = existing_application()
    project.plan.issues = [
        ValidationIssue(id="pair", severity="error", code="spacing", title="Расстояние", description="Мало места", object_id="two", related_object_ids=["one"], rule_id="spacing", actual=1, required=3, unit="м"),
        ValidationIssue(id="other", severity="warning", code="other", title="Иное", description="Иное", object_id="other"),
        ValidationIssue(id="global", severity="warning", code="data", title="Данные", description="Неполные данные"),
    ]
    app.repository.save(project)
    before = app.get(project.id).model_dump_json()
    result = execute_tool(app, project.id, "plan_issues", {"object_ids": ["one"], "rule_ids": ["spacing"]})["result"]
    assert result["total"] == 1
    assert result["items"][0]["id"] == "pair"
    assert result["items"][0]["actual"] == 1 and result["items"][0]["required"] == 3
    assert result["global_issues_excluded"]
    first = execute_tool(app, project.id, "plan_issues", {"limit": 1})["result"]
    assert first["total"] == 3 and first["next_offset"] == 1
    second = execute_tool(app, project.id, "plan_issues", {"offset": 1})["result"]
    assert [item["id"] for item in second["items"]] == ["other", "global"]
    assert app.get(project.id).model_dump_json() == before


def test_missing_objects_are_not_reported_as_no_issues():
    app, project = existing_application()
    for query in ({"object_ids": ["missing"]}, {"zone_ids": ["missing"]}):
        with pytest.raises(ValueError):
            execute_tool(app, project.id, "plan_issues", query)
