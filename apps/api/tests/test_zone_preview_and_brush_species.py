from test_manual_flow import area, client, prepare_project, select_areas


def test_zone_preflight_shows_exact_overlap_without_saving():
    project_id = prepare_project("Проверка границ")
    first = area("a", "Одинаковое имя", [[12, 12], [40, 12], [40, 40], [12, 40]])
    select_areas(project_id, [first])
    before = client.get(f"/api/projects/{project_id}").json()
    crossing = area("b", "Новый участок", [[30, 30], [60, 30], [60, 48], [30, 48]])
    response = client.post(f"/api/projects/{project_id}/planting-zones/preview", json=crossing)
    assert response.status_code == 200, response.json()
    preview = response.json()
    assert preview["can_save"] is False
    assert preview["overlaps"][0]["zone_id"] == "a"
    assert preview["overlaps"][0]["area_m2"] == 100
    assert preview["overlaps"][0]["geometry"]["type"] == "Polygon"
    assert client.get(f"/api/projects/{project_id}").json()["planting_zones"] == before["planting_zones"]
    assert client.put(f"/api/projects/{project_id}/planting-zones", json={"zones": [first, crossing]}).status_code == 400


def test_separate_and_nested_areas_are_not_false_overlaps():
    project_id = prepare_project("Свободный контур")
    select_areas(project_id, [area("a", "Участок", [[12, 12], [40, 12], [40, 40], [12, 40]])])
    for coordinates in ([[45, 15], [65, 15], [65, 35], [45, 35]], [[16, 16], [24, 16], [24, 24], [16, 24]]):
        result = client.post(f"/api/projects/{project_id}/planting-zones/preview", json=area("b", "Новый", coordinates)).json()
        assert result["can_save"] is True, result
        assert result["overlaps"] == []


def test_brush_with_species_builds_forecasts_before_application():
    project_id = prepare_project("Кисть с породой")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [72, 12], [72, 52], [12, 52]])])
    plan = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]
    draft = {"base_plan_version": plan["version"], "zone_ids": ["work"], "strokes": [{"mode": "add", "geometry": {"type": "LineString", "coordinates": [[20, 30], [60, 30]]}}], "width_m": 10, "composition": "trees", "tree_species_revision_id": "sorbus-aucuparia@2026-08-28.1"}
    response = client.post(f"/api/projects/{project_id}/plan/brush/preview", json=draft)
    assert response.status_code == 200, response.json()
    additions = response.json()["change_set"]["additions"]
    assert additions
    for item in additions:
        assert item["species_revision_id"] == draft["tree_species_revision_id"]
        assert {point["horizon_year"] for point in item["canopy_forecast"]} == {0, 5, 10, 15, 20, 30, 40}
        assert item["root_forecast"]
    assert client.get(f"/api/projects/{project_id}").json()["plan"] == plan
    wrong = client.post(f"/api/projects/{project_id}/plan/brush/preview", json={**draft, "tree_species_revision_id": "spiraea-japonica@2026-08-28.1"})
    assert wrong.status_code == 400
