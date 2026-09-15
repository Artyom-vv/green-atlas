from __future__ import annotations
from app.composition import get_application

import asyncio
import csv
from io import BytesIO, StringIO
import json
from math import hypot
from pathlib import Path
import zipfile

import ezdxf
import pytest
from fastapi.testclient import TestClient
from shapely.affinity import translate
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union
from starlette.datastructures import UploadFile

from app import api as api_module
from app.dxf_import import limits as import_limits
from app.application import ProjectApplication
from app.scene.context import _context_base_elevation_source
from app.contracts import CoordinateReference, FillPatternRequest, GrowthEnvelopeForecast, LayerMapping, PlanObject, PlanObjectCreate, PlantingZoneAssignment
from app.dxf_import.adapters import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.domain import PositionChecker
from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import InMemoryProjectHistory
from app.main import app
from app.operations.adapters import SqliteOperationRepository
from app.planning.patterns import ShapelyCandidateGenerator, generate_fill
from app.planning.domain import required_spacing
from app.projects.adapters import SqliteProjectRepository
from app.species.forecast import forecast_at
from app.validation.adapters import RuleBasedPlanValidator


client = TestClient(app)
SITE_DXF = Path(__file__).parents[3] / "fixtures" / "site.dxf"
LARGE_DXF = Path(__file__).parents[3] / "fixtures" / "large-map" / "vdnkh-large.dxf"
KITAY_GOROD_3D_DXF = (
    Path(__file__).parents[3]
    / "fixtures"
    / "large-map"
    / "kitay-gorod"
    / "kitay-gorod-3d.dxf"
)


def test_generic_osm_layer_elevation_is_not_mislabelled_as_copernicus() -> None:
    assert _context_base_elevation_source({
        "source_layer": "OSM_BUILDING",
        "source_base_elevation_m": 12.5,
    }) == "dxf_elevation"
    assert _context_base_elevation_source({
        "source_layer": "GREEN_ATLAS_BUILDING_OSM_LEVELS",
        "source_base_elevation_m": 12.5,
    }) == "copernicus_dem_glo90"


def site_content() -> bytes:
    return SITE_DXF.read_bytes()


def binary_site_content() -> bytes:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("SOURCE_CONTEXT", color=3)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_line((10, 10), (90, 10), dxfattribs={"layer": "SOURCE_CONTEXT"})
    stream = BytesIO()
    document.write(stream, fmt="bin")
    return stream.getvalue()


def boundaryless_content() -> bytes:
    """A realistic working fragment: objects are present, outer parcel is not."""
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("BUILDING", color=1)
    document.layers.add("SURVEY_CONTEXT", color=3)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(38, 38), (48, 38), (48, 48), (38, 48)], close=True, dxfattribs={"layer": "BUILDING"})
    modelspace.add_lwpolyline([(0, 0), (90, 0), (90, 90), (0, 90)], close=True, dxfattribs={"layer": "SURVEY_CONTEXT"})
    stream = StringIO()
    document.write(stream)
    return stream.getvalue().encode()


def prepare_map(project_id: str) -> None:
    """Use the only public calculation route, including its real lifecycle."""
    started = client.post(f"/api/projects/{project_id}/operations/geometry")
    assert started.status_code == 202, started.json()
    operation = client.get(f"/api/projects/{project_id}/operations/{started.json()['id']}")
    assert operation.status_code == 200
    assert operation.json()["status"] == "completed", operation.json()


def prepare_project(name: str = "Ручной план") -> str:
    created = client.post("/api/projects", json={"name": name})
    assert created.status_code == 201
    project_id = created.json()["id"]

    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("site.dxf", site_content(), "application/dxf")},
    )
    assert imported.status_code == 200
    mappings = [
        {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
        for layer in imported.json()["layers"]
    ]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200
    prepare_map(project_id)
    return project_id


def select_areas(project_id: str, areas: list[dict]) -> None:
    response = client.put(f"/api/projects/{project_id}/planting-zones", json={"zones": areas})
    assert response.status_code == 200, response.json()


def area(id_: str, label: str, coordinates: list[list[float]]) -> dict:
    return {
        "id": id_,
        "label": label,
        "geometry": {"type": "Polygon", "coordinates": [[*coordinates, coordinates[0]]]},
    }


def test_import_is_limited_before_parser_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_limits, "MAX_DXF_CONTENT_BYTES", 4)
    project_id = client.post("/api/projects", json={"name": "Размер DXF"}).json()["id"]

    response = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("too-large.dxf", b"12345", "application/dxf")},
    )

    assert response.status_code == 400
    assert "DXF должен быть не больше" in response.json()["message"]


def test_chunked_upload_uses_one_mutable_source_buffer() -> None:
    payload = b"x" * (2 * 1024 * 1024 + 17)
    upload = UploadFile(file=BytesIO(payload), filename="large-source.dxf")

    collected = asyncio.run(api_module.read_limited_dxf_upload(upload))

    # The HTTP collector deliberately keeps one extendable buffer instead of
    # an array of immutable chunks followed by a second full ``bytes`` join.
    # Ezdxf and sqlite consume this buffer without changing its source bytes.
    assert isinstance(collected, bytearray)
    assert collected == payload


def test_mutable_source_passes_through_import_and_persists_exactly() -> None:
    payload = bytearray(site_content())
    project_id = client.post("/api/projects", json={"name": "Буфер загрузки"}).json()["id"]

    imported = get_application().import_dxf(project_id, "site.dxf", payload)

    assert imported.source_file is not None
    assert get_application().repository.get_source(project_id) == bytes(payload)


def test_opening_manual_plan_releases_raw_geometry_but_keeps_map_and_source() -> None:
    source = site_content()
    project_id = prepare_project("Освобождение исходной геометрии")
    before = get_application().get(project_id)
    assert before.source_geometry is not None
    assert before.geometry is not None
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])

    opened = client.post(f"/api/projects/{project_id}/plan/manual")

    assert opened.status_code == 200
    after = get_application().get(project_id)
    assert after.source_geometry is None
    assert after.geometry is not None
    viewport = client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 80, "resolution": 1},
    )
    assert viewport.status_code == 200
    assert viewport.json()["feature_collection"]["features"]
    assert client.get(f"/api/projects/{project_id}/source-dxf/download").content == source


def test_editor_can_add_a_nested_local_working_area_after_plan_creation() -> None:
    project_id = prepare_project("Локальный участок после открытия")
    outer = area("territory", "Вся территория", [[5, 5], [90, 5], [90, 70], [5, 70]])
    select_areas(project_id, [outer])
    opened = client.post(f"/api/projects/{project_id}/plan/manual")
    assert opened.status_code == 200
    added = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    assert added.status_code == 200

    local = area("local", "Локальный участок", [[12, 12], [36, 12], [36, 36], [12, 36]])
    saved = client.put(f"/api/projects/{project_id}/planting-zones", json={"zones": [outer, local]})

    assert saved.status_code == 200, saved.json()
    assert [zone["id"] for zone in saved.json()["planting_zones"]] == ["territory", "local"]
    assert len(saved.json()["plan"]["objects"]) == 1
    assert saved.json()["status"] == "editing"


def test_real_large_dxf_survives_http_import_geometry_viewport_and_source_recovery() -> None:
    """Exercise the durable path on a real, heterogeneous map-sized DXF.

    Unit tests cover parser shapes and a synthetic dense survey independently.
    This fixture combines thousands of roads, buildings, green context and a
    site border, so it catches regressions where a large but valid source is
    parsed yet fails during the real API save, background geometry operation
    or viewport projection.
    """
    source = LARGE_DXF.read_bytes()
    project_id = client.post("/api/projects", json={"name": "Крупный DXF через API"}).json()["id"]

    uploaded = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": (LARGE_DXF.name, source, "application/dxf")},
    )
    assert uploaded.status_code == 200, uploaded.json()
    assert uploaded.json()["source_file"]["entity_count"] == 4859
    assert next(layer for layer in uploaded.json()["layers"] if layer["source_name"] == "OSM_HYDROGRAPHY")["suggested_kind"] == "water"
    mappings = [
        {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
        for layer in uploaded.json()["layers"]
    ]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200

    started = client.post(f"/api/projects/{project_id}/operations/geometry")
    assert started.status_code == 202, started.json()
    operation = client.get(f"/api/projects/{project_id}/operations/{started.json()['id']}")
    assert operation.status_code == 200
    assert operation.json()["status"] == "completed", operation.json()
    assert client.get(f"/api/projects/{project_id}").json()["map_ready"] is True

    bounds = uploaded.json()["source_file"]["bounds"]
    assert bounds is not None
    viewport = client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": bounds[0], "min_y": bounds[1], "max_x": bounds[2], "max_y": bounds[3], "resolution": 12},
    )
    assert viewport.status_code == 200
    features = viewport.json()["feature_collection"]["features"]
    assert features
    assert any(feature["properties"].get("source_layer") == "OSM_ROAD_LOCAL" for feature in features)

    full_project = get_application().get(project_id)
    site_geometry = next(
        feature["geometry"]
        for feature in full_project.geometry.feature_collection["features"]
        if feature["properties"].get("kind") == "site_border"
    )
    selected = {"id": "whole-map", "label": "Проверяемая территория", "geometry": site_geometry}
    select_areas(project_id, [selected])
    opened = client.post(f"/api/projects/{project_id}/plan/manual")
    assert opened.status_code == 200
    preview = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
        "type": "fill",
        "base_plan_version": opened.json()["plan"]["version"],
        "plant_kind": "tree",
        "zone_ids": ["whole-map"],
        "placement_mode": "count",
        "target_count": 100,
        "layout": "natural",
        "spacing_m": 6,
        "edge_offset_m": 2,
        "seed": 47,
    })
    assert preview.status_code == 200, preview.json()
    assert preview.json()["accepted_count"] == 100
    checker = PositionChecker(get_application().get(project_id))
    assert all(
        checker.check(item["x"], item["y"], item["radius"], item["kind"]) is None
        for item in preview.json()["change_set"]["additions"]
    )

    recovered = client.get(f"/api/projects/{project_id}/source-dxf/download")
    assert recovered.status_code == 200
    assert recovered.content == source


def test_real_moscow_3d_dxf_keeps_georeference_and_estimated_dem_through_public_scene_api() -> None:
    """Prove the production HTTP path, not just the parser's internal model."""
    source = KITAY_GOROD_3D_DXF.read_bytes()
    project_id = client.post("/api/projects", json={"name": "Китай-город 3D через API"}).json()["id"]

    uploaded = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": (KITAY_GOROD_3D_DXF.name, source, "application/dxf")},
    )
    assert uploaded.status_code == 200, uploaded.json()
    assert uploaded.json()["coordinate_reference"]["status"] == "declared"
    assert uploaded.json()["coordinate_reference"]["origin_wgs84"] == [55.75225, 37.6235]
    mappings = [
        {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
        for layer in uploaded.json()["layers"]
    ]
    assert client.put(
        f"/api/projects/{project_id}/layer-mappings",
        json={"mappings": mappings},
    ).status_code == 200
    prepare_map(project_id)

    project = get_application().get(project_id)
    site_geometry = next(
        feature["geometry"]
        for feature in project.geometry.feature_collection["features"]
        if feature["properties"].get("kind") == "site_border"
    )
    select_areas(project_id, [{"id": "whole-map", "label": "Китай-город", "geometry": site_geometry}])
    opened = client.post(f"/api/projects/{project_id}/plan/manual")
    assert opened.status_code == 200, opened.json()

    response = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 0})
    assert response.status_code == 200, response.json()
    scene = response.json()
    assert scene["coordinate_reference"]["status"] == "declared"
    assert scene["georeference_status"] == "declared"
    assert scene["terrain_status"] == "estimated"
    assert scene["terrain_evidence"]["status"] == "estimated"
    assert "90 м" in scene["terrain_evidence"]["note"]
    terrain = [
        primitive
        for primitive in scene["vertical_primitives"]
        if primitive["terrain_mapping_status"] == "confirmed"
    ]
    assert len(terrain) == 320
    assert {primitive["terrain_confidence"] for primitive in terrain} == {"estimated"}
    assert all("Copernicus WorldDEM-90" in primitive["source_attribution"] for primitive in terrain)
    assert scene["building_feature_count"] > 0
    assert scene["building_height_confirmed_count"] > 0


