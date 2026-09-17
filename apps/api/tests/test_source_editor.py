import json
import zipfile
from io import BytesIO, StringIO
from pathlib import Path

import ezdxf
from fastapi.testclient import TestClient

from app.main import app

SOURCE = Path(__file__).parents[3] / "fixtures/site.dxf"


def imported(client):
    project_id = client.post("/api/projects", json={"name": "Source editor"}).json()["id"]
    response = client.post(f"/api/projects/{project_id}/source-dxf", files={
        "file": ("site.dxf", SOURCE.read_bytes(), "application/dxf"),
    })
    assert response.status_code == 200, response.text
    return f"/api/projects/{project_id}"


def test_source_opens_and_edits_without_claiming_calculated_constraints(monkeypatch):
    client = TestClient(app)
    url = imported(client)
    opened = client.post(f"{url}/source-editor")
    assert opened.status_code == 200, opened.text
    assert opened.json()["map_ready"]
    assert opened.json()["source_review"]["status"] == "pending"
    assert opened.json()["allowed_area_m2"] is None
    assert client.get(f"{url}?include_geometry=false").json()["source_review"]
    passport = client.get(f"{url}/data-passport").json()
    assert passport["calculation_status"] == "not_ready"
    assert not any(item["used_in_calculation"] for item in passport["entries"])
    zone = {"id": "work", "label": "Рабочий участок", "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [40, 0], [40, 40], [0, 40], [0, 0]]],
    }}
    saved = client.put(f"{url}/planting-zones", json={"zones": [zone]})
    assert saved.status_code == 200, saved.text
    assert client.post(f"{url}/plan/manual").status_code == 200
    # Hover checks in a pending draft must not deserialize the CAD geometry.
    from app.composition import get_application
    repository = get_application().repository
    original_get = repository.get
    from app.cad_intake.prepare_contracts import PreparedSourceProvenance
    project = original_get(url.rsplit("/", 1)[-1])
    project.source_file.prepared_provenance = PreparedSourceProvenance(
        intake_operation_id="test-intake", manifest_sha256="a" * 64,
        profile_version=1, entry="site.dxf", source_sha256=project.source_file.content_sha256,
    )
    repository.save(project)
    reads = []
    def tracked_get(project_id, *, lightweight=False):
        reads.append(lightweight)
        return original_get(project_id, lightweight=lightweight)
    with monkeypatch.context() as patch:
        patch.setattr(repository, "get", tracked_get)
        checked = client.post(f"{url}/plan/placement-check", json={"kind": "tree", "x": 20, "y": 20})
        assert checked.status_code == 200, checked.text
        assert checked.json()["allowed"]
        assert checked.json()["status"] == "unknown"
        assert reads and all(reads)
        reads.clear()
        history = client.get(f"{url}/plan/history")
        assert history.status_code == 200, history.text
        assert reads and all(reads)
    reads.clear()
    with monkeypatch.context() as patch:
        patch.setattr(repository, "get", tracked_get)
        added = client.post(f"{url}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    assert reads.count(False) == 1
    assert added.status_code == 200, added.text
    assert added.json()["objects"][0]["status"] == "warning"
    assert any(item["code"] == "SOURCE_REVIEW_PENDING" for item in added.json()["issues"])
    outside = client.post(f"{url}/plan/objects", json={"kind": "tree", "x": 100, "y": 100})
    assert outside.status_code == 400
    assert client.get(f"{url}/source-dxf/download").content == SOURCE.read_bytes()
    # A retry does not reset the plan or create a new revision.
    before = client.get(url).json()
    retried = client.post(f"{url}/source-editor").json()
    assert retried["state_version"] == before["state_version"]
    assert retried["plan"] == before["plan"]
    final = client.post(f"{url}/releases", json={"mode": "final"})
    assert final.status_code == 400
    assert "расчёт ограничений" in final.text
    draft = client.post(f"{url}/releases", json={"mode": "draft"})
    assert draft.status_code == 200, draft.text
    bundle = next(a for a in draft.json()["artifacts"] if a["kind"] == "bundle")
    bundle_content = client.get(bundle["download_url"]).content
    with zipfile.ZipFile(BytesIO(bundle_content)) as archive:
        manifest_name = next(name for name in archive.namelist() if name.endswith("manifest.json"))
        manifest = json.loads(archive.read(manifest_name))
    assert not any(item["used_in_calculation"] for item in manifest["layer_mappings"])
    target = client.post("/api/projects", json={"name": "Restored source draft"}).json()["id"]
    restored = client.post(f"/api/projects/{target}/release-bundle", files={
        "file": ("draft.zip", bundle_content, "application/zip"),
    })
    assert restored.status_code == 200, restored.text
    assert restored.json()["source_review"] == before["source_review"]
    assert restored.json()["plan"]["objects"][0]["status"] == "warning"
    assert restored.json()["source_file"]["prepared_provenance"] == before["source_file"]["prepared_provenance"]
    exported = client.post(f"{url}/exports")
    assert exported.status_code == 200, exported.text
    assert exported.json()["filename"].endswith("_draft_plan.dxf")
    document = ezdxf.read(StringIO(client.get(exported.json()["download_url"]).content.decode("utf8")))
    planting = document.modelspace().query('CIRCLE[layer=="GREEN_ATLAS_TREES"]')[0]
    assert "source_review=pending" in [tag.value for tag in planting.get_xdata("GREEN_ATLAS")]


def test_calculation_promotes_draft_without_discarding_manual_work():
    client = TestClient(app)
    url = imported(client)
    client.post(f"{url}/source-editor")
    zone = {"id": "work", "label": "Рабочий участок", "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [40, 0], [40, 40], [0, 40], [0, 0]]],
    }}
    assert client.put(f"{url}/planting-zones", json={"zones": [zone]}).status_code == 200
    assert client.post(f"{url}/plan/manual").status_code == 200
    added = client.post(f"{url}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    object_id = added.json()["objects"][0]["id"]
    response = client.post(f"{url}/operations/geometry")
    assert response.status_code == 202, response.text
    operation = client.get(f"{url}/operations/{response.json()['id']}").json()
    assert operation["status"] == "completed", operation
    after = client.get(url).json()
    assert after["source_review"] is None
    assert after["map_ready"]
    assert after["plan"]["objects"][0]["id"] == object_id
    assert not any(issue["code"] == "SOURCE_REVIEW_PENDING" for issue in after["plan"]["issues"])


def test_manual_snapshot_does_not_overwrite_concurrent_project_change(monkeypatch):
    from app.composition import get_application

    client = TestClient(app)
    url = imported(client)
    client.post(f"{url}/source-editor")
    zone = {"id": "work", "label": "QA", "geometry": {
        "type": "Polygon", "coordinates": [[[0, 0], [40, 0], [40, 40], [0, 40], [0, 0]]],
    }}
    assert client.put(f"{url}/planting-zones", json={"zones": [zone]}).status_code == 200
    assert client.post(f"{url}/plan/manual").status_code == 200
    application = get_application()
    changes = application.changes
    original = changes.apply_change_set

    def concurrently_changed(project_id, request, **kwargs):
        winner = application.repository.get(project_id)
        winner.name = "Concurrent winner"
        application.repository.save(winner)
        return original(project_id, request, **kwargs)

    monkeypatch.setattr(changes, "apply_change_set", concurrently_changed)
    response = client.post(f"{url}/plan/objects", json={"kind": "tree", "x": 20, "y": 20})
    assert response.status_code in (400, 409, 412), response.text
    saved = client.get(url).json()
    assert saved["name"] == "Concurrent winner"
    assert saved["plan"]["objects"] == []
