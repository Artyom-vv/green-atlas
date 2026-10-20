"""Download only verified saved artifacts, without rebuilding their source project."""

import json
from collections.abc import Iterator
from hashlib import sha256
from unittest.mock import Mock

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api import router
from app.composition import get_application
from app.exporting.application import ExportApplication
from app.exporting.contracts import ReleaseArtifact, ReleasePackage
from app.main import http_error
from app.planning.contracts import Plan
from app.projects.adapters import SqliteProjectRepository
from app.projects.contracts import Project

CONTENT = b"AC1032\x00saved immutable CAD artifact"
DownloadStore = tuple[TestClient, SqliteProjectRepository, Project, ReleasePackage]


@pytest.fixture
def download_store(monkeypatch: pytest.MonkeyPatch) -> Iterator[DownloadStore]:
    repository = SqliteProjectRepository(":memory:")
    project = repository.create(Project(name="Saved release", plan=Plan()))
    artifact = ReleaseArtifact(
        id="cad-artifact",
        filename="Озеленение.dwg",
        kind="cad",
        media_type="application/acad",
        size=len(CONTENT),
        sha256=sha256(CONTENT).hexdigest(),
        download_url=f"/api/projects/{project.id}/releases/release/artifacts/cad-artifact",
    )
    package = ReleasePackage(
        id="release",
        project_id=project.id,
        plan_version=1,
        geometry_version=0,
        mode="draft",
        status="draft",
        created_at="2026-10-19T00:00:00Z",
        rule_set_revision="test-rules",
        species_catalog_revision="test-catalog",
        scene_horizon=0,
        artifacts=[artifact],
    )
    repository.publish_release(
        project, package.id, package.model_dump_json(), {artifact.id: CONTENT}
    )
    application = ExportApplication(
        repository=repository,
        writer=Mock(create=Mock(side_effect=AssertionError("No DXF regeneration"))),
        cad_writer=Mock(create=Mock(side_effect=AssertionError("No CAD regeneration"))),
        scene=Mock(side_effect=AssertionError("No scene regeneration")),
    )
    original_get = repository.get

    def get(project_id: str, *, lightweight: bool = False) -> Project:
        assert lightweight, "Downloading a saved artifact must not load the full map"
        return original_get(project_id, lightweight=lightweight)

    monkeypatch.setattr(repository, "get", get)
    api = FastAPI()
    api.exception_handler(HTTPException)(http_error)
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: application
    with TestClient(api) as client:
        yield client, repository, project, package
    repository.close()


def test_download_returns_exact_saved_bytes_after_later_plan_changes(
    download_store: DownloadStore,
) -> None:
    client, repository, project, package = download_store
    project.name = "Later project name"
    assert project.plan is not None
    project.plan.version = 2
    repository.save(project)
    before = repository.get_release(project.id, package.id)

    response = client.get(package.artifacts[0].download_url)

    assert response.status_code == 200
    assert response.content == CONTENT
    assert response.headers["content-type"] == "application/acad"
    assert response.headers["content-length"] == str(len(CONTENT))
    assert response.headers["content-disposition"].startswith("attachment;")
    assert repository.get_release(project.id, package.id) == before
    assert repository.get_export(project.id, package.artifacts[0].id) == CONTENT


@pytest.mark.parametrize(
    "content",
    [CONTENT[:-1], CONTENT + b"extra", b"B" * len(CONTENT), b""],
    ids=["truncated", "appended", "same-size-substitution", "empty"],
)
def test_corrupt_stored_bytes_are_refused_without_a_download_or_repair(
    download_store: DownloadStore, content: bytes
) -> None:
    client, repository, project, package = download_store
    artifact = package.artifacts[0]
    repository._connection.execute(
        "UPDATE project_exports SET content=? WHERE project_id=? AND artifact_id=?",
        (content, project.id, artifact.id),
    )
    before = repository.get_release(project.id, package.id)

    response = client.get(artifact.download_url)

    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"
    assert response.json()["details"] == {"reason": "RELEASE_ARTIFACT_CORRUPT"}
    assert "content-disposition" not in response.headers
    assert response.headers["content-type"] == "application/json"
    assert repository.get_release(project.id, package.id) == before
    assert repository.get_export(project.id, artifact.id) == content


@pytest.mark.parametrize(
    "field,value",
    [("size", len(CONTENT) + 1), ("sha256", "0" * 64), ("sha256", "")],
)
def test_mismatched_integrity_metadata_is_refused(
    download_store: DownloadStore, field: str, value: str | int
) -> None:
    client, repository, project, package = download_store
    payload = json.loads(package.model_dump_json())
    payload["artifacts"][0][field] = value
    repository._connection.execute(
        "UPDATE project_releases SET payload=? WHERE project_id=? AND release_id=?",
        (json.dumps(payload), project.id, package.id),
    )

    response = client.get(package.artifacts[0].download_url)

    assert response.status_code == 400
    assert response.json()["details"] == {"reason": "RELEASE_ARTIFACT_CORRUPT"}
    assert "content-disposition" not in response.headers


@pytest.mark.parametrize("field", ["size", "sha256"])
def test_missing_required_metadata_never_returns_unverified_bytes(
    download_store: DownloadStore, field: str
) -> None:
    client, repository, project, package = download_store
    payload = json.loads(package.model_dump_json())
    del payload["artifacts"][0][field]
    repository._connection.execute(
        "UPDATE project_releases SET payload=? WHERE project_id=? AND release_id=?",
        (json.dumps(payload), project.id, package.id),
    )

    response = client.get(package.artifacts[0].download_url)

    assert response.status_code >= 400
    assert "content-disposition" not in response.headers
    assert response.headers["content-type"] == "application/json"


@pytest.mark.parametrize("missing", ["release", "artifact", "blob"])
def test_missing_release_artifact_or_blob_retains_not_found_semantics(
    download_store: DownloadStore, missing: str
) -> None:
    client, repository, project, package = download_store
    artifact = package.artifacts[0]
    url = artifact.download_url
    if missing == "release":
        url = url.replace("/releases/release/", "/releases/missing/")
    elif missing == "artifact":
        url = url.removesuffix(artifact.id) + "missing"
    else:
        repository._connection.execute(
            "DELETE FROM project_exports WHERE project_id=? AND artifact_id=?",
            (project.id, artifact.id),
        )

    response = client.get(url)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert "content-disposition" not in response.headers