def test_large_dxf_manual_plan_survives_repository_reopen_and_exports_from_source(tmp_path: Path) -> None:
    """Exercise the durable editing path on a real heterogeneous DXF.

    A full drawing must not merely import: after an operator selects a real
    place and adds a planting object, a fresh repository connection has to
    retain the calculated map, the manual plan and the byte-exact source used
    for export. This mirrors a process restart without leaning on the API's
    module-level test repository.
    """
    database_path = tmp_path / "large-manual.sqlite3"

    def application_for(path: Path) -> ProjectApplication:
        return ProjectApplication(
            repository=SqliteProjectRepository(path),
            operation_repository=SqliteOperationRepository(path),
            history=InMemoryProjectHistory(),
            dxf_reader=EzdxfReader(),
            geometry=ShapelyGeometryEngine(),
            geometry_query=IndexedGeometryQuery(),
            validator=RuleBasedPlanValidator(),
            writer=DxfRoundTripWriter(),
            candidate_generator=ShapelyCandidateGenerator(),
        )

    source = LARGE_DXF.read_bytes()
    first = application_for(database_path)
    created = first.create_project("Большой DXF с ручной схемой")
    imported = first.import_dxf(created.id, LARGE_DXF.name, source)
    first.save_mappings(created.id, [LayerMapping(layer_id=layer.id, kind=layer.suggested_kind, visible=True) for layer in imported.layers])
    operation = first.start_geometry_operation(created.id)
    first.run_geometry_operation(operation.id)
    prepared = first.get(created.id)

    allowed = unary_union([
        shape(feature["geometry"])
        for feature in prepared.geometry.feature_collection["features"]
        if feature.get("properties", {}).get("kind") == "allowed"
    ])
    # Keep both the selected area and the planted tree away from every
    # already-calculated boundary; a representative point of a buffered area
    # lets the fixture, not a magic coordinate, define the real place.
    interior = allowed.buffer(-6)
    assert not interior.is_empty
    anchor = interior.representative_point()
    selected_area = anchor.buffer(4)
    first.save_planting_zones(created.id, [PlantingZoneAssignment(id="large-work", label="Рабочий фрагмент", geometry=mapping(selected_area))])
    first.create_manual_plan(created.id)
    added = first.add_object(created.id, PlanObjectCreate(kind="tree", x=anchor.x, y=anchor.y))

    assert len(added.objects) == 1
    assert first.get(created.id).source_geometry is None

    # A new application owns new SQLite connections and empty in-memory map,
    # spacing and history caches. Only the durable project can restore this.
    restarted = application_for(database_path)
    restored = restarted.get(created.id)
    assert restored.source_geometry is None
    assert restored.geometry is not None
    assert [item.id for item in restored.plan.objects] == [added.objects[0].id]
    assert restarted.download_source(created.id) == source

    bounds = restored.source_file.bounds
    assert bounds is not None
    viewport = restarted.query_geometry(created.id, tuple(bounds), 8)
    assert any(feature.get("properties", {}).get("kind") == "planting_area" for feature in viewport.feature_collection["features"])

    artifact = restarted.export(created.id)
    exported = restarted.download_export(created.id, artifact.id)
    reopened = EzdxfReader().read("large-manual-plan.dxf", exported)
    assert reopened.entity_count == restored.source_file.entity_count + 1
    assert any(layer.source_name == "GREEN_ATLAS_TREES" for layer in reopened.layers)
    assert restarted.download_source(created.id) == source


def test_application_limit_applies_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_limits, "MAX_DXF_CONTENT_BYTES", 4)
    project_id = client.post("/api/projects", json={"name": "Размер без HTTP"}).json()["id"]

    with pytest.raises(ValueError, match="DXF должен быть не больше"):
        get_application().import_dxf(project_id, "too-large.dxf", b"12345")

    assert get_application().get(project_id).source_file is None


def test_failed_map_keeps_the_exact_uploaded_dxf_available_for_download() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_BAD", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    modelspace.add_lwpolyline([(20, 20), (40, 40), (20, 40), (40, 20)], close=True, dxfattribs={"layer": "BUILDING_BAD"})
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode()
    project_id = client.post("/api/projects", json={"name": "Сохранить сложный исходник"}).json()["id"]
    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("broken-geometry.dxf", source, "application/dxf")},
    )
    assert imported.status_code == 200
    mappings = [{"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True} for layer in imported.json()["layers"]]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200

    started = client.post(f"/api/projects/{project_id}/operations/geometry")
    assert started.status_code == 202
    operation = client.get(f"/api/projects/{project_id}/operations/{started.json()['id']}")
    assert operation.json()["status"] == "failed"

    downloaded = client.get(f"/api/projects/{project_id}/source-dxf/download")
    assert downloaded.status_code == 200
    assert downloaded.content == source
    assert "filename*=UTF-8''broken-geometry.dxf" in downloaded.headers["content-disposition"]
    assert client.get(f"/api/projects/{project_id}").json()["map_ready"] is False


def test_unprojectable_physical_cad_layer_stops_the_manual_flow_but_keeps_the_source() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.M
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("BUILDING_VOLUME", color=2)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "SITE_BORDER"})
    solid = modelspace.add_3dsolid(dxfattribs={"layer": "BUILDING_VOLUME"})
    solid.sab = b"ACIS"
    stream = StringIO()
    document.write(stream)
    source = stream.getvalue().encode()
    project_id = client.post("/api/projects", json={"name": "Не терять CAD-объект"}).json()["id"]
    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("building-volume.dxf", source, "application/dxf")},
    )
    mappings = [{"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True} for layer in imported.json()["layers"]]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200

    started = client.post(f"/api/projects/{project_id}/operations/geometry")
    operation = client.get(f"/api/projects/{project_id}/operations/{started.json()['id']}").json()

    assert operation["status"] == "failed"
    assert "BUILDING_VOLUME" in operation["error"]["message"]
    assert "3DSOLID" in operation["error"]["message"]
    assert client.get(f"/api/projects/{project_id}/source-dxf/download").content == source
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 400


def test_failed_reimport_keeps_the_saved_source_manual_plan_and_export() -> None:
    project_id = prepare_project("Не терять рабочий DXF")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200
    source_before = get_application().repository.get_source(project_id)

    failed = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("broken.dxf", b"not a valid dxf", "application/dxf")},
    )

    assert failed.status_code == 400
    assert "DXF" in failed.json()["message"]
    restored = client.get(f"/api/projects/{project_id}").json()
    assert restored["source_file"]["name"] == "site.dxf"
    assert restored["status"] == "editing"
    assert len(restored["plan"]["objects"]) == 1
    assert get_application().repository.get_source(project_id) == source_before

    artifact = client.post(f"/api/projects/{project_id}/exports")
    assert artifact.status_code == 200
    assert client.get(artifact.json()["download_url"]).status_code == 200


def test_valid_reimport_cannot_silently_replace_a_manual_project_source() -> None:
    project_id = prepare_project("Не заменить источник под планом")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200
    source_before = get_application().repository.get_source(project_id)

    replacement = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("replacement.dxf", site_content(), "application/dxf")},
    )

    assert replacement.status_code == 400
    assert "после открытия ручной схемы" in replacement.json()["message"]
    restored = client.get(f"/api/projects/{project_id}").json()
    assert restored["source_file"]["name"] == "site.dxf"
    assert len(restored["plan"]["objects"]) == 1
    assert get_application().repository.get_source(project_id) == source_before


def test_manual_route_keeps_several_real_working_areas() -> None:
    project_id = prepare_project("Два участка")
    select_areas(project_id, [
        area("lawn-a", "Контур DXF: газон A", [[12, 12], [60, 12], [60, 35], [12, 35]]),
        area("lawn-b", "Контур DXF: газон B", [[62, 12], [72, 12], [72, 30], [62, 30]]),
    ])

    manual = client.post(f"/api/projects/{project_id}/plan/manual")
    assert manual.status_code == 200
    payload = manual.json()
    assert payload["plan"]["objects"] == []
    assert [item["label"] for item in payload["planting_zones"]] == ["Контур DXF: газон A", "Контур DXF: газон B"]

    snapshot = client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": 0, "min_y": 0, "max_x": 120, "max_y": 80, "resolution": 1},
    )
    assert snapshot.status_code == 200
    selected = [
        feature["properties"]["label"]
        for feature in snapshot.json()["feature_collection"]["features"]
        if feature["properties"].get("kind") == "planting_area"
    ]
    assert selected == ["Контур DXF: газон A", "Контур DXF: газон B"]


def test_manual_area_keeps_a_boundaryless_dxf_usable() -> None:
    """Manual areas are the fallback boundary, never a fake site polygon."""
    project_id = client.post("/api/projects", json={"name": "Фрагмент без границы"}).json()["id"]
    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("survey-fragment.dxf", boundaryless_content(), "application/dxf")},
    )
    assert imported.status_code == 200
    mappings = [
        {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
        for layer in imported.json()["layers"]
    ]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200
    prepare_map(project_id)

    prepared = client.get(f"/api/projects/{project_id}")
    assert prepared.status_code == 200
    assert prepared.json()["map_ready"] is True
    assert prepared.json()["site_area_m2"] is None
    select_areas(project_id, [area("manual", "Ручной контур", [[5, 5], [80, 5], [80, 80], [5, 80]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200

    allowed = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    assert allowed.status_code == 200
    building_conflict = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 43, "y": 43})
    assert building_conflict.status_code == 400
    assert "наружной стены" in building_conflict.json()["message"]
    outside = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 85, "y": 20})
    assert outside.status_code == 400
    assert "выбранных рабочих областей" in outside.json()["message"]


def test_existing_green_is_a_hard_occupied_contour_for_manual_and_bulk_placement() -> None:
    project_id = prepare_project("Сохранение существующего озеленения")
    select_areas(project_id, [area("work", "Вся площадка", [[2, 2], [118, 2], [118, 88], [2, 88]])])
    opened = client.post(f"/api/projects/{project_id}/plan/manual")
    assert opened.status_code == 200

    overlapping = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 51, "y": 68})
    assert overlapping.status_code == 400
    assert "существующее озеленение" in overlapping.json()["message"].lower()

    preview = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
        "type": "fill",
        "base_plan_version": opened.json()["plan"]["version"],
        "plant_kind": "tree",
        "zone_ids": ["work"],
        "placement_mode": "count",
        "target_count": 60,
        "layout": "natural",
        "spacing_m": 5,
        "edge_offset_m": 1,
        "seed": 47,
    })
    assert preview.status_code == 200, preview.json()
    project = get_application().get(project_id)
    green = unary_union([
        shape(feature["geometry"])
        for feature in project.geometry.feature_collection["features"]
        if feature["properties"].get("kind") == "existing_green"
    ])
    additions = preview.json()["change_set"]["additions"]
    assert additions
    assert all(not green.intersects(Point(item["x"], item["y"]).buffer(item["radius"])) for item in additions)


