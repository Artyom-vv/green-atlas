from app.agent_growth_answer import growth_answer


def test_growth_ranges_are_derived_from_evidence_and_low_confidence_is_visible():
    events = [{"tool": "growth_objects", "data": {"result": {"horizon_year": 20, "total": 3, "items": [
        {"object_id": "one", "canopy": {"diameter_min_m": 2.08, "diameter_max_m": 6, "confidence": "low"}, "roots": None},
        {"object_id": "two", "canopy": {"diameter_min_m": 3, "diameter_max_m": 8, "confidence": "medium"}, "roots": None},
    ]}}}]
    answer = growth_answer(events, [0])
    assert "2,08–8 м" in answer
    assert "среди этих посадок" in answer
    assert "низкая уверенность" in answer
    assert "нет прогноза для 2" in answer
    assert "только часть" in answer
    assert "20 лет" in answer


def test_does_not_rewrite_other_tools_or_mixed_evidence():
    assert growth_answer([{"tool": "project_context"}], [0]) is None
    assert growth_answer([], []) is None
