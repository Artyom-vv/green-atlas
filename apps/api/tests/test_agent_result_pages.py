import pytest
from app.agent_result_pages import ResultPage, page_result


def test_large_last_item_is_accessible_without_json_truncation():
    artifacts = {3: {"result": {"objects": [{"id": str(i), "x": i} for i in range(1000)]}}}
    root = page_result(artifacts, ResultPage(event_index=3))
    assert root["items"][0]["path"] == ["result"]
    last = page_result(artifacts, ResultPage(event_index=3, path=["result", "objects"], offset=999))
    assert last["total"] == 1000 and last["next_offset"] is None
    item = page_result(artifacts, ResultPage(event_index=3, path=last["items"][0]["path"]))
    assert item["items"] == [{"key": "id", "value": "999"}, {"key": "x", "value": 999}]


def test_nested_geometry_pages_and_invalid_paths():
    artifacts = {0: {"coordinates": [[1, 2], [3, 4]]}}
    page = page_result(artifacts, ResultPage(event_index=0, path=["coordinates"], limit=1))
    assert page["next_offset"] == 1
    assert page["items"][0]["path"] == ["coordinates", 0]
    for query in (ResultPage(event_index=1), ResultPage(event_index=0, path=["coordinates", -1]),
                  ResultPage(event_index=0, path=["missing"])):
        with pytest.raises(ValueError):
            page_result(artifacts, query)


def test_projection_reads_labels_without_loading_geometry_or_one_call_per_zone():
    artifacts = {0: [{"id": str(i), "label": f"Зона {i}", "geometry": {"coordinates": [i]}} for i in range(7)]}
    result = page_result(artifacts, ResultPage(event_index=0, fields=["id", "label", "owner"]))
    assert len(result["items"]) == 7
    assert result["items"][-1]["fields"] == {"id": {"value": "6"}, "label": {"value": "Зона 6"}}
    assert result["items"][-1]["missing_fields"] == ["owner"]
    assert "geometry" not in result["items"][-1]["fields"]
