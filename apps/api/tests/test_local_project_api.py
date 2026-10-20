"""Public recovery reads do not write or infer that a missing receipt is safe."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from application_factory import recompose_application
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_change_receipts import deletion, durable_application
from test_utility_context import mapped_project

from app.api import router
from app.application import ProjectApplication
from app.composition import get_application
from app.geometry.checking.adapters import ShapelyGeometryEngine
from app.geometry.viewport.query_adapters import IndexedGeometryQuery
from app.projects.contracts import Project
from app.validation.adapters import RuleBasedPlanValidator

LocalApi = tuple[TestClient, ProjectApplication, Project]


@pytest.fixture
def local_api(tmp_path: Path) -> Iterator[LocalApi]:
    application, project, repository, _history = durable_application(
        tmp_path / "workspace.sqlite3"
    )
    api = FastAPI()
    api.include_router(router)
    api.dependency_overrides[get_application] = lambda: application
    with TestClient(api) as client:
        yield client, application, project
    repository.close()


def test_saved_plan_routes_to_workspace_with_no_geometry_in_read(
    local_api: LocalApi,
) -> None:
    client, application, project = local_api
    summaries = client.get("/api/projects").json()
    assert summaries[0]["resume_kind"] == "workspace"
    assert summaries[0]["prepared_state"] == "not_required"
    response = client.get(f"/api/projects/{project.id}")
    assert response.status_code == 200
    saved_plan = application.get(project.id).plan
    assert saved_plan is not None
    assert response.json()["plan"] == saved_plan.model_dump(mode="json")
    assert response.json()["geometry"] is None
    assert response.json()["resume_kind"] == "workspace"


def test_partial_reviewer_put_preserves_required_saved_layers(
    local_api: LocalApi,
) -> None:
    client, application, _project = local_api
    geometry = ShapelyGeometryEngine()
    application = recompose_application(
        application,
        geometry_query=IndexedGeometryQuery(),
        validator=RuleBasedPlanValidator(geometry=geometry),
    )
    assert isinstance(client.app, FastAPI)
    client.app.dependency_overrides[get_application] = lambda: application
    saved = mapped_project()
    saved.layers[0].required = True
    saved = application.repository.create(saved)
    assert saved.geometry is not None
    assert saved.layers[1].utility_context is not None
    original_geometry = saved.geometry.model_dump_json()
    context = saved.layers[1].utility_context.model_copy(
        update={"confirmed_by": "Changed reviewer"}
    )

    response = client.put(
        f"/api/projects/{saved.id}/layer-mappings",
        json={
            "mappings": [
                {
                    "layer_id": saved.layers[1].id,
                    "kind": "utility",
                    "utility_context": context.model_dump(mode="json"),
                }
            ]
        },
    )

    assert response.status_code == 200, response.text
    current = application.get(saved.id)
    assert current.geometry is not None
    assert current.layers[1].utility_context is not None
    assert current.layers[0] == saved.layers[0]
    assert current.layers[1].utility_context.confirmed_by == "Changed reviewer"
    assert current.map_ready
    assert current.geometry_version == saved.geometry_version
    assert current.geometry.model_dump_json() == original_geometry
    assert (
        client.get(f"/api/projects/{saved.id}").json()["prepared_state"]
        == "not_required"
    )


def test_receipt_can_confirm_lost_post_response_without_repeating_write(
    local_api: LocalApi,
) -> None:
    client, application, project = local_api
    request = deletion(application, project)
    route = f"/api/projects/{project.id}/plan/change-sets/{request.preview_id}/receipt"
    params = {
        "digest": request.digest,
        "base_plan_version": request.base_plan_version,
        "base_state_version": project.state_version,
    }
    absent = client.get(route, params=params)
    assert absent.status_code == 200
    assert absent.json() == {"status": "not_found", "receipt": None}
    committed = application.apply_change_set(project.id, request)
    before = application.get(project.id).model_dump_json()
    confirmed = client.get(route, params=params)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "applied"
    assert confirmed.json()["receipt"]["state_version"] == committed.state_version
    assert confirmed.json()["receipt"]["deleted_ids"] == ["one"]
    assert application.get(project.id).model_dump_json() == before
    application.undo_plan_change(project.id)
    assert client.get(route, params=params).json()["status"] == "undone"


@pytest.mark.parametrize("field", ["digest", "base_plan_version", "base_state_version"])
def test_receipt_rejects_mismatched_confirmation_and_does_not_mutate(
    local_api: LocalApi, field: str
) -> None:
    client, application, project = local_api
    request = deletion(application, project)
    base_state = project.state_version
    application.apply_change_set(project.id, request)
    params: dict[str, str | int] = {
        "digest": request.digest,
        "base_plan_version": request.base_plan_version,
        "base_state_version": base_state,
    }
    params[field] = "f" * 64 if field == "digest" else int(params[field]) + 1
    before = application.get(project.id).model_dump_json()
    response = client.get(
        f"/api/projects/{project.id}/plan/change-sets/{request.preview_id}/receipt",
        params=params,
    )
    assert response.status_code == 400
    assert application.get(project.id).model_dump_json() == before


def test_receipt_does_not_leak_confirmation_into_another_project(
    local_api: LocalApi,
) -> None:
    client, application, project = local_api
    request = deletion(application, project)
    application.apply_change_set(project.id, request)
    other = application.create_project("Other project")
    response = client.get(
        f"/api/projects/{other.id}/plan/change-sets/{request.preview_id}/receipt",
        params={
            "digest": request.digest,
            "base_plan_version": request.base_plan_version,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"status": "not_found", "receipt": None}
