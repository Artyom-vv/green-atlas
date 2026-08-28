from __future__ import annotations

import asyncio
from io import BytesIO, StringIO
from pathlib import Path

import ezdxf
import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Point, mapping, shape
from shapely.ops import unary_union
from starlette.datastructures import UploadFile

from app import api as api_module
from app import application as application_module
from app.application import ProjectApplication
from app.contracts import LayerMapping, PlanObjectCreate, PlantingZoneAssignment
from app.dxf_import.adapters import EzdxfReader
from app.exporting.adapters import DxfRoundTripWriter
from app.geometry.adapters import ShapelyGeometryEngine
from app.geometry.query_adapters import IndexedGeometryQuery
from app.history.adapters import InMemoryProjectHistory
from app.main import app
from app.operations.adapters import SqliteOperationRepository
from app.planning.patterns import ShapelyCandidateGenerator
from app.projects.adapters import SqliteProjectRepository
from app.validation.adapters import RuleBasedPlanValidator


client = TestClient(app)
SITE_DXF = Path(__file__).parents[3] / "fixtures" / "site.dxf"
LARGE_DXF = Path(__file__).parents[3] / "fixtures" / "large-map" / "vdnkh-large.dxf"


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
    monkeypatch.setattr(api_module, "MAX_DXF_CONTENT_BYTES", 4)
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

    imported = api_module.application.import_dxf(project_id, "site.dxf", payload)

    assert imported.source_file is not None
    assert api_module.application.repository.get_source(project_id) == bytes(payload)


def test_opening_manual_plan_releases_raw_geometry_but_keeps_map_and_source() -> None:
    source = site_content()
    project_id = prepare_project("Освобождение исходной геометрии")
    before = api_module.application.get(project_id)
    assert before.source_geometry is not None
    assert before.geometry is not None
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])

    opened = client.post(f"/api/projects/{project_id}/plan/manual")

    assert opened.status_code == 200
    after = api_module.application.get(project_id)
    assert after.source_geometry is None
    assert after.geometry is not None
    viewport = client.get(
        f"/api/projects/{project_id}/map-features",
        params={"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 80, "resolution": 1},
    )
    assert viewport.status_code == 200
    assert viewport.json()["feature_collection"]["features"]
    assert client.get(f"/api/projects/{project_id}/source-dxf/download").content == source


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

    recovered = client.get(f"/api/projects/{project_id}/source-dxf/download")
    assert recovered.status_code == 200
    assert recovered.content == source


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
    monkeypatch.setattr(application_module, "MAX_DXF_CONTENT_BYTES", 4)
    project_id = client.post("/api/projects", json={"name": "Размер без HTTP"}).json()["id"]

    with pytest.raises(ValueError, match="DXF должен быть не больше"):
        api_module.application.import_dxf(project_id, "too-large.dxf", b"12345")

    assert api_module.application.get(project_id).source_file is None


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
    source_before = api_module.application.repository.get_source(project_id)

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
    assert api_module.application.repository.get_source(project_id) == source_before

    artifact = client.post(f"/api/projects/{project_id}/exports")
    assert artifact.status_code == 200
    assert client.get(artifact.json()["download_url"]).status_code == 200


def test_valid_reimport_cannot_silently_replace_a_manual_project_source() -> None:
    project_id = prepare_project("Не заменить источник под планом")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200
    source_before = api_module.application.repository.get_source(project_id)

    replacement = client.post(
        f"/api/projects/{project_id}/source-dxf",
        files={"file": ("replacement.dxf", site_content(), "application/dxf")},
    )

    assert replacement.status_code == 400
    assert "после открытия ручной схемы" in replacement.json()["message"]
    restored = client.get(f"/api/projects/{project_id}").json()
    assert restored["source_file"]["name"] == "site.dxf"
    assert len(restored["plan"]["objects"]) == 1
    assert api_module.application.repository.get_source(project_id) == source_before


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
    assert "после открытия ручной схемы" in replacement.json()["message"]
    project = client.get(f"/api/projects/{project_id}").json()
    assert len(project["plan"]["objects"]) == 1


def test_manual_plan_freezes_the_layer_meaning_and_map_geometry() -> None:
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

    for response in (remap, background_recalculation):
        assert response.status_code == 400
        assert "после открытия ручной схемы" in response.json()["message"]


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
    assert project_id in api_module.application.geometry_query._indexes
    api_module.application.validator._position_checker(api_module.application.get(project_id))
    assert project_id in api_module.application.validator._position_checkers

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
    assert project_id not in api_module.application.geometry_query._indexes
    assert project_id not in api_module.application.validator._position_checkers
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
        api_module.application.query_geometry(project_id, (float("nan"), 0, 120, 80), 1)


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


def test_failed_export_storage_does_not_publish_a_new_project_revision(monkeypatch: pytest.MonkeyPatch) -> None:
    project_id = prepare_project("Не публиковать несуществующий файл")
    select_areas(project_id, [area("work", "Участок посадки", [[12, 12], [60, 12], [60, 35], [12, 35]])])
    assert client.post(f"/api/projects/{project_id}/plan/manual").status_code == 200
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 20, "y": 20}).status_code == 200

    def fail_storage(*_args: object, **_kwargs: object) -> None:
        raise OSError("Хранилище экспорта недоступно")

    monkeypatch.setattr(api_module.application.repository, "publish_export", fail_storage)
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

    assert api_module.application.repository.get_source(project_id) == source
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