def test_layer_meaning_can_be_corrected_after_plan_without_losing_work() -> None:
    project_id = prepare_project("Уточнение слоя после начала работы")
    select_areas(project_id, [area("work", "Рабочий участок", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200
    current = client.get(f"/api/projects/{project_id}").json()
    mappings = [{
        "layer_id": layer["id"],
        "kind": "restricted" if layer["source_name"] == "UTIL_WATER" else layer["mapped_kind"],
        "visible": layer["visible"],
    } for layer in current["layers"]]

    remapped = client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings})
    assert remapped.status_code == 200, remapped.json()
    assert len(remapped.json()["planting_zones"]) == 1
    assert len(remapped.json()["plan"]["objects"]) == 1
    prepare_map(project_id)

    recalculated = client.get(f"/api/projects/{project_id}").json()
    assert recalculated["map_ready"] is True
    assert recalculated["status"] == "editing"
    assert len(recalculated["planting_zones"]) == 1
    assert len(recalculated["plan"]["objects"]) == 1
    assert recalculated["source_geometry"] is None


def test_pp743_building_distance_is_measured_to_plant_axis() -> None:
    project_id = client.post("/api/projects", json={"name": "Отступ до оси"}).json()["id"]
    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("survey-fragment.dxf", boundaryless_content(), "application/dxf")},
    ).json()
    mappings = [{"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True} for layer in imported["layers"]]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200
    prepare_map(project_id)
    select_areas(project_id, [area("manual", "Ручной контур", [[5, 5], [80, 5], [80, 80], [5, 80]])])
    client.post(f"/api/projects/{project_id}/plan/manual")

    exact = client.post(f"/api/projects/{project_id}/plan/placement-check", json={"kind": "tree", "x": 33, "y": 43})
    too_close = client.post(f"/api/projects/{project_id}/plan/placement-check", json={"kind": "tree", "x": 33.01, "y": 43})

    assert exact.status_code == 200 and exact.json()["allowed"] is True
    assert too_close.status_code == 200 and too_close.json()["allowed"] is False
    assert "4.99" in too_close.json()["reason"]
    assert "5.00" in too_close.json()["reason"]


def test_manual_plan_requires_a_prepared_map_and_selected_area() -> None:
    project_id = client.post("/api/projects", json={"name": "Нельзя пропустить область"}).json()["id"]
    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("site.dxf", site_content(), "application/dxf")},
    )
    mappings = [{"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True} for layer in imported.json()["layers"]]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200

    before_map = client.post(f"/api/projects/{project_id}/plan/manual")
    assert before_map.status_code == 400
    assert "подготовьте карту" in before_map.json()["message"]
    prepare_map(project_id)

    before_area = client.post(f"/api/projects/{project_id}/plan/manual")
    assert before_area.status_code == 400
    assert "выберите хотя бы один участок" in before_area.json()["message"]


def test_selected_areas_cannot_silently_delete_an_existing_manual_plan() -> None:
    project_id = prepare_project("Защита ручной схемы")
    select_areas(project_id, [area("first", "Первый участок", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200

    replacement = client.put(
        f"/api/projects/{project_id}/planting-zones",
        json={"zones": [area("second", "Второй участок", [[62, 12], [72, 12], [72, 30], [62, 30]])]},
    )
    assert replacement.status_code == 400
    assert "Нельзя удалить участок, в котором уже есть посадки" in replacement.json()["message"]
    project = client.get(f"/api/projects/{project_id}").json()
    assert len(project["plan"]["objects"]) == 1


def test_manual_plan_blocks_an_unrequested_geometry_recalculation() -> None:
    project_id = prepare_project("Нельзя сдвинуть основу под планом")
    select_areas(project_id, [area("first", "Первый участок", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    project = client.get(f"/api/projects/{project_id}").json()
    mappings = [
        {"layer_id": layer["id"], "kind": layer["mapped_kind"], "visible": layer["visible"]}
        for layer in project["layers"]
    ]

    remap = client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings})
    background_recalculation = client.post(f"/api/projects/{project_id}/operations/geometry")

    assert remap.status_code == 200
    assert background_recalculation.status_code == 400
    assert "после открытия ручной схемы" in background_recalculation.json()["message"]


def test_invalid_drawn_area_is_rejected_without_replacing_saved_areas() -> None:
    project_id = prepare_project("Контур без автопочинки")
    select_areas(project_id, [area("saved", "Сохранённый участок", [[12, 12], [40, 12], [40, 30], [12, 30]])])

    invalid = client.put(
        f"/api/projects/{project_id}/planting-zones",
        json={"zones": [area("bow-tie", "Самопересечение", [[12, 12], [40, 30], [12, 30], [40, 12]])]},
    )

    assert invalid.status_code == 400
    assert "самопересекающийся" in invalid.json()["message"]
    project = client.get(f"/api/projects/{project_id}").json()
    assert [zone["id"] for zone in project["planting_zones"]] == ["saved"]


def test_selected_areas_remain_visible_when_geometry_is_created_by_manual_plan() -> None:
    project_id = prepare_project("Участки до расчёта")
    select_areas(project_id, [area("lawn", "Контур DXF: газон", [[12, 12], [60, 12], [60, 35], [12, 35]])])

    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    snapshot = client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": 0, "min_y": 0, "max_x": 120, "max_y": 80, "resolution": 1},
    )

    assert snapshot.status_code == 200
    assert any(feature["properties"].get("planting_zone_id") == "lawn" for feature in snapshot.json()["feature_collection"]["features"])


def test_replacing_selected_areas_invalidates_the_map_snapshot() -> None:
    project_id = prepare_project("Новая область вместо старой")
    prepare_map(project_id)
    select_areas(project_id, [area("first", "Первый контур", [[12, 12], [40, 12], [40, 30], [12, 30]])])
    first = client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": 0, "min_y": 0, "max_x": 120, "max_y": 80, "resolution": 1},
    )
    assert first.status_code == 200
    assert any(item["properties"].get("planting_zone_id") == "first" for item in first.json()["feature_collection"]["features"])

    select_areas(project_id, [area("second", "Второй контур", [[45, 12], [60, 12], [60, 30], [45, 30]])])
    second = client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": 0, "min_y": 0, "max_x": 120, "max_y": 80, "resolution": 1},
    )
    assert second.status_code == 200
    zone_ids = {
        item["properties"].get("planting_zone_id")
        for item in second.json()["feature_collection"]["features"]
        if item["properties"].get("kind") == "planting_area"
    }
    assert zone_ids == {"second"}


def test_changing_layer_meaning_discards_the_old_map_and_selected_areas() -> None:
    project_id = prepare_project("Новая трактовка слоя")
    select_areas(project_id, [area("work", "Старый участок", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": 0, "min_y": 0, "max_x": 120, "max_y": 80, "resolution": 1},
    ).status_code == 200
    assert project_id in get_application().geometry_query._indexes
    get_application().validator._position_checker(get_application().get(project_id))
    assert project_id in get_application().validator._position_checkers

    project = client.get(f"/api/projects/{project_id}").json()
    mappings = [
        {
            "layer_id": layer["id"],
            "kind": "ignore" if layer["mapped_kind"] == "building" else layer["mapped_kind"],
            "visible": layer["visible"],
        }
        for layer in project["layers"]
    ]
    assert any(item["kind"] == "ignore" for item in mappings)

    remapped = client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings})

    assert remapped.status_code == 200
    assert remapped.json()["geometry"] is None
    assert remapped.json()["planting_zones"] == []
    assert remapped.json()["status"] == "mapped"
    assert project_id not in get_application().geometry_query._indexes
    assert project_id not in get_application().validator._position_checkers
    manual = client.post(f"/api/projects/{project_id}/plan/manual")
    assert manual.status_code == 400
    assert "подготовьте карту" in manual.json()["message"]


@pytest.mark.parametrize("invalid_parameter, invalid_value", [
    ("min_x", "NaN"),
    ("min_y", "Infinity"),
    ("max_x", "NaN"),
    ("max_y", "-Infinity"),
    ("resolution", "NaN"),
])
def test_map_viewport_rejects_nonfinite_coordinates_before_geometry_query(invalid_parameter: str, invalid_value: str) -> None:
    project_id = prepare_project("Конечный viewport")
    params: dict[str, str | int] = {"min_x": 0, "min_y": 0, "max_x": 120, "max_y": 80, "resolution": 1}
    params[invalid_parameter] = invalid_value

    response = client.get(f"/api/projects/{project_id}/map-features", params=params)

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert invalid_parameter in response.json()["field_errors"]


def test_application_query_rejects_nonfinite_viewport_without_http_validation() -> None:
    project_id = prepare_project("Конечный viewport без HTTP")

    with pytest.raises(ValueError, match="конечными"):
        get_application().query_geometry(project_id, (float("nan"), 0, 120, 80), 1)


def test_manual_move_reassigns_object_between_selected_areas() -> None:
    project_id = prepare_project("Перенос между участками")
    select_areas(project_id, [
        area("lawn-a", "Контур DXF: газон A", [[12, 12], [60, 12], [60, 35], [12, 35]]),
        area("lawn-b", "Контур DXF: газон B", [[62, 12], [72, 12], [72, 30], [62, 30]]),
    ])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200

    added = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    assert added.status_code == 200
    object_id = added.json()["objects"][0]["id"]
    assert added.json()["objects"][0]["planting_zone_id"] == "lawn-a"

    moved = client.patch(f"/api/projects/{project_id}/plan/objects/{object_id}", json={"x": 66, "y": 20})
    assert moved.status_code == 200
    assert moved.json()["objects"][0]["planting_zone_id"] == "lawn-b"


def test_position_reports_untyped_network_as_unknown_not_safe() -> None:
    project_id = prepare_project("Неизвестная сеть")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [80, 12], [80, 55], [12, 55]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200

    known = client.post(f"/api/projects/{project_id}/plan/placement-check", json={"kind": "tree", "x": 20, "y": 20})
    assert known.status_code == 200
    assert known.json()["status"] == "allowed"

    unknown = client.post(f"/api/projects/{project_id}/plan/placement-check", json={"kind": "tree", "x": 75, "y": 40})
    assert unknown.status_code == 200
    assert unknown.json()["allowed"] is True
    assert unknown.json()["status"] == "unknown"
    assert unknown.json()["rule_id"] == "untyped_utility"

    added = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 75, "y": 40})
    assert added.status_code == 200
    assert added.json()["objects"][0]["status"] == "warning"

    assert any(item["code"] == "UNTYPED_UTILITY_REVIEW" for item in added.json()["issues"])


def test_unknown_candidate_does_not_occupy_preview_spacing_or_enter_automatic_plan() -> None:
    project_id = prepare_project("Неизвестная сеть в наборе")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [80, 12], [80, 55], [12, 55]])])
    base_version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]

    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": base_version,
        "source": "pattern",
        "label": "Проверка сети и безопасной позиции",
        "operations": [
            {"type": "add", "object": {"kind": "tree", "x": 75, "y": 40}},
            {"type": "add", "object": {"kind": "tree", "x": 77, "y": 40}},
        ],
    })

    assert preview.status_code == 200, preview.json()
    payload = preview.json()
    assert payload["can_apply"] is False
    assert [item["status"] for item in payload["candidate_results"]] == ["unknown", "allowed"]
    unknown = payload["candidate_results"][0]
    assert unknown["code"] == "UNTYPED_UTILITY_REVIEW"
    assert unknown["category"] == "data"
    assert unknown["source_layer"] == "UTIL_HEAT"
    assert unknown["source_feature_ids"]
    assert payload["candidate_results"][1]["code"] == "POSITION_ACCEPTED"
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []


