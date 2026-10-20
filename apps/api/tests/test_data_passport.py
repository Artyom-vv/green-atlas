from __future__ import annotations

from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from app.cad_intake.prepare_contracts import (
    CadOpeningReview,
    CadSkippedReference,
    PreparedSourceProvenance,
)
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
from app.dxf_import.source.review_contracts import SourceReview
from app.geometry.coverage_contracts import (
    GeometryCoverage,
    GeometryCoverageIssue,
    LayerGeometryCoverage,
)
from app.main import app
from app.planning.contracts import Plan

client = TestClient(app)


@pytest.mark.parametrize("legacy_year", [0, 1, 20, 40])
def test_passport_uses_current_geometry_without_legacy_growth_horizon(legacy_year):
    project = Project(
        name="Сценарный прогноз", plan=Plan(planning_horizon_year=legacy_year)
    )
    before = project.model_dump_json()
    passport = build_data_passport(project)
    assert passport.planning_horizon_year == 0
    assert project.model_dump_json() == before
    assert build_data_passport(Project(name="Без плана")).planning_horizon_year is None


def _layer(
    name: str,
    suggested: LayerKind,
    mapped: LayerKind | None = None,
    *,
    complete: bool = True,
    count: int = 1,
) -> Layer:
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
    passport = build_data_passport(
        Project(name="Решения по слоям", layers=[annotation, mixed, lawn])
    )
    assert passport.unclassified_layers == ["Смешанный слой"]
    assert "Газон" not in passport.excluded_layers
    assert (
        next(e for e in passport.entries if e.kind == "lawn").object_count
        == lawn.object_count
    )


def test_data_passport_marks_unprepared_and_unknown_source_as_not_ready() -> None:
    passport = build_data_passport(
        Project(
            name="Неполный исходник",
            layers=[
                _layer("SITE_BORDER", LayerKind.SITE_BORDER),
                _layer("SURVEY_NOTES", LayerKind.IGNORE, count=12),
            ],
        )
    )

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
        geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "border",
                        "properties": {
                            "source_layer": "SITE_BORDER",
                            "kind": "site_border",
                        },
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [[[0, 0], [10, 0], [10, 10], [0, 0]]],
                        },
                    },
                    {
                        "type": "Feature",
                        "id": "road",
                        "properties": {"source_layer": "ROAD", "kind": "road"},
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[0, 4], [10, 4]],
                        },
                    },
                    {
                        "type": "Feature",
                        "id": "green",
                        "properties": {
                            "source_layer": "GREEN_EXISTING",
                            "kind": "existing_green",
                        },
                        "geometry": {"type": "Point", "coordinates": [8, 8]},
                    },
                ],
            }
        ),
        coordinate_reference=CoordinateReference(
            status="declared",
            crs_id="EPSG:32637",
            name="test",
            source="dxf_geodata",
            axis_order="xy",
        ),
        source_file=SourceFile(
            name="survey.dxf",
            size=100,
            imported_at="2026-09-01T10:00:00+00:00",
            owner="ГБУ Озеленение",
            dxf_version="R2018",
            units="m",
            entity_count=17,
        ),
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
    assert all(
        entry.source_imported_at == "2026-09-01T10:00:00+00:00"
        for entry in passport.entries
    )
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
            _layer(
                "BUILDING_PARTIAL",
                LayerKind.BUILDING,
                LayerKind.IGNORE,
                complete=False,
                count=40,
            ),
        ],
        map_ready=True,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []}
        ),
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
    created.coordinate_reference = CoordinateReference(
        status="local", source="dxf_geodata"
    )
    saved = get_application().repository.save(created)

    response = client.get(f"/api/projects/{saved.id}/data-passport")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mass_placement_status"] == "blocked"
    assert {entry["kind"] for entry in payload["entries"]} >= {"site_border", "utility"}
    assert all(entry["used_object_count"] is None for entry in payload["entries"])
    assert all(entry["display_feature_count"] is None for entry in payload["entries"])


def test_display_parts_are_not_reported_as_calculated_cad_objects() -> None:
    project = Project(
        name="One object, three display parts",
        layers=[
            _layer("wall", LayerKind.BUILDING),
        ],
        geometry=GeometrySnapshot(
            feature_collection={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "properties": {"source_layer": "wall", "kind": "building"},
                    }
                    for _ in range(3)
                ],
            }
        ),
    )
    building = next(
        e for e in build_data_passport(project).entries if e.kind == "building"
    )
    assert building.object_count == 1
    assert building.display_feature_count == 3
    assert building.used_object_count is None


def test_explicit_layer_role_replaces_suggestion_without_duplicate_objects() -> None:
    project = Project(
        name="Reassigned",
        layers=[
            _layer("shared", LayerKind.SITE_BORDER, LayerKind.ROAD, count=8472),
        ],
    )
    entries = {e.kind: e for e in build_data_passport(project).entries}
    assert entries["site_border"].object_count == 0
    assert entries["road"].object_count == 8472
    assert entries["road"].display_feature_count is None


