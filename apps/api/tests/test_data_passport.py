from __future__ import annotations

from fastapi.testclient import TestClient

from app import api as api_module
from app.contracts import CoordinateReference, GeometrySnapshot, Layer, LayerKind, Project
from app.data_passport import build_data_passport
from app.main import app


client = TestClient(app)


def _layer(name: str, suggested: LayerKind, mapped: LayerKind | None = None, *, complete: bool = True, count: int = 1) -> Layer:
    return Layer(
        id=name,
        source_name=name,
        suggested_kind=suggested,
        mapped_kind=mapped if mapped is not None else suggested,
        object_count=count,
        color="#225CFF",
        geometry_complete=complete,
    )


def test_data_passport_marks_unprepared_and_unknown_source_as_not_ready() -> None:
    passport = build_data_passport(Project(
        name="Неполный исходник",
        layers=[
            _layer("SITE_BORDER", LayerKind.SITE_BORDER),
            _layer("SURVEY_NOTES", LayerKind.IGNORE, count=12),
        ],
    ))

    assert passport.overall_status == "not_ready"
    assert passport.calculation_status == "not_ready"
    assert passport.mass_placement_status == "blocked"
    assert passport.unclassified_layers == ["SURVEY_NOTES"]
    assert passport.entries[-1].kind == "unclassified"
    assert "Подтвердите слои и подготовьте карту" in passport.gaps


def test_data_passport_reports_real_layers_used_by_calculated_map() -> None:
    project = Project(
        name="Подготовленная карта",
        layers=[
            _layer("SITE_BORDER", LayerKind.SITE_BORDER),
            _layer("BUILDING", LayerKind.BUILDING, count=4),
            _layer("ROAD", LayerKind.ROAD, count=7),
            _layer("UTIL_HEAT", LayerKind.UTILITY, count=2),
            _layer("GREEN_EXISTING", LayerKind.EXISTING_GREEN, count=3),
        ],
        map_ready=True,
        geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": [
            {"type": "Feature", "id": "border", "properties": {"source_layer": "SITE_BORDER", "kind": "site_border"}, "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 0]]]}},
            {"type": "Feature", "id": "road", "properties": {"source_layer": "ROAD", "kind": "road"}, "geometry": {"type": "LineString", "coordinates": [[0, 4], [10, 4]]}},
            {"type": "Feature", "id": "green", "properties": {"source_layer": "GREEN_EXISTING", "kind": "existing_green"}, "geometry": {"type": "Point", "coordinates": [8, 8]}},
        ]}),
        coordinate_reference=CoordinateReference(status="declared", crs_id="EPSG:32637", name="test", source="dxf_geodata", axis_order="xy"),
    )

    passport = build_data_passport(project)
    by_kind = {entry.kind: entry for entry in passport.entries}

    assert passport.calculation_status == "ready"
    assert passport.mass_placement_status == "limited"
    assert by_kind["site_border"].used_in_calculation is True
    assert by_kind["road"].used_in_calculation is True
    assert by_kind["existing_green"].used_in_calculation is True
    assert by_kind["building"].used_in_calculation is False
    assert by_kind["building"].status == "partial"
    assert "BUILDING" not in passport.used_in_calculation
    assert "Здания и сооружения" not in passport.missing_classes
    assert any("Технические зоны" in gap for gap in passport.gaps)


def test_data_passport_does_not_call_excluded_incomplete_layer_verified() -> None:
    project = Project(
        name="Исключённый фрагмент",
        layers=[
            _layer("SITE_BORDER", LayerKind.SITE_BORDER),
            _layer("BUILDING_PARTIAL", LayerKind.BUILDING, LayerKind.IGNORE, complete=False, count=40),
        ],
        map_ready=True,
        geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": []}),
        coordinate_reference=CoordinateReference(status="local", source="dxf_geodata"),
    )

    passport = build_data_passport(project)
    building = next(entry for entry in passport.entries if entry.kind == "building")

    assert building.status == "excluded"
    assert building.used_in_calculation is False
    assert passport.mass_placement_status == "limited"
    assert passport.excluded_layers == ["BUILDING_PARTIAL"]
    assert any("Здания и сооружения" in gap for gap in passport.gaps)


def test_data_passport_is_exposed_as_a_compact_project_contract() -> None:
    created = api_module.application.create_project("Паспорт через API")
    created.layers = [_layer("UTIL_HEAT", LayerKind.UTILITY, count=3)]
    created.coordinate_reference = CoordinateReference(status="local", source="dxf_geodata")
    saved = api_module.application.repository.save(created)

    response = client.get(f"/api/projects/{saved.id}/data-passport")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mass_placement_status"] == "blocked"
    assert {entry["kind"] for entry in payload["entries"]} >= {"site_border", "utility"}