def test_manual_edit_rejects_outside_and_verified_building_conflicts() -> None:
    project_id = prepare_project("Проверка расположения")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200

    outside = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": -10, "y": -10})
    assert outside.status_code == 400
    assert "границы проектирования" in outside.json()["message"]

    added = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    assert added.status_code == 200
    object_id = added.json()["objects"][0]["id"]

    outside_selected_area = client.patch(f"/api/projects/{project_id}/plan/objects/{object_id}", json={"x": 42, "y": 60})
    assert outside_selected_area.status_code == 400
    assert "выбранных рабочих областей" in outside_selected_area.json()["message"]


@pytest.mark.parametrize("payload", [
    '{"kind":"tree","x":NaN,"y":20}',
    '{"kind":"tree","x":20,"y":Infinity}',
    '{"kind":"tree","x":20,"y":20,"radius":NaN}',
])
def test_manual_edit_rejects_nonfinite_coordinates_before_geometry(payload: str) -> None:
    project_id = prepare_project("Только конечные координаты")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200

    response = client.post(
        f"/api/projects/{project_id}/plan/objects",
        content=payload,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []


def test_export_reopens_original_dxf_and_adds_only_planting_layers() -> None:
    project_id = prepare_project("Экспорт без подмены")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200

    artifact = client.post(f"/api/projects/{project_id}/exports")
    assert artifact.status_code == 200
    downloaded = client.get(artifact.json()["download_url"])
    assert downloaded.status_code == 200

    document = ezdxf.read(StringIO(downloaded.content.decode("utf-8")))
    assert len(document.modelspace().query('LWPOLYLINE[layer=="SITE_BORDER"]')) == 1
    planting = document.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')
    assert len(planting) == 1
    xdata = planting[0].get_xdata("GREEN_ATLAS")
    strings = [tag.value for tag in xdata if tag.code == 1000]
    assert "schema=green-atlas:1" in strings
    assert any(str(value).startswith("object_id=") for value in strings)
    assert not any(str(value).startswith(prefix) for value in strings for prefix in ("strategy=", "factor="))


def test_draft_release_is_reproducible_and_links_every_artifact_by_object_id() -> None:
    project_id = prepare_project("Воспроизводимый выпуск")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    first_plan = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).json()
    object_id = first_plan["objects"][0]["id"]

    first = client.post(f"/api/projects/{project_id}/releases", json={"mode": "draft", "scene_horizon": 20})
    assert first.status_code == 200, first.json()
    package = first.json()
    assert package["status"] == "draft"
    assert {item["kind"] for item in package["artifacts"]} == {"bundle", "dxf", "schedule", "manifest", "scene", "dendroplan"}
    state_after_first = int(first.headers["X-Project-State-Version"])

    second = client.post(f"/api/projects/{project_id}/releases", json={"mode": "draft", "scene_horizon": 20})
    assert second.status_code == 200
    assert second.json() == package
    assert int(second.headers["X-Project-State-Version"]) == state_after_first

    contents = {}
    for artifact in package["artifacts"]:
        downloaded = client.get(artifact["download_url"])
        assert downloaded.status_code == 200
        contents[artifact["kind"]] = downloaded.content
        assert downloaded.content == client.get(artifact["download_url"]).content

    schedule_rows = list(csv.DictReader(StringIO(contents["schedule"].decode("utf-8-sig"))))
    scene = json.loads(contents["scene"])
    manifest = json.loads(contents["manifest"])
    assert {row["object_id"] for row in schedule_rows} == {object_id}
    assert {item["object_id"] for item in scene["objects"]} == {object_id}
    assert manifest["objects"] == [object_id]
    assert f'data-object-id="{object_id}"'.encode() in contents["dendroplan"]

    document = ezdxf.read(StringIO(contents["dxf"].decode("utf-8")))
    planting = document.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')
    exported_ids = {
        tag.value.split("=", 1)[1]
        for entity in planting
        for tag in entity.get_xdata("GREEN_ATLAS")
        if tag.code == 1000 and str(tag.value).startswith("object_id=")
    }
    assert exported_ids == {object_id}
    with zipfile.ZipFile(BytesIO(contents["bundle"])) as archive:
        assert len(archive.namelist()) == 5
        assert all(info.date_time == (2026, 8, 28, 0, 0, 0) for info in archive.infolist())


def test_final_release_requires_species_and_networks_and_preserves_draft_scope() -> None:
    project_id = prepare_project("Финальный выпуск")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    plan = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).json()
    object_id = plan["objects"][0]["id"]

    blocked = client.post(f"/api/projects/{project_id}/releases", json={"mode": "final", "scene_horizon": 20})
    assert blocked.status_code == 400
    assert "назначьте виды" in blocked.json()["message"]

    assigned = client.patch(
        f"/api/projects/{project_id}/plan/objects/{object_id}",
        json={"species_revision_id": "tilia-cordata@2026-08-28.1", "size_class": "standard"},
    )
    assert assigned.status_code == 200, assigned.json()
    missing_basis = client.post(f"/api/projects/{project_id}/releases", json={"mode": "final", "scene_horizon": 20})
    assert missing_basis.status_code == 400
    assert "основания ПП-616 и ПП-1160" in missing_basis.json()["message"]

    release_request = {
        "mode": "final",
        "scene_horizon": 20,
        "regulatory_basis": {
            "pp616_status": "not_applicable",
            "pp616_reference": "Новые посадки без удаления существующих насаждений",
            "pp1160_status": "not_required",
            "pp1160_reference": "Удаление и пересадка не входят в эту ревизию",
            "confirmed_by": "Иванов И И",
        },
    }
    final = client.post(f"/api/projects/{project_id}/releases", json=release_request)
    assert final.status_code == 400, final.json()
    assert "проверки инженерных сетей" in final.json()["message"]
    draft = client.post(f"/api/projects/{project_id}/releases", json={**release_request, "mode": "draft"})
    assert draft.status_code == 200, draft.json()
    assert draft.json()["status"] == "draft"
    assert sum(warning.startswith("Инженерные сети:") for warning in draft.json()["warnings"]) == 1
    manifest_artifact = next(item for item in draft.json()["artifacts"] if item["kind"] == "manifest")
    manifest = client.get(manifest_artifact["download_url"]).json()
    assert manifest["missing_species_object_ids"] == []
    registry = manifest["regulatory_registry"]
    records = {item["id"]: item for item in registry["records"]}
    assert registry["revision"].startswith("moscow-greening-registry@")
    assert "pp616-compensation-process" in registry["applied_rule_ids"]
    assert "pp1160-permit-service" in registry["applied_rule_ids"]
    assert records["pp616-compensation-process"]["coverage"] == "partial"
    assert records["pp1160-permit-service"]["machine_checkable"] is False
    assert records["pp1160-permit-service"]["release_gate_machine_checkable"] is True
    assert "финального выпуска" in records["pp1160-permit-service"]["machine_checkable_scope"]
    assert manifest["regulatory_basis"]["confirmed_by"] == "Иванов И И"
    assert all("geometry_complete" not in layer for layer in manifest["layer_mappings"])
    assert all(layer["parsing_status"] in {"complete", "partial"} for layer in manifest["layer_mappings"])
    assert all("used_in_calculation" in layer for layer in manifest["layer_mappings"])
    assert "approved" not in json.dumps(manifest).lower()
    dxf_artifact = next(item for item in draft.json()["artifacts"] if item["kind"] == "dxf")
    document = ezdxf.read(StringIO(client.get(dxf_artifact["download_url"]).content.decode("utf-8")))
    tags = [tag.value for tag in document.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')[0].get_xdata("GREEN_ATLAS") if tag.code == 1000]
    assert "species_revision_id=tilia-cordata@2026-08-28.1" in tags
    assert "size_class=standard" in tags


def test_failed_export_storage_does_not_publish_a_new_project_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = prepare_project("Не публиковать несуществующий файл")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200

    def fail_storage(*_args: object, **_kwargs: object) -> None:
        raise OSError("Хранилище экспорта недоступно")

    monkeypatch.setattr(get_application().repository, "publish_export", fail_storage)
    failed = client.post(f"/api/projects/{project_id}/exports")

    assert failed.status_code == 400
    assert "Хранилище экспорта недоступно" in failed.json()["message"]
    assert client.get(f"/api/projects/{project_id}").json()["status"] == "editing"


def test_binary_source_survives_full_saved_manual_flow() -> None:
    source = binary_site_content()
    created = client.post("/api/projects", json={"name": "Бинарный полный путь"})
    project_id = created.json()["id"]
    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("binary-site.dxf", source, "application/dxf")},
    )
    assert imported.status_code == 200
    mappings = [{"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True} for layer in imported.json()["layers"]]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200
    prepare_map(project_id)
    select_areas(project_id, [area("whole", "Вся площадка", [[5, 5], [95, 5], [95, 95], [5, 95]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 30, "y": 30}).status_code == 200

    artifact = client.post(f"/api/projects/{project_id}/exports")
    downloaded = client.get(artifact.json()["download_url"])
    reopened = EzdxfReader().read("binary-plan.dxf", downloaded.content)

    assert get_application().repository.get_source(project_id) == source
    assert downloaded.content.startswith(b"AutoCAD Binary DXF")
    assert any(feature["properties"].get("source_layer") == "SOURCE_CONTEXT" for feature in reopened.geometry.feature_collection["features"])
    assert any(layer.source_name == "GREEN_ATLAS_TREES" for layer in reopened.layers)


def test_export_download_keeps_the_exact_saved_artifact_after_later_edits() -> None:
    project_id = prepare_project("Устойчивый экспорт")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200
    artifact = client.post(f"/api/projects/{project_id}/exports").json()
    assert client.get(f"/api/projects/{project_id}").json()["status"] == "editing"
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 40, "y": 20}).status_code == 200

    downloaded = client.get(artifact["download_url"])

    assert downloaded.status_code == 200
    assert len(ezdxf.read(StringIO(downloaded.content.decode("utf-8"))).modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')) == 1
    assert client.get(f"/api/projects/{project_id}").json()["status"] == "editing"
    assert client.get(f"/api/projects/{project_id}/exports/not-an-artifact/download").status_code == 404


def test_moving_or_deleting_after_export_also_returns_the_project_to_draft() -> None:
    project_id = prepare_project("Правки после экспорта")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    first = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).json()["objects"][0]["id"]
    second = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 40, "y": 20}).json()["objects"][1]["id"]

    assert client.post(f"/api/projects/{project_id}/exports").status_code == 200
    assert client.patch(f"/api/projects/{project_id}/plan/objects/{first}", json={"x": 22, "y": 20}).status_code == 200
    assert client.get(f"/api/projects/{project_id}").json()["status"] == "editing"

    assert client.post(f"/api/projects/{project_id}/exports").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects/delete", json={"ids": [second]}).status_code == 200
    assert client.get(f"/api/projects/{project_id}").json()["status"] == "editing"


def test_projects_can_be_deleted() -> None:
    project_id = client.post("/api/projects", json={"name": "Удаляемый проект"}).json()["id"]
    assert client.delete(f"/api/projects/{project_id}").status_code == 204
    assert client.get(f"/api/projects/{project_id}").status_code == 404


