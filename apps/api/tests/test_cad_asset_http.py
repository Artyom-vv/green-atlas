import pytest
from fastapi.testclient import TestClient

from app.composition import get_cad_assets
from app.main import app
from app.operations.contracts import OperationStatus
from tests.cad_asset_fixtures import fixture_asset


@pytest.fixture
def service(tmp_path, monkeypatch):
    fixture = fixture_asset(tmp_path)
    app.dependency_overrides[get_cad_assets] = lambda: fixture.application

    def unrelated_project_read(*args, **kwargs):
        pytest.fail("A CAD asset must not load Project, geometry or the source BLOB")

    import app.main as main_module

    monkeypatch.setattr(main_module, "get_application", unrelated_project_read)
    monkeypatch.setattr(
        fixture.runtime.project_repository, "get", unrelated_project_read
    )
    monkeypatch.setattr(
        fixture.runtime.project_repository, "get_source", unrelated_project_read
    )
    try:
        with TestClient(app) as client:
            yield fixture, client
    finally:
        app.dependency_overrides.pop(get_cad_assets)
        fixture.runtime.close()


def test_http_metadata_and_complete_file_without_project_blob(service):
    fixture, client = service
    response = client.get(fixture.url)
    assert response.status_code == 200, response.text
    metadata = response.json()
    assert metadata["source_units"] == 6 and metadata["unit_scale_to_m"] == 1
    assert metadata["calculation_ready"] is False
    file = client.get(metadata["file_url"])
    assert file.status_code == 200
    assert file.headers["etag"] == f'"{metadata["asset_sha256"]}"'
    assert file.headers["content-length"] == str(metadata["asset_bytes"])
    assert file.headers["content-type"] == "application/dxf"
    assert "X-Project-State-Version" not in file.headers
    assert file.content == fixture.asset.read_bytes()
    assert str(fixture.asset.parent) not in response.text


def test_http_lifecycle_missing_and_wrong_project_are_scoped(service):
    fixture, client = service
    unknown = fixture.url.replace(fixture.operation.id, "unknown")
    other = fixture.url.replace(fixture.operation.project_id, "another-project")
    assert client.get(unknown).status_code == 404
    assert client.get(other + "/file").status_code == 404
    fixture.operation.status = OperationStatus.CANCELLED
    fixture.runtime.operation_repository.save(fixture.operation)
    assert client.get(fixture.url).status_code == 409
    assert client.get(fixture.url + "/file").status_code == 409


def test_http_changed_asset_returns_no_bytes_or_private_path(service):
    fixture, client = service
    fixture.asset.write_bytes(b"changed asset")
    response = client.get(fixture.url + "/file")
    assert response.status_code == 409
    assert response.json()["code"] == "CAD_ASSET_UNAVAILABLE"
    assert str(fixture.asset) not in response.text
    assert b"changed asset" not in response.content