def test_row_pattern_creates_many_sites_as_one_undoable_revision() -> None:
    project_id = prepare_project("Ряд посадок")
    select_areas(project_id, [area("row-zone", "Линейный участок", [[12, 12], [65, 12], [65, 30], [12, 30]])])
    opened = client.post(f"/api/projects/{project_id}/plan/manual").json()
    base_version = opened["plan"]["version"]

    pattern = client.post(f"/api/projects/{project_id}/plan/patterns/preview", json={
        "type": "row",
        "base_plan_version": base_version,
        "plant_kind": "tree",
        "axis": {"type": "LineString", "coordinates": [[18, 20], [60, 20]]},
        "spacing_m": 6,
    })

    assert pattern.status_code == 200, pattern.json()
    preview = pattern.json()["change_set"]
    assert pattern.json()["requested_count"] == 8
    assert pattern.json()["accepted_count"] == 8
    assert preview["can_apply"] is True
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["objects"] == []

    applied = client.post(f"/api/projects/{project_id}/plan/change-sets/apply", json={
        "preview_id": preview["id"],
        "digest": preview["digest"],
        "base_plan_version": preview["base_plan_version"],
    })
    assert applied.status_code == 200, applied.json()
    assert applied.json()["plan_version"] == base_version + 1
    assert len(applied.json()["added_ids"]) == 8
    assert client.post(f"/api/projects/{project_id}/plan/history/undo").json()["plan"]["objects"] == []


def test_fill_pattern_is_deterministic_across_multiple_zones_and_reports_skips() -> None:
    project_id = prepare_project("Заполнение участков")
    zones = [
        area("west", "Запад", [[12, 12], [36, 12], [36, 36], [12, 36]]),
        area("east", "Восток", [[42, 12], [66, 12], [66, 36], [42, 36]]),
    ]
    select_areas(project_id, zones)
    base_version = client.post(f"/api/projects/{project_id}/plan/manual").json()["plan"]["version"]
    assert client.post(f"/api/projects/{project_id}/plan/objects", json={"kind": "tree", "x": 24, "y": 24}).status_code == 200
    current_version = client.get(f"/api/projects/{project_id}").json()["plan"]["version"]
    request = {
        "type": "fill",
        "base_plan_version": current_version,
        "plant_kind": "tree",
        "zone_ids": ["west", "east"],
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
    assert first.json()["requested_count"] >= first.json()["accepted_count"] > 1
    assert first.json()["change_set"]["can_apply"] is True
    assert all(any(shape(zone["geometry"]).covers(Point(x, y)) for zone in zones) for x, y in first_coordinates)
    assert client.get(f"/api/projects/{project_id}").json()["plan"]["version"] == current_version