def test_renaming_a_working_area_preserves_plan_history_and_undo_context() -> None:
    project_id = prepare_project("История после имени участка")
    original = area("work", "Участок", [[12, 12], [60, 12], [60, 35], [12, 35]])
    select_areas(project_id, [original])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 40, "y": 20}).status_code == 200

    renamed = {**original, "label": "Главная аллея"}
    assert client.put(f"/api/projects/{project_id}/planting-zones", json={"zones": [renamed]}).status_code == 200
    history = client.get(f"/api/projects/{project_id}/plan/history").json()
    assert history["can_undo"] is True
    assert history["undo_label"] == "Добавление дерева"

    undone = client.post(f"/api/projects/{project_id}/plan/history/undo").json()
    assert len(undone["plan"]["objects"]) == 1
    assert undone["planting_zones"][0]["label"] == "Главная аллея"


def test_project_list_exposes_working_context_without_loading_map_geometry() -> None:
    project_id = prepare_project("Контекст в списке")
    select_areas(project_id, [area("work", "Главная аллея", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200

    summary = next(item for item in client.get("/api/projects").json() if item["id"] == project_id)

    assert summary["has_geometry"] is True
    assert summary["planting_zone_count"] == 1
    assert summary["plan_object_count"] == 1
    assert summary["plan_version"] >= 2


def test_change_set_previews_and_applies_several_objects_as_one_revision() -> None:
    project_id = prepare_project("Атомарный набор")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    opened = client.post(f"/api/projects/{project_id}/plan/manual").json()
    base_version = opened["plan"]["version"]
    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": base_version,
        "source": "group",
        "label": "Две посадки",
        "operations": [
            {"type": "add", "object": {"kind": "tree", "x": 20, "y": 20}},
            {"type": "add", "object": {"kind": "tree", "x": 30, "y": 20}},
        ],
    })

    assert preview.status_code == 200, preview.json()
    assert preview.json()["can_apply"] is True
    assert len(preview.json()["additions"]) == 2
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []

    payload = {
        "preview_id": preview.json()["id"],
        "digest": preview.json()["digest"],
        "base_plan_version": base_version,
    }
    applied = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json=payload)
    repeated = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json=payload)

    assert applied.status_code == 200, applied.json()
    assert repeated.status_code == 200, repeated.json()
    assert repeated.json() == applied.json()
    assert applied.json()["plan_version"] == base_version + 1
    assert len(applied.json()["added_ids"]) == 2
    assert len(client.get(f"/api/projects/{project_id}").json()["plan"]["objects"]) == 2
    undone = client.post(f"/api/projects/{project_id}/plan/history/undo")
    assert undone.status_code == 200
    assert undone.json()["plan"]["objects"] == []


def test_group_translation_checks_final_positions_without_colliding_with_its_old_footprint() -> None:
    project_id = prepare_project("Перемещение группы")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [72, 12], [72, 40], [12, 40]])])
    client.post(f"/api/projects/{project_id}/plan/manual")
    first = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).json()
    second = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 30, "y": 20}).json()
    objects = second["objects"]

    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": second["version"],
        "source": "group",
        "label": "Сдвиг группы",
        "operations": [
            {"type": "update", "object_id": objects[0]["id"], "changes": {"x": 26, "y": 20}},
            {"type": "update", "object_id": objects[1]["id"], "changes": {"x": 36, "y": 20}},
        ],
    })

    assert preview.status_code == 200, preview.json()
    assert preview.json()["can_apply"] is True
    assert [item["status"] for item in preview.json()["candidate_results"]] == ["allowed", "allowed"]


def test_blocked_change_set_reports_each_candidate_without_partial_save() -> None:
    project_id = prepare_project("Заблокированный набор")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    base_version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]

    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": base_version,
        "source": "group",
        "label": "Одна допустимая и одна ошибочная посадка",
        "operations": [
            {"type": "add", "object": {"kind": "tree", "x": 20, "y": 20}},
            {"type": "add", "object": {"kind": "tree", "x": -10, "y": -10}},
        ],
    })

    assert preview.status_code == 200
    assert preview.json()["can_apply"] is False
    assert [item["status"] for item in preview.json()["candidate_results"]] == ["allowed", "blocked"]
    rejected = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json={
        "preview_id": preview.json()["id"],
        "digest": preview.json()["digest"],
        "base_plan_version": base_version,
    })
    assert rejected.status_code == 400
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["plan"]["version"] == base_version
    assert project["plan"]["objects"] == []


def test_change_set_rejects_a_preview_after_the_plan_version_changes() -> None:
    project_id = prepare_project("Устаревший предпросмотр")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    base_version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]
    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": base_version,
        "source": "manual",
        "label": "Будущая посадка",
        "operations": [{"type": "add", "object": {"kind": "tree", "x": 30, "y": 20}}],
    }).json()
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200

    stale = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json={
        "preview_id": preview["id"],
        "digest": preview["digest"],
        "base_plan_version": base_version,
    })

    assert stale.status_code == 409
    assert stale.json()["code"] == "PLAN_VERSION_CONFLICT"
    assert len(client.get(f"/api/projects/{project_id}").json()["plan"]["objects"]) == 1


def test_change_set_rejects_a_preview_after_the_working_area_changes() -> None:
    project_id = prepare_project("Предпросмотр старого участка")
    select_areas(project_id, [area("work", "Большой участок", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    base_version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]
    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": base_version,
        "source": "manual",
        "label": "Посадка на старом участке",
        "operations": [{"type": "add", "object": {"kind": "tree", "x": 30, "y": 20}}],
    }).json()
    select_areas(project_id, [area("work", "Уменьшенный участок", [[12, 12], [25, 12], [25, 35], [12, 35]])])

    stale = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json={
        "preview_id": preview["id"],
        "digest": preview["digest"],
        "base_plan_version": base_version,
    })

    assert stale.status_code == 400
    assert "Рассчитайте изменения ещё раз" in stale.json()["message"]
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []


def test_change_set_preview_cannot_be_applied_to_another_project() -> None:
    source_id = prepare_project("Источник предпросмотра")
    target_id = prepare_project("Чужой проект")
    source_zone = area("work", "Участок", [[12, 12], [60, 12], [60, 35], [12, 35]])
    select_areas(source_id, [source_zone])
    select_areas(target_id, [source_zone])
    source_version = client.post(f"/api/projects/{source_id}/plan/manual").json()["plan"]["version"]
    target_version = client.post(f"/api/projects/{target_id}/plan/manual").json()["plan"]["version"]
    assert source_version == target_version
    preview = client.post(f"/api/projects/{source_id}/plan/change-sets/preview", json={
        "base_plan_version": source_version,
        "source": "manual",
        "label": "Посадка только в исходном проекте",
        "operations": [{"type": "add", "object": {"kind": "tree", "x": 30, "y": 20}}],
    }).json()

    foreign = client.post(f"/api/projects/{target_id}/plan/change-sets/apply", json={
        "preview_id": preview["id"],
        "digest": preview["digest"],
        "base_plan_version": target_version,
    })

    assert foreign.status_code == 400
    assert "другому проекту" in foreign.json()["message"]
    assert client.get(f"/api/projects/{target_id}").json()["plan"]["objects"] == []


def test_row_pattern_creates_many_sites_as_one_undoable_revision(monkeypatch) -> None:
    project_id = prepare_project("Ряд посадок")
    select_areas(project_id, [area("row-zone", "Линейный участок", [[12, 12], [65, 12], [65, 30], [12, 30]])])
    opened = client.post(f"/api/projects/{project_id}/plan/manual").json()
    base_version = opened["plan"]["version"]

    def unused_area_masks(*args, **kwargs):
        raise AssertionError("Row sampling must not construct unused safe-area masks")

    monkeypatch.setattr(get_application().evaluation, "automatic_generation_zones", unused_area_masks)
    pattern = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
        "type": "row",
        "base_plan_version": base_version,
        "plant_kind": "tree",
        "zone_ids": ["row-zone"],
        "axis": {"type": "LineString", "coordinates": [[18, 20], [60, 20]]},
        "spacing_m": 6,
    })

    assert pattern.status_code == 200, pattern.json()
    preview = pattern.json()["change_set"]
    assert pattern.json()["requested_count"] == 8
    assert pattern.json()["accepted_count"] == 7
    assert any("существующее озеленение" in item["reason"].lower() for item in pattern.json()["skipped"])
    existing_green = next(item for item in pattern.json()["skipped"] if item["code"] == "EXISTING_GREEN_OVERLAP")
    assert existing_green["status"] == "blocked"
    assert existing_green["category"] == "constraint"
    assert existing_green["source_layer"] == "GREEN_EXISTING"
    assert any(item["code"] == "EXISTING_GREEN_OVERLAP" and item["count"] >= 1 for item in pattern.json()["reason_summary"])
    assert preview["can_apply"] is True
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []

    applied = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json={
        "preview_id": preview["id"],
        "digest": preview["digest"],
        "base_plan_version": preview["base_plan_version"],
    })
    assert applied.status_code == 200, applied.json()
    assert applied.json()["plan_version"] == base_version + 1
    assert len(applied.json()["added_ids"]) == 7
    assert client.post(f"/api/projects/{project_id}/plan/history/undo").json()["plan"]["objects"] == []


def test_row_pattern_can_be_defined_by_count_instead_of_repeated_clicks() -> None:
    project_id = prepare_project("Ряд по количеству")
    select_areas(project_id, [area("row-zone", "Линейный участок", [[10, 10], [90, 10], [90, 40], [10, 40]])])
    version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]

    pattern = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
        "type": "row",
        "base_plan_version": version,
        "plant_kind": "shrub",
        "zone_ids": ["row-zone"],
        "axis": {"type": "LineString", "coordinates": [[15, 30], [85, 30]]},
        "placement_mode": "count",
        "target_count": 12,
        "spacing_policy": "canopy",
    })

    assert pattern.status_code == 200, pattern.json()
    assert pattern.json()["requested_count"] == 12
    assert pattern.json()["accepted_count"] > 0
    assert all(item["spacing_policy"] == "canopy" for item in pattern.json()["change_set"]["additions"])


def test_row_pattern_is_limited_to_the_selected_working_areas() -> None:
    project_id = prepare_project("Ряд в выбранном участке")
    select_areas(project_id, [
        area("west", "Запад", [[10, 10], [40, 10], [40, 40], [10, 40]]),
        area("east", "Восток", [[60, 10], [90, 10], [90, 40], [60, 40]]),
    ])
    version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]

    pattern = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
        "type": "row",
        "base_plan_version": version,
        "plant_kind": "shrub",
        "zone_ids": ["east"],
        "axis": {"type": "LineString", "coordinates": [[15, 30], [85, 30]]},
        "placement_mode": "count",
        "target_count": 12,
        "spacing_policy": "canopy",
    })

    assert pattern.status_code == 200, pattern.json()
    payload = pattern.json()
    assert payload["accepted_count"] > 0
    assert payload["change_set"] is not None
    assert {item["planting_zone_id"] for item in payload["change_set"]["additions"]} == {"east"}
    outside = [item for item in payload["skipped"] if item["code"] == "OUTSIDE_SELECTED_ZONE"]
    assert outside
    assert all(item["x"] < 60 for item in outside)
    assert any(item["code"] == "OUTSIDE_SELECTED_ZONE" for item in payload["reason_summary"])


