from __future__ import annotations

from fastapi.testclient import TestClient

from app.composition import get_application
from app.contracts import (
    CoordinateReference,
    GeometrySnapshot,
    Layer,
    LayerKind,
    Project,
    SourceFile,
)
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


def test_confirmed_context_is_not_reported_as_an_unclassified_layer() -> None:
    annotation = _layer("Оформление границы", LayerKind.IGNORE)
    annotation.mapping_confirmed = True
    mixed = _layer("Смешанный слой", LayerKind.IGNORE)
    mixed.mapping_confirmed = False
    lawn = _layer("Газон", LayerKind.LAWN)
    lawn.mapping_confirmed = True
    passport = build_data_passport(Project(name="Решения по слоям", layers=[annotation, mixed, lawn]))
    assert passport.unclassified_layers == ["Смешанный слой"]
    assert "Газон" not in passport.excluded_layers
    assert next(e for e in passport.entries if e.kind == "lawn").object_count == lawn.object_count


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
        source_file=SourceFile(name="survey.dxf", size=100, imported_at="2026-09-01T10:00:00+00:00", owner="ГБУ Озеленение", dxf_version="R2018", units="m", entity_count=17),
    )

    passport = build_data_passport(project)
    by_kind = {entry.kind: entry for entry in passport.entries}

    assert passport.calculation_status == "ready"
    assert passport.mass_placement_status == "limited"
    assert passport.source_file_name == "survey.dxf"
    assert passport.source_imported_at == "2026-09-01T10:00:00+00:00"
    assert passport.source_owner == "ГБУ Озеленение"
    assert passport.coordinate_reference.crs_id == "EPSG:32637"
    assert by_kind["building"].semantic_confidence == "medium"
    assert by_kind["building"].decision_level == "warning"
    assert all(entry.source_file_name == "survey.dxf" for entry in passport.entries)
    assert all(entry.source_imported_at == "2026-09-01T10:00:00+00:00" for entry in passport.entries)
    assert all(entry.source_owner == "ГБУ Озеленение" for entry in passport.entries)
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
    created = get_application().create_project("Паспорт через API")
    created.layers = [_layer("UTIL_HEAT", LayerKind.UTILITY, count=3)]
    created.coordinate_reference = CoordinateReference(status="local", source="dxf_geodata")
    saved = get_application().repository.save(created)

    response = client.get(f"/api/projects/{saved.id}/data-passport")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mass_placement_status"] == "blocked"
    assert {entry["kind"] for entry in payload["entries"]} >= {"site_border", "utility"}
    assert all(entry["used_object_count"] is None for entry in payload["entries"])
    assert all(entry["display_feature_count"] is None for entry in payload["entries"])


def test_display_parts_are_not_reported_as_calculated_cad_objects() -> None:
    project = Project(name="One object, three display parts", layers=[
        _layer("wall", LayerKind.BUILDING),
    ], geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"source_layer": "wall", "kind": "building"}}
        for _ in range(3)
    ]}))
    building = next(e for e in build_data_passport(project).entries if e.kind == "building")
    assert building.object_count == 1
    assert building.display_feature_count == 3
    assert building.used_object_count is None


def test_explicit_layer_role_replaces_suggestion_without_duplicate_objects() -> None:
    project = Project(name="Reassigned", layers=[
        _layer("shared", LayerKind.SITE_BORDER, LayerKind.ROAD, count=8472),
    ])
    entries = {e.kind: e for e in build_data_passport(project).entries}
    assert entries["site_border"].object_count == 0
    assert entries["road"].object_count == 8472
    assert entries["road"].display_feature_count is None


def test_empty_map_does_not_prove_layer_participation() -> None:
    project = Project(name="Empty projection", layers=[_layer("wall", LayerKind.BUILDING)],
                      geometry=GeometrySnapshot(feature_collection={"type": "FeatureCollection", "features": []}))
    building = next(e for e in build_data_passport(project).entries if e.kind == "building")
    assert building.status == "partial"
    assert not building.used_in_calculation
    assert building.display_feature_count == 0
    assert building.used_object_count is None