def test_empty_map_does_not_prove_layer_participation() -> None:
    project = Project(
        name="Empty projection",
        layers=[_layer("wall", LayerKind.BUILDING)],
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []}
        ),
    )
    building = next(
        e for e in build_data_passport(project).entries if e.kind == "building"
    )
    assert building.status == "partial"
    assert not building.used_in_calculation
    assert building.display_feature_count == 0
    assert building.used_object_count is None


PASSPORT_JSON_GOLDENS = {
    "empty": "62ba397c2cb99c0384786c8667314aeec2839b29103462aa3b2088a1d5317660",
    "verified": "67e6a72a80199b69662c52f853df01100a774a303cc7ba11a3d8517534390f92",
    "source_only": "15c6da34814375498c3d05ef538b3b916f534c0d31b123792909407a2942c530",
    "review_pending": "4827175a0cca62d96006faa8f8b82a71a297b580a5d92238f1b20bf778e03ccb",
    "decisions": "150f5e3b8c84cb96f8e96c05b3f63cc08a3ae8c482c932b2c107162bf89e2155",
    "coverage": "07b8041f0f06c8d78b4f9a003d6e5114ac8a37006da9bc5c25fc8a9f6a866d88",
    "coverage_partial": "8dfa241976f851143501600c600ae1601fa018a86a77a6be0fc9ddcadd89367d",
    "available_data": "1bd76c243bff754da2aa3006e014e4ae9211c9257bb58ec0af4849bfd607cae6",
    "opening_review": "501d2fcbced9bc3e42872f014aa74eef599bc29a73baaea97d1bf88c496363fe",
    "crs_unconfirmed": "31df724cd29dd3861f121dd5975e8769fc5fa65cd6733beb661a16c487574ca4",
}


class CoverageEvidence:
    """Фиксированное покрытие без запросов к AutoCAD и без изменений проекта."""

    def __init__(
        self, coverage: GeometryCoverage | None, failure: Exception | None = None
    ) -> None:
        self.coverage = coverage
        self.failure = failure
        self.calls = 0

    def source_coverage(self, project: Project) -> GeometryCoverage | None:
        self.calls += 1
        if self.failure:
            raise self.failure
        return self.coverage


def _passport_json_fixture(scenario: str) -> tuple[Project, CoverageEvidence | None]:
    if scenario == "empty":
        return Project(name="Пустой паспорт"), None
    # Порядок слоёв намеренно отличается от порядка классов паспорта.
    kinds = [
        LayerKind.RESTRICTED,
        LayerKind.ROAD,
        LayerKind.BUILDING,
        LayerKind.UTILITY,
        LayerKind.SITE_BORDER,
        LayerKind.EXISTING_GREEN,
    ]
    layers = [
        _layer(kind.value, kind, count=index + 1) for index, kind in enumerate(kinds)
    ]
    layers[1].object_count = 0
    features = [
        {
            "type": "Feature",
            "properties": {
                "source_layer": layer.source_name,
                "kind": layer.mapped_kind,
            },
        }
        for layer in layers
        if layer.object_count
    ]
    features.extend(
        [
            {
                "type": "Feature",
                "properties": {"source_layer": " building ", "kind": "road"},
            },
            {
                "type": "Feature",
                "properties": {
                    "source_layer": "building",
                    "kind": "building",
                    "source_context_only": True,
                },
            },
            {
                "type": "Feature",
                "properties": {"source_layer": "building", "kind": "forbidden"},
            },
        ]
    )
    project = Project(
        name="Полный паспорт",
        layers=layers,
        map_ready=True,
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": features}
        ),
        coordinate_reference=CoordinateReference(
            status="declared",
            crs_id="EPSG:32637",
            name="Проверенная система",
            source="dxf_geodata",
            axis_order="xy",
        ),
        source_file=SourceFile(
            name="survey.dxf",
            content_sha256="a" * 64,
            size=100,
            imported_at="2026-09-01T10:00:00+00:00",
            owner="ГБУ Озеленение",
            dxf_version="R2018",
            units="m",
            entity_count=21,
        ),
    )
    if scenario == "source_only":
        project.source_geometry, project.geometry = project.geometry, None
    elif scenario == "review_pending":
        project.source_review = SourceReview()
    elif scenario == "decisions":
        excluded = _layer(
            "building-excluded", LayerKind.BUILDING, LayerKind.IGNORE, complete=False
        )
        excluded.mapping_confirmed = True
        unconfirmed = excluded.model_copy(
            update={
                "id": "building-unconfirmed",
                "source_name": "building-unconfirmed",
                "mapping_confirmed": False,
            }
        )
        unassigned = _layer("utility-unassigned", LayerKind.UTILITY)
        unassigned.mapped_kind = None
        unknown = _layer("unknown", LayerKind.IGNORE, count=5)
        confirmed_context = _layer("confirmed-context", LayerKind.IGNORE)
        confirmed_context.mapping_confirmed = True
        project.layers.extend(
            [
                excluded,
                unconfirmed,
                unassigned,
                unknown,
                confirmed_context,
                _layer("remapped", LayerKind.SITE_BORDER, LayerKind.ROAD, count=7),
            ]
        )
    elif scenario in {"coverage", "coverage_partial", "available_data"}:
        records = {
            layer.source_name: LayerGeometryCoverage(area_count=1) for layer in layers
        }
        records["road"] = LayerGeometryCoverage(context_count=3)
        del records[
            "building"
        ]  # Слой учитывается отдельно, но класс не получает represented.
        project.layers[3].geometry_complete = False
        records["utility"] = LayerGeometryCoverage(linear_count=2, point_count=1)
        if scenario != "coverage":
            second = _layer(
                "utility-second", LayerKind.UTILITY, complete=False, count=4
            )
            project.layers.append(second)
            records[second.source_name] = LayerGeometryCoverage(
                linear_count=1,
                unresolved=[
                    GeometryCoverageIssue(
                        routes=["BB", "CC"],
                        reason="projection_missing",
                        detail="Нет подготовленного представления",
                    )
                ],
            )
        if scenario == "available_data":
            assert project.geometry is not None
            project.geometry.calculation_scope = "available_data"
        return project, CoverageEvidence(
            GeometryCoverage(source_sha256="a" * 64, layers=records)
        )
    elif scenario in {"opening_review", "crs_unconfirmed"}:
        project.coordinate_reference.crs_id = None
        if scenario == "opening_review":
            assert project.source_file is not None
            project.source_file.prepared_provenance = PreparedSourceProvenance(
                intake_operation_id="fixture-intake",
                manifest_sha256="b" * 64,
                entry="survey.dxf",
                source_sha256="a" * 64,
                opening_review=CadOpeningReview(
                    skipped_references=[
                        CadSkippedReference(owner="survey.dxf", block="external")
                    ],
                    skipped_drawings=["missing.dwg"],
                ),
            )
    elif scenario != "verified":
        raise AssertionError(f"Неизвестный сценарий: {scenario}")
    return project, None


