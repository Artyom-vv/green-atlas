from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.cad_import.cache import file_sha256
from app.cad_intake.application import CadIntakeApplication
from app.cad_intake.config import AllowedCadRoot, CadIntakeConfig
from app.cad_intake.contracts import CadPackagePassport
from app.composition import create_runtime, get_application, get_cad_intake
from app.main import app
from app.projects.contracts import Project


class PackageStub:
    def inspect(self, operation_id, request, check_cancelled, report_progress):
        check_cancelled()
        return CadPackagePassport(
            root_id=request.root_id,
            entry=request.entry,
            manifest_sha256="a" * 64,
            drawings=[],
            references=[],
            status="requires_review",
            blockers=[],
        )


@pytest.fixture
def service(tmp_path: Path):
    original = tmp_path / "originals"
    original.mkdir()
    source = original / "entry.dwg"
    source.write_bytes(b"AC1032fixture")
    runtime = create_runtime(tmp_path / "service.sqlite3")
    project = runtime.project_repository.create(Project(name="CAD HTTP"))
    config = CadIntakeConfig(
        (AllowedCadRoot("official", "Комплект", original),),
        tmp_path / "data",
        Path("converter"),
    )
    intake = CadIntakeApplication(
        config, runtime.application.operations.lifecycle, PackageStub()
    )
    app.dependency_overrides[get_application] = lambda: runtime.application
    app.dependency_overrides[get_cad_intake] = lambda: intake
    # Main's response header middleware deliberately calls its composition dependency directly.
    import app.main as main_module

    original_get = main_module.get_application
    main_module.get_application = lambda: runtime.application
    try:
        with TestClient(app) as client:
            yield client, runtime, project, source
    finally:
        main_module.get_application = original_get
        app.dependency_overrides.pop(get_application)
        app.dependency_overrides.pop(get_cad_intake)
        runtime.close()


def test_http_discovery_start_status_and_project_unchanged(service):
    client, runtime, project, source = service
    roots = client.get("/api/cad/roots")
    assert roots.json() == [{"id": "official", "label": "Комплект"}]
    entries = client.get("/api/cad/roots/official/entries").json()
    assert entries["entries"] == [
        {
            "path": "entry.dwg",
            "name": "entry.dwg",
            "kind": "drawing",
            "bytes": source.stat().st_size,
        }
    ]
    fingerprint = client.get(
        "/api/cad/roots/official/fingerprint", params={"path": "entry.dwg"}
    ).json()
    assert fingerprint["sha256"] == file_sha256(source)
    payload = {
        "root_id": "official",
        "entry": "entry.dwg",
        "entry_sha256": fingerprint["sha256"],
    }
    endpoint = f"/api/projects/{project.id}/operations/cad-intake"
    assert client.post(endpoint, json=payload).status_code == 428
    assert (
        client.post(endpoint, json=payload, headers={"If-Match": '"9"'}).status_code
        == 409
    )
    started = client.post(endpoint, json=payload, headers={"If-Match": '"1"'})
    assert started.status_code == 202
    operation = started.json()
    assert operation["kind"] == "inspect_cad_package"
    status = client.get(
        f"/api/projects/{project.id}/operations/{operation['id']}"
    ).json()
    assert status["status"] == "completed"
    assert status["cad_intake"]["passport"]["calculation_ready"] is False
    latest = client.get(
        f"/api/projects/{project.id}/operations/latest",
        params={"kind": "inspect_cad_package"},
    )
    assert latest.json()["id"] == operation["id"]
    assert runtime.project_repository.get(project.id).state_version == 1
    assert runtime.project_repository.get_source(project.id) is None
    assert str(source.parent) not in started.text
    assert str(source.parent) not in str(status)


@pytest.mark.parametrize(
    "path",
    [
        "../entry.dwg",
        "C:/private/entry.dwg",
        "/etc/passwd",
        "\\\\host\\share\\entry.dwg",
    ],
)
def test_http_paths_are_not_arbitrary_file_reads(service, path):
    client, _, project, _ = service
    assert (
        client.get(
            "/api/cad/roots/official/fingerprint", params={"path": path}
        ).status_code
        == 400
    )
    result = client.post(
        f"/api/projects/{project.id}/operations/cad-intake",
        headers={"If-Match": '"1"'},
        json={"root_id": "official", "entry": path, "entry_sha256": "a" * 64},
    )
    assert result.status_code == 422


def test_disabled_configuration_does_not_expose_server_paths(tmp_path, monkeypatch):
    monkeypatch.delenv("GREEN_ATLAS_CAD_ROOTS_JSON", raising=False)
    monkeypatch.delenv("GREEN_ATLAS_DWG_CONVERTER", raising=False)
    config = CadIntakeConfig.from_environment(
        str(tmp_path / "data" / "service.sqlite3")
    )
    assert config.roots == ()
    assert config.storage == tmp_path / "data" / "cad-intake"
    with pytest.raises(ValueError, match="не настроен"):
        config.require_enabled()
