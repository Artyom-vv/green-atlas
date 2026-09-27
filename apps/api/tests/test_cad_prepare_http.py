from cad_preview_fixtures import fixture_preview
from fastapi.testclient import TestClient
from test_cad_prepare import write_snapshot

from app.cad_intake.prepare_adapter import ProcessCadProjectPreparation
from app.cad_intake.prepare_application import CadPrepareApplication
from app.composition import get_application, get_cad_prepare
from app.main import app


def test_full_preparation_http_requires_version_and_publishes_editable_source(
    tmp_path, monkeypatch
):
    fixture = fixture_preview(tmp_path)
    write_snapshot(fixture)
    application = CadPrepareApplication(
        fixture.config,
        fixture.lifecycle,
        ProcessCadProjectPreparation(fixture.config, fixture.runtime.database_path),
        lambda _: None,
    )
    app.dependency_overrides[get_application] = lambda: fixture.runtime.application
    app.dependency_overrides[get_cad_prepare] = lambda: application
    monkeypatch.setattr("app.main.get_application", lambda: fixture.runtime.application)
    endpoint = f"/api/projects/{fixture.project.id}/cad-prepare"
    payload = {
        "intake_operation_id": fixture.request.intake_operation_id,
        "manifest_sha256": fixture.request.manifest_sha256,
        "profile_version": 1,
    }
    try:
        with TestClient(app) as client:
            assert client.post(endpoint, json=payload).status_code == 428
            assert (
                client.post(
                    endpoint, json=payload, headers={"If-Match": '"9"'}
                ).status_code
                == 409
            )
            response = client.post(endpoint, json=payload, headers={"If-Match": '"1"'})
            assert response.status_code == 202, response.text
            operation = client.get(
                f"/api/projects/{fixture.project.id}/operations/{response.json()['id']}"
            ).json()
            assert operation["status"] == "completed", operation
            project = client.get(
                f"/api/projects/{fixture.project.id}?include_geometry=false"
            ).json()
            assert (
                project["map_ready"] and project["source_review"]["status"] == "pending"
            )
            assert project["import_status"]["editability"] == "editable"
            assert (
                fixture.runtime.project_repository.get_source(fixture.project.id)
                == fixture.source.read_bytes()
            )
    finally:
        app.dependency_overrides.pop(get_application)
        app.dependency_overrides.pop(get_cad_prepare)
        fixture.runtime.close()