@pytest.mark.parametrize("scenario", PASSPORT_JSON_GOLDENS)
def test_full_passport_json_preserves_baseline(scenario: str) -> None:
    """Хеш фиксирует весь JSON, включая порядок, строки, счётчики, defaults и null."""
    project, evidence = _passport_json_fixture(scenario)
    before = project.model_dump_json()
    passport = build_data_passport(project, geometry=evidence)
    assert (
        sha256(
            passport.model_dump_json(exclude={"planning_horizon_year"}).encode()
        ).hexdigest()
        == PASSPORT_JSON_GOLDENS[scenario]
    )
    assert project.model_dump_json() == before
    if evidence:
        assert evidence.calls == 1
    passport.coordinate_reference.name = "Изменение только паспорта"
    assert project.model_dump_json() == before


@pytest.mark.parametrize("scenario", PASSPORT_JSON_GOLDENS)
def test_verified_display_summary_preserves_complete_passport_json(
    scenario: str,
) -> None:
    """Сводка заменяет только обход карты; весь паспорт совпадает побайтно."""
    project, evidence = _passport_json_fixture(scenario)
    expected = build_data_passport(project, geometry=evidence).model_dump_json()
    counts = (
        {}
        if scenario == "empty"
        else {
            "restricted": 1,
            "building": 2,
            "utility": 1,
            "site_border": 1,
            "existing_green": 1,
        }
    )
    before_counts = counts.copy()
    for field in ("geometry", "source_geometry"):
        graph = getattr(project, field)
        if graph is not None:
            setattr(
                project,
                field,
                graph.model_copy(
                    update={
                        "feature_collection": {
                            "type": "FeatureCollection",
                            "features": [],
                        }
                    }
                ),
            )
    before = project.model_dump_json()
    actual = build_data_passport(
        project, geometry=evidence, display_counts=counts
    ).model_dump_json()
    assert actual == expected
    assert counts == before_counts
    assert project.model_dump_json() == before


@pytest.mark.parametrize("with_source", [True, False])
def test_coverage_none_keeps_fallback_and_is_called_only_with_source(
    with_source: bool,
) -> None:
    project, _ = _passport_json_fixture("review_pending")
    if not with_source:
        project.source_file = None
    evidence = CoverageEvidence(None)
    assert build_data_passport(project, geometry=evidence) == build_data_passport(
        project
    )
    assert evidence.calls == int(with_source)


def test_coverage_errors_and_source_mismatch_preserve_project() -> None:
    project, _ = _passport_json_fixture("verified")
    before = project.model_dump_json()
    failure = RuntimeError("Не удалось прочитать подготовленное покрытие")
    evidence = CoverageEvidence(None, failure)
    with pytest.raises(RuntimeError) as raised:
        build_data_passport(project, geometry=evidence)
    assert raised.value is failure
    assert evidence.calls == 1
    wrong_source = CoverageEvidence(GeometryCoverage(source_sha256="b" * 64, layers={}))
    with pytest.raises(
        ValueError, match="^Отчёт геометрии относится к другому захвату$"
    ):
        build_data_passport(project, geometry=wrong_source)
    assert wrong_source.calls == 1
    assert project.model_dump_json() == before