def test_fill_and_row_previews_are_non_persistent_growth_ready_map_ghosts() -> None:
    """One preview response is enough for a pre-apply map and year slider.

    The client deliberately does not send the display year to the spatial
    engine: hard constraints and candidate positions stay identical while it
    interpolates the returned versioned forecast anchors locally.
    """
    project_id = prepare_project("Предпросмотр до сохранения")
    select_areas(project_id, [
        area("work", "Рабочая область", [[12, 12], [65, 12], [65, 42], [12, 42]]),
    ])
    opened = client.post(f"/api/projects/{project_id}/plan/manual").json()
    before = client.get(f"/api/projects/{project_id}").json()
    species_id = next(
        item["id"] for item in client.get("/api/species", params={"kind": "tree"}).json()
        if item["species_id"] == "sorbus-aucuparia"
    )
    common = {
        "base_plan_version": opened["plan"]["version"],
        "plant_kind": "tree",
        "zone_ids": ["work"],
        "placement_mode": "count",
        "target_count": 8,
        "size_class": "standard",
        "species_revision_id": species_id,
        "spacing_policy": "balanced",
    }
    requests = [
        {
            **common,
            "type": "fill",
            "layout": "natural",
            "spacing_m": 6,
            "edge_offset_m": 1,
            "seed": 47,
        },
        {
            **common,
            "type": "row",
            "axis": {"type": "LineString", "coordinates": [[18, 20], [60, 20]]},
        },
    ]
    observed_skips = []

    for request in requests:
        response = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json=request)

        assert response.status_code == 200, response.json()
        preview = response.json()
        additions = preview["change_set"]["additions"]
        assert preview["type"] == request["type"]
        assert preview["accepted_count"] == len(additions) > 0
        assert all(item["planting_zone_id"] == "work" for item in additions)
        assert all(item["species_revision_id"] == species_id for item in additions)
        assert all(
            [forecast["horizon_year"] for forecast in item["canopy_forecast"]]
            == [0, 5, 10, 15, 20, 30, 40]
            for item in additions
        )
        assert all(
            [forecast["horizon_year"] for forecast in item["root_forecast"]]
            == [0, 5, 10, 15, 20, 30, 40]
            for item in additions
        )
        arbitrary_year = forecast_at(
            [GrowthEnvelopeForecast.model_validate(item) for item in additions[0]["canopy_forecast"]],
            23,
        )
        assert arbitrary_year is not None
        assert arbitrary_year.horizon_year == 23
        assert all(
            item["status"] in {"blocked", "soft_conflict", "unknown"} and item["reason"]
            for item in preview["skipped"]
        )
        observed_skips.extend(preview["skipped"])

    assert any(item["status"] == "blocked" and item["category"] == "constraint" for item in observed_skips)

    after = client.get(f"/api/projects/{project_id}").json()
    assert after["state_version"] == before["state_version"]
    assert after["plan"]["version"] == before["plan"]["version"]
    assert after["plan"]["objects"] == before["plan"]["objects"] == []


def test_fill_pattern_is_deterministic_across_multiple_zones_and_reports_skips() -> None:
    project_id = prepare_project("Заполнение участков")
    zones = [
        area("west", "Запад", [[12, 12], [36, 12], [36, 36], [12, 36]]),
        area("east", "Восток", [[42, 12], [66, 12], [66, 36], [42, 36]]),
    ]
    select_areas(project_id, zones)
    base_version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 18, "y": 18}).status_code == 200
    current_version = client.get(f"/api/projects/{project_id}").json()["plan"]["version"]
    request = {
        "type": "fill",
        "base_plan_version": current_version,
        "plant_kind": "tree",
        "zone_ids": ["west", "east"],
        "placement_mode": "count",
        "target_count": 8,
        "layout": "natural",
        "spacing_m": 6,
        "edge_offset_m": 2,
        "angle_deg": 15,
        "seed": 47,
    }

    first = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json=request)
    second = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json=request)

    assert first.status_code == 200, first.json()
    assert second.status_code == 200, second.json()
    first_coordinates = [(item["x"], item["y"]) for item in first.json()["change_set"]["additions"]]
    second_coordinates = [(item["x"], item["y"]) for item in second.json()["change_set"]["additions"]]
    assert first_coordinates == second_coordinates
    assert first.json()["requested_count"] == first.json()["accepted_count"] == 8
    assert first.json()["change_set"]["can_apply"] is True
    assert all(any(shape(zone["geometry"]).covers(Point(x, y)) for zone in zones) for x, y in first_coordinates)
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["version"] == current_version


def test_placement_masks_are_discoverable_and_keep_every_preview_candidate_safe() -> None:
    project_id = prepare_project("Маски озеленения")
    select_areas(project_id, [area("work", "Рабочая область", [[2, 2], [95, 2], [95, 82], [2, 82]])])
    version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]

    catalog = client.get(f"/api/projects/{project_id}/plan/placement-masks")

    assert catalog.status_code == 200
    assert {item["id"] for item in catalog.json()} == {"road_edges", "regular_grid", "cluster_groves"}
    assert next(item for item in catalog.json() if item["id"] == "road_edges")["available"] is True

    project = get_application().get(project_id)
    checker = PositionChecker(project)
    requests = [
        {
            "mask_id": "road_edges",
            "road_offset_m": 5,
            "spacing_m": 6,
            "edge_offset_m": 0,
            "target_count": 12,
        },
        {
            "mask_id": "regular_grid",
            "spacing_m": 6,
            "edge_offset_m": 2,
            "target_count": 12,
        },
        {
            "mask_id": "cluster_groves",
            "spacing_m": 6,
            "edge_offset_m": 2,
            "cluster_gap_m": 22,
            "cluster_size": 7,
            "target_count": 12,
            "seed": 17,
        },
    ]
    observed_reasons: set[str] = set()
    regular_grid_coordinates: list[tuple[float, float]] = []
    for mask in requests:
        response = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
            "type": "mask",
            "base_plan_version": version,
            "plant_kind": "tree",
            "zone_ids": ["work"],
            "placement_mode": "count",
            "spacing_policy": "balanced",
            **mask,
        })

        assert response.status_code == 200, response.json()
        payload = response.json()
        assert payload["type"] == "mask"
        assert payload["mask_id"] == mask["mask_id"]
        assert payload["change_set"] is not None
        assert payload["change_set"]["can_apply"] is True
        assert payload["accepted_count"] == len(payload["change_set"]["additions"]) > 0
        observed_reasons.update(item["code"] for item in payload["reason_summary"])
        # This is the same hard checker used by manual editing. A mask cannot
        # bypass site/zone, road, building or occupied-contour restrictions.
        for item in payload["change_set"]["additions"]:
            assert checker.check(item["x"], item["y"], item["radius"], item["kind"]) is None
            assert item["planting_zone_id"] == "work"
        additions = [PlanObject.model_validate(item) for item in payload["change_set"]["additions"]]
        if mask["mask_id"] == "regular_grid":
            regular_grid_coordinates = [(item.x, item.y) for item in additions]
        assert all(
            hypot(first.x - second.x, first.y - second.y) + 1e-6 >= required_spacing(first, second)
            for index, first in enumerate(additions)
            for second in additions[:index]
        )

    # The source contains an untyped utility layer. Automatic masks surface
    # and skip those candidates instead of treating missing network evidence
    # as permission to place.
    assert "UNTYPED_UTILITY_REVIEW" in observed_reasons
    assert max(y for _, y in regular_grid_coordinates) - min(y for _, y in regular_grid_coordinates) > 40

    # Preview remains non-persistent until the operator explicitly applies it.
    restored = client.get(f"/api/projects/{project_id}").json()
    assert restored["plan"]["version"] == version
    assert restored["plan"]["objects"] == []


def test_count_fill_is_bounded_and_spans_a_large_area() -> None:
    zone = PlantingZoneAssignment.model_validate(area("large", "Большая область", [[0, 0], [2000, 0], [2000, 2000], [0, 2000]]))
    candidates = generate_fill(FillPatternRequest(
        base_plan_version=1,
        zone_ids=["large"],
        placement_mode="count",
        target_count=500,
        layout="natural",
        spacing_m=5,
        edge_offset_m=0,
    ), [zone])

    assert len(candidates) == 500
    assert max(item.x for item in candidates) - min(item.x for item in candidates) > 1800
    assert max(item.y for item in candidates) - min(item.y for item in candidates) > 1800
    assert len({round(item.x % 5, 3) for item in candidates[:30]}) > 10


def test_fill_reports_when_safe_geometry_cannot_hold_the_requested_count() -> None:
    project_id = prepare_project("Ограниченная ёмкость")
    select_areas(project_id, [area("small", "Малый участок", [[20, 20], [30, 20], [30, 30], [20, 30]])])
    version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]

    response = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
        "type": "fill",
        "base_plan_version": version,
        "plant_kind": "tree",
        "zone_ids": ["small"],
        "placement_mode": "count",
        "target_count": 20,
        "layout": "natural",
        "spacing_m": 6,
        "edge_offset_m": 1,
        "seed": 47,
    })

    assert response.status_code == 200, response.json()
    payload = response.json()
    assert payload["accepted_count"] < payload["requested_count"]
    assert any(item["code"] == "SAFE_CAPACITY_REACHED" for item in payload["reason_summary"])


def test_fill_density_intent_derives_spacing_from_the_selected_species() -> None:
    project_id = prepare_project("Плотность по кроне")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [72, 12], [72, 58], [12, 58]])])
    version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]
    species_id = next(
        item["id"] for item in client.get("/api/species", params={"kind": "tree"}).json()
        if item["species_id"] == "sorbus-aucuparia"
    )
    common = {
        "type": "fill",
        "base_plan_version": version,
        "plant_kind": "tree",
        "zone_ids": ["work"],
        "placement_mode": "count",
        "target_count": 80,
        "layout": "natural",
        "spacing_m": 6,
        "edge_offset_m": 1,
        "seed": 47,
        "size_class": "standard",
        "species_revision_id": species_id,
    }

    dense = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={**common, "spacing_policy": "canopy"})
    open_ = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={**common, "spacing_policy": "open"})

    assert dense.status_code == open_.status_code == 200
    assert dense.json()["effective_spacing_m"] < open_.json()["effective_spacing_m"]
    assert dense.json()["accepted_count"] >= open_.json()["accepted_count"]


def test_primary_fill_can_preview_a_deterministic_mixed_group_with_versioned_species() -> None:
    project_id = prepare_project("Смешанная группа")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [88, 12], [88, 88], [12, 88]])])
    version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]
    tree_species = next(
        item for item in client.get("/api/species", params={"kind": "tree"}).json()
        if item["species_id"] == "sorbus-aucuparia"
    )
    shrub_species = next(
        item for item in client.get("/api/species", params={"kind": "shrub"}).json()
        if item["species_id"] == "cornus-alba"
    )
    request = {
        "type": "fill",
        "base_plan_version": version,
        "plant_kind": "tree",
        "composition": "mixed",
        "tree_share": 0.65,
        "tree_species_revision_id": tree_species["id"],
        "shrub_species_revision_id": shrub_species["id"],
        "zone_ids": ["work"],
        "placement_mode": "count",
        "target_count": 24,
        "layout": "natural",
        "spacing_m": 5,
        "edge_offset_m": 2,
        "seed": 47,
        "spacing_policy": "canopy",
    }

    first = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json=request)
    second = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json=request)

    assert first.status_code == second.status_code == 200
    first_objects = first.json()["change_set"]["additions"]
    second_objects = second.json()["change_set"]["additions"]
    assert [(item["kind"], item["x"], item["y"]) for item in first_objects] == [
        (item["kind"], item["x"], item["y"]) for item in second_objects
    ]
    assert {item["kind"] for item in first_objects} == {"tree", "shrub"}
    assert {item["species_revision_id"] for item in first_objects if item["kind"] == "tree"} == {tree_species["id"]}
    assert {item["species_revision_id"] for item in first_objects if item["kind"] == "shrub"} == {shrub_species["id"]}
    assert first.json()["generated_count"] >= first.json()["accepted_count"]
    assert first.json()["capacity_shortfall"] == max(0, 24 - first.json()["accepted_count"])

    open_trees = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
        **request,
        "composition": "trees",
        "species_revision_id": tree_species["id"],
        "tree_species_revision_id": None,
        "shrub_species_revision_id": None,
        "spacing_policy": "open",
    })
    assert open_trees.status_code == 200, open_trees.json()
    assert first.json()["effective_spacing_m"] < open_trees.json()["effective_spacing_m"]
    assert first.json()["accepted_count"] >= open_trees.json()["accepted_count"]


def test_versioned_species_assignment_adds_bounded_canopy_and_root_forecasts() -> None:
    catalog = client.get("/api/species", params={"kind": "tree"})
    assert catalog.status_code == 200
    assert len(catalog.json()) >= 8
    lime = next(item for item in catalog.json() if item["species_id"] == "tilia-cordata")
    assert lime["id"].endswith("@2026-08-28.1")
    assert len(lime["source_urls"]) >= 2

    project_id = prepare_project("Породы и рост")
    select_areas(project_id, [
        area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]]),
        area("small", "Малый участок", [[70, 12], [80, 12], [80, 22], [70, 22]]),
    ])
    opened = client.post(f"/api/projects/{project_id}/plan/manual").json()
    added = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).json()
    object_id = added["objects"][0]["id"]
    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": added["version"],
        "source": "group",
        "label": "Назначение породы",
        "operations": [{"type": "update", "object_id": object_id, "changes": {"species_revision_id": lime["id"], "size_class": "standard"}}],
    })
    assert preview.status_code == 200, preview.json()
    forecast = preview.json()["updates"][0]
    assert [item["horizon_year"] for item in forecast["canopy_forecast"]] == [0, 5, 10, 15, 20, 30, 40]
    assert [item["horizon_year"] for item in forecast["root_forecast"]] == [0, 5, 10, 15, 20, 30, 40]
    assert all(item["radius_min_m"] <= item["radius_max_m"] for item in forecast["root_forecast"])
    assert all("не норматив" in item["basis"] for item in forecast["canopy_forecast"])

    shortlist = client.post(f"/api/projects/{project_id}/species/shortlist", json={"object_ids": [object_id]})
    assert shortlist.status_code == 200
    assert any(item["species"]["id"] == lime["id"] and item["status"] == "review" for item in shortlist.json())

    zone_shortlist = client.post(f"/api/projects/{project_id}/species/shortlist", json={"zone_ids": ["work"]})
    assert zone_shortlist.status_code == 200, zone_shortlist.json()
    assert {item["species"]["kind"] for item in zone_shortlist.json()} == {"tree", "shrub"}
    rowan = next(item for item in zone_shortlist.json() if item["species"]["species_id"] == "sorbus-aucuparia")
    assert rowan["status"] == "available"
    assert rowan["selected_area_m2"] == 1104.0
    assert rowan["estimated_safe_area_m2"] > 0
    assert rowan["estimated_capacity"] > 0
    assert rowan["reasons"][0] == "Предварительный выбор для 1 выбранных участков"
    assert "Ориентировочно до" in rowan["reasons"][1]
    assert rowan["reasons"][2] == "Корневая архитектура учтена прогнозным диапазоном"

    small_shortlist = client.post(f"/api/projects/{project_id}/species/shortlist", json={"zone_ids": ["small"]})
    assert small_shortlist.status_code == 200, small_shortlist.json()
    small_rowan = next(item for item in small_shortlist.json() if item["species"]["species_id"] == "sorbus-aucuparia")
    assert small_rowan["selected_area_m2"] == 100.0
    assert small_rowan["estimated_capacity"] < rowan["estimated_capacity"]

    missing_zone = client.post(f"/api/projects/{project_id}/species/shortlist", json={"zone_ids": ["missing"]})
    assert missing_zone.status_code == 400
    assert missing_zone.json()["code"] == "BAD_REQUEST"


def test_species_kind_mismatch_is_blocked_without_changing_the_plan() -> None:
    project_id = prepare_project("Несовместимая порода")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    opened = client.post(f"/api/projects/{project_id}/plan/manual").json()
    shrub = client.get("/api/species", params={"kind": "shrub"}).json()[0]
    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": opened["plan"]["version"],
        "source": "manual",
        "label": "Ошибочная порода",
        "operations": [{"type": "add", "object": {"kind": "tree", "x": 20, "y": 20, "species_revision_id": shrub["id"]}}],
    })
    assert preview.status_code == 200
    assert preview.json()["can_apply"] is False
    assert "не соответствует" in preview.json()["candidate_results"][0]["reason"]
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []


def test_recommendation_is_one_explainable_atomic_draft_and_preserves_locked_sites() -> None:
    project_id = prepare_project("Объяснимое предложение")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [72, 12], [72, 52], [12, 52]])])
    opened = client.post(f"/api/projects/{project_id}/plan/manual").json()
    locked = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).json()
    locked_id = locked["objects"][0]["id"]
    locked_plan = client.patch(
        f"/api/projects/{project_id}/plan/objects/{locked_id}",
        json={"locked": True},
    ).json()

    response = client.post(f"/api/projects/{project_id}/plan/recommendations/preview", json={
        "base_plan_version": locked_plan["version"],
        "zone_ids": ["work"],
        "profile": "balanced",
        "max_sites": 12,
    })

    assert response.status_code == 200, response.json()
    proposal = response.json()
    preview = proposal["change_set"]
    assert preview["source"] == "recommendation"
    assert 1 <= len(preview["additions"]) <= 12
    assert len(proposal["explanations"]) == len(preview["additions"])
    assert proposal["evidence"]["sunlight"] == "missing"
    assert proposal["evidence"]["soil"] == "missing"
    assert all(not explanation["biological_risks"] for explanation in proposal["explanations"])
    assert all(
        effect["status"] == "unknown" and effect["value"] is None
        for explanation in proposal["explanations"]
        for effect in explanation["effects"]
    )
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["version"] == locked_plan["version"]

    applied = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json={
        "preview_id": preview["id"],
        "digest": preview["digest"],
        "base_plan_version": preview["base_plan_version"],
    })
    assert applied.status_code == 200, applied.json()
    assert applied.json()["plan_version"] == locked_plan["version"] + 1
    added_ids = {item["id"] for item in preview["additions"]}
    added_issues = [item for item in applied.json()["plan"]["issues"] if item["object_id"] in added_ids]
    assert all(item["code"] == "NETWORK_CONTEXT_UNKNOWN" and item["severity"] == "warning" for item in added_issues)
    review_ids = {item["object_id"] for item in added_issues}
    assert all(item["status"] == ("warning" if item["id"] in review_ids else "valid") for item in applied.json()["plan"]["objects"] if item["id"] in added_ids)
    preserved = next(item for item in applied.json()["plan"]["objects"] if item["id"] == locked_id)
    assert preserved["locked"] is True
    undone = client.post(f"/api/projects/{project_id}/plan/history/undo").json()
    assert [item["id"] for item in undone["plan"]["objects"]] == [locked_id]


def test_recommendation_is_deterministic_and_stale_versions_are_rejected() -> None:
    project_id = prepare_project("Повторяемое предложение")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [72, 12], [72, 52], [12, 52]])])
    version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]
    payload = {"base_plan_version": version, "zone_ids": ["work"], "profile": "continuity", "max_sites": 20}

    first = client.post(f"/api/projects/{project_id}/plan/recommendations/preview", json=payload)
    second = client.post(f"/api/projects/{project_id}/plan/recommendations/preview", json=payload)
    assert first.status_code == second.status_code == 200
    assert [(item["x"], item["y"]) for item in first.json()["change_set"]["additions"]] == [
        (item["x"], item["y"]) for item in second.json()["change_set"]["additions"]
    ]
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200
    stale = client.post(f"/api/projects/{project_id}/plan/recommendations/preview", json=payload)
    assert stale.status_code == 409
    assert stale.json()["code"] == "PLAN_VERSION_CONFLICT"


def test_brush_subtracts_saved_objects_but_preserves_locked_objects_in_one_revision() -> None:
    project_id = prepare_project("Кисть посадок")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [72, 12], [72, 58], [12, 58]])])
    client.post(f"/api/projects/{project_id}/plan/manual")
    first = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).json()
    locked_id = first["objects"][0]["id"]
    locked = client.patch(f"/api/projects/{project_id}/plan/objects/{locked_id}", json={"locked": True}).json()
    second = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 30, "y": 20}).json()
    removable_id = next(item["id"] for item in second["objects"] if item["id"] != locked_id)

    response = client.post(f"/api/projects/{project_id}/plan/brush/preview", json={
        "base_plan_version": second["version"],
        "zone_ids": ["work"],
        "strokes": [
            {"mode": "add", "geometry": {"type": "LineString", "coordinates": [[16, 44], [66, 44]]}},
            {"mode": "subtract", "geometry": {"type": "LineString", "coordinates": [[16, 20], [34, 20]]}},
        ],
        "width_m": 6,
        "spacing_m": 5,
        "density": "dense",
        "composition": "mixed",
        "tree_share": 0.6,
        "seed": 71,
    })
    assert response.status_code == 200, response.json()
    brush = response.json()
    preview = brush["change_set"]
    assert brush["added_count"] > 4
    assert brush["removed_count"] == 1
    assert removable_id in preview["deletion_ids"]
    assert locked_id not in preview["deletion_ids"]
    assert any(item["code"] == "LOCKED_OBJECT" for item in brush["skipped"])
    assert {item["kind"] for item in preview["additions"]} == {"tree", "shrub"}

    applied = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json={
        "preview_id": preview["id"],
        "digest": preview["digest"],
        "base_plan_version": preview["base_plan_version"],
    })
    assert applied.status_code == 200, applied.json()
    assert any(item["id"] == locked_id and item["locked"] for item in applied.json()["plan"]["objects"])
    assert all(item["id"] != removable_id for item in applied.json()["plan"]["objects"])
    undone = client.post(f"/api/projects/{project_id}/plan/history/undo").json()["plan"]
    assert {item["id"] for item in undone["objects"]} == {locked_id, removable_id}


def test_brush_density_depends_on_geometry_not_pointer_event_frequency() -> None:
    project_id = prepare_project("Стабильная кисть")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [72, 12], [72, 58], [12, 58]])])
    version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]
    base = {
        "base_plan_version": version,
        "zone_ids": ["work"],
        "width_m": 10,
        "spacing_m": 4,
        "density": "balanced",
        "composition": "trees",
        "seed": 19,
    }
    sparse_events = client.post(f"/api/projects/{project_id}/plan/brush/preview", json={**base, "strokes": [{"mode": "add", "geometry": {"type": "LineString", "coordinates": [[16, 36], [66, 36]]}}]})
    many_events = client.post(f"/api/projects/{project_id}/plan/brush/preview", json={**base, "strokes": [{"mode": "add", "geometry": {"type": "LineString", "coordinates": [[16, 36], [26, 36], [36, 36], [46, 36], [56, 36], [66, 36]]}}]})
    assert sparse_events.status_code == many_events.status_code == 200
    assert [(item["x"], item["y"]) for item in sparse_events.json()["change_set"]["additions"]] == [
        (item["x"], item["y"]) for item in many_events.json()["change_set"]["additions"]
    ]
    assert sparse_events.json()["accepted_count"] <= 500


def test_scene_uses_stable_object_ids_local_coordinates_and_growth_horizons() -> None:
    project_id = prepare_project("Параметрическая сцена")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [72, 12], [72, 58], [12, 58]])])
    client.post(f"/api/projects/{project_id}/plan/manual")
    first_plan = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).json()
    second_plan = client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "shrub", "x": 60, "y": 40}).json()
    tree_id = next(item["id"] for item in second_plan["objects"] if item["kind"] == "tree")
    species = next(item for item in client.get("/api/species", params={"kind": "tree"}).json() if item["species_id"] == "tilia-cordata")
    preview = client.post(f"/api/projects/{project_id}/plan/change-sets/preview", json={
        "base_plan_version": second_plan["version"],
        "source": "manual",
        "label": "Порода для сцены",
        "operations": [{"type": "update", "object_id": tree_id, "changes": {"species_revision_id": species["id"], "size_class": "standard"}}],
    }).json()
    applied = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json={"preview_id": preview["id"], "digest": preview["digest"], "base_plan_version": preview["base_plan_version"]})
    assert applied.status_code == 200

    unproven = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 0})
    assert unproven.status_code == 200
    assert unproven.json()["georeference_status"] == "missing"
    assert unproven.json()["georeference_evidence"] == {
        "status": "missing",
        "coverage": "none",
        "source": None,
        "note": "Система координат не указана",
    }
    assert unproven.json()["terrain_evidence"]["status"] == "missing"
    assert unproven.json()["terrain_evidence"]["coverage"] == "none"
    assert unproven.json()["terrain_evidence"]["source"] is None
    assert unproven.json()["building_heights_status"] == "missing"
    assert unproven.json()["building_height_evidence"]["status"] == "missing"
    assert unproven.json()["building_height_evidence"]["coverage"] == "none"
    assert unproven.json()["building_height_evidence"]["source"] is None
    assert all(
        item["height_m"] is None and item["height_status"] == "missing"
        for item in unproven.json()["context_features"]
        if item["kind"] == "building"
    )

    project = get_application().get(project_id)
    project.coordinate_reference = CoordinateReference(
        status="verified",
        crs_id="EPSG:32637",
        name="WGS 84 / UTM zone 37N",
        source="control_points",
        axis_order="xy",
        control_points_count=3,
        evidence="Сверено по трём контрольным точкам.",
    )
    building_count = 0
    for feature in project.geometry.feature_collection["features"]:
        if feature.get("properties", {}).get("kind") == "building":
            building_count += 1
            feature["properties"]["source_extrusion_height_m"] = 9.75
    assert building_count > 0
    get_application().repository.save(project)

    current = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 0})
    early = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 1})
    future = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 20})
    interpolated = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 23})
    terminal = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 40})
    assert current.status_code == early.status_code == future.status_code == interpolated.status_code == terminal.status_code == 200
    current_scene, early_scene, future_scene, interpolated_scene = current.json(), early.json(), future.json(), interpolated.json()
    assert current_scene["coordinate_origin"] == [40.0, 30.0]
    assert current_scene["completeness"] == "partial"
    assert current_scene["terrain_status"] == "missing"
    assert current_scene["terrain_elevation_m"] is None
    assert current_scene["coordinate_reference"]["crs_id"] == "EPSG:32637"
    assert current_scene["georeference_status"] == "confirmed"
    assert current_scene["georeference_evidence"] == {
        "status": "confirmed",
        "coverage": "full",
        "source": "control_points",
        "note": "Сверено по трём контрольным точкам.",
    }
    assert current_scene["geometry_source"] == "prepared_geometry"
    assert current_scene["geometry_source_file_name"] == "site.dxf"
    assert current_scene["building_heights_status"] == "confirmed"
    assert current_scene["building_height_evidence"]["status"] == "confirmed"
    assert current_scene["building_height_evidence"]["coverage"] == "full"
    assert current_scene["building_height_evidence"]["source"] == "dxf_extrusion"
    assert current_scene["building_feature_count"] > 0
    assert current_scene["building_height_confirmed_count"] == current_scene["building_feature_count"]
    assert isinstance(current_scene["context_features"], list)
    assert {item["kind"] for item in current_scene["context_features"]}.intersection({"building", "road", "site_border"})
    assert all(
        item["base_elevation_status"] == ("confirmed" if item["base_elevation_m"] is not None else "missing")
        and item["base_elevation_source"] == ("dxf_elevation" if item["base_elevation_m"] is not None else None)
        for item in current_scene["context_features"]
    )
    assert {item["object_id"] for item in current_scene["objects"]} == {item["id"] for item in applied.json()["plan"]["objects"]}
    current_tree = next(item for item in current_scene["objects"] if item["object_id"] == tree_id)
    early_tree = next(item for item in early_scene["objects"] if item["object_id"] == tree_id)
    future_tree = next(item for item in future_scene["objects"] if item["object_id"] == tree_id)
    assert early_tree["height_max_m"] is not None
    assert current_tree["species_id"] == "tilia-cordata"
    assert current_tree["scientific_name"] == "Tilia cordata"
    assert current_tree["size_class"] == "standard"
    assert current_tree["model_variant_key"] == "species:tilia-cordata"
    assert current_tree["growth_stage"] == "young"
    assert current_tree["growth_stage_status"] == "estimated"
    assert current_tree["forecast_horizon_year"] == 0
    assert current_tree["height_status"] == "estimated"
    assert future_tree["growth_stage_status"] == "estimated"
    assert future_tree["forecast_horizon_year"] == 20
    assert future_tree["canopy_radius_max_m"] > current_tree["canopy_radius_max_m"]
    assert future_tree["height_max_m"] is not None
    interpolated_tree = next(item for item in interpolated_scene["objects"] if item["object_id"] == tree_id)
    assert interpolated_tree["canopy_radius_min_m"] == 3.423
    assert interpolated_tree["canopy_radius_max_m"] == 7.0
    assert interpolated_tree["height_max_m"] is not None
    assert max(abs(item["local_x"]) for item in future_scene["objects"]) == 20
    assert client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 41}).status_code == 422


def test_scene_api_exposes_source_xyz_without_claiming_unmapped_mesh_is_terrain() -> None:
    document = ezdxf.new("R2013", setup=True)
    document.units = ezdxf.units.CM
    document.layers.add("SITE_BORDER", color=1)
    document.layers.add("SURVEY_SURFACE", color=3)
    modelspace = document.modelspace()
    modelspace.add_lwpolyline(
        [(0, 0), (10_000, 0), (10_000, 10_000), (0, 10_000)],
        close=True,
        dxfattribs={"layer": "SITE_BORDER"},
    )
    face = modelspace.add_3dface(
        [(1000, 2000, 310), (2000, 2000, 320), (2000, 3000, 360), (1000, 3000, 350)],
        dxfattribs={"layer": "SURVEY_SURFACE"},
    )
    stream = StringIO()
    document.write(stream)

    created = client.post("/api/projects", json={"name": "XYZ API"})
    project_id = created.json()["id"]
    imported = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("survey-cm.dxf", stream.getvalue().encode(), "application/dxf")},
    )
    assert imported.status_code == 200
    mappings = [
        {"layer_id": layer["id"], "kind": layer["suggested_kind"], "visible": True}
        for layer in imported.json()["layers"]
    ]
    assert client.put(f"/api/projects/{project_id}/layer-mappings", json={"mappings": mappings}).status_code == 200
    prepare_map(project_id)
    select_areas(project_id, [area("work", "Рабочая область", [[5, 5], [80, 5], [80, 80], [5, 80]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200

    response = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 0})
    assert response.status_code == 200
    scene = response.json()
    primitive = next(item for item in scene["vertical_primitives"] if item["source_handle"] == str(face.dxf.handle))

    # XY is translated to the scene origin, while source Z remains metric.
    assert primitive["vertices"][0] == pytest.approx([-10.0, 0.0, 3.1], abs=0.001)
    assert primitive["vertices"][2] == pytest.approx([0.0, 10.0, 3.6], abs=0.001)
    assert primitive["source_file_units"] == "см"
    assert primitive["unit_scale_to_m"] == 0.01
    assert primitive["vertical_evidence"] == "explicit_xyz"
    assert primitive["terrain_mapping_status"] == "unmapped"
    assert scene["terrain_status"] == "missing"
    assert scene["terrain_evidence"]["status"] == "missing"
    assert scene["terrain_evidence"]["source"] is None


def test_scene_preserves_dxf_context_before_translating_to_local_coordinates() -> None:
    project_id = prepare_project("Смещённая сцена")
    select_areas(project_id, [area("work", "Рабочая область", [[12, 12], [72, 12], [72, 58], [12, 58]])])
    client.post(f"/api/projects/{project_id}/plan/manual")
    client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 60, "y": 40})

    project = get_application().get(project_id)
    assert project.geometry is not None and project.plan is not None
    x_offset, y_offset = 1_000, 2_000
    shifted_features = [
        {
            **feature,
            "geometry": mapping(translate(shape(feature["geometry"]), xoff=x_offset, yoff=y_offset)),
        }
        for feature in project.geometry.feature_collection["features"]
    ]
    project.geometry = project.geometry.model_copy(update={
        "feature_collection": {"type": "FeatureCollection", "features": shifted_features},
    })
    project.plan = project.plan.model_copy(update={
        "objects": [
            item.model_copy(update={"x": item.x + x_offset, "y": item.y + y_offset})
            for item in project.plan.objects
        ],
    })
    get_application().repository.save(project)

    response = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 0})
    assert response.status_code == 200, response.json()
    scene = response.json()
    assert scene["coordinate_origin"] == [1_040.0, 2_030.0]
    site_context = [shape(item["geometry"]) for item in scene["context_features"] if item["kind"] == "site_border"]
    assert site_context
    assert all(
        any(context.covers(Point(item["local_x"], item["local_y"])) for context in site_context)
        for item in scene["objects"]
    )


def test_scene_keeps_empty_work_areas_and_distant_source_context() -> None:
    project_id = prepare_project("Вся территория в 3D")
    select_areas(project_id, [
        area("work", "С посадками", [[12, 12], [72, 12], [72, 58], [12, 58]]),
        area("empty", "Без посадок", [[80, 10], [100, 10], [100, 40], [80, 40]]),
    ])
    client.post(f"/api/projects/{project_id}/plan/manual")
    client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    project = get_application().get(project_id)
    assert project.geometry is not None
    remote = {"type": "Feature", "id": "far-building", "geometry": {"type": "Polygon", "coordinates": [[[1000, 1000], [1020, 1000], [1020, 1020], [1000, 1000]]]}, "properties": {"kind": "building"}}
    project.geometry.feature_collection["features"].append(remote)
    get_application().repository.save(project)
    scene = client.get(f"/api/projects/{project_id}/plan/scene", params={"horizon_year": 0}).json()
    context = scene["context_features"]
    assert sum(item["kind"] == "planting_area" for item in context) == 2
    building = next(item for item in context if item["feature_id"] == "far-building")
    assert shape(building["geometry"]).bounds == (980, 980, 1000, 1000)
