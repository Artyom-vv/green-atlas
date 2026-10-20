"""HTTP refactoring must retain the original endpoint-only error boundary."""

import asyncio
import inspect
from collections.abc import Iterator
from threading import get_ident
from typing import Literal, NoReturn
from unittest.mock import Mock
from uuid import UUID

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError, ResponseValidationError
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel
from shapely.geometry import box, mapping

from app.api import router
from app.application import ProjectApplication
from app.composition import get_application
from app.dxf_import.native.asset_contracts import NativeDxfAssetUnavailable
from app.dxf_import.review.contracts import (
    SourceAreaGroupCheck,
    SourceAreaGroupRejected,
)
from app.geometry.contracts import GeometrySnapshot
from app.http_errors import BusinessErrorRoute
from app.main import http_error, validation_error
from app.planning.changes.contracts import ChangeSetCandidateResult, ChangeSetRejected
from app.planning.domain import PlanVersionConflict
from app.planting_zones.contracts import PlantingZoneAssignment
from app.projects.concurrency import ProjectVersionConflict
from app.projects.contracts import Project
from app.shared.failures import (
    DomainFailure,
    FailureReason,
    ProjectNotFound,
    ResourceNotFound,
)

ContractApi = tuple[TestClient, FastAPI, Mock]


@pytest.fixture
def contract_api() -> Iterator[ContractApi]:
    api = FastAPI()
    api.exception_handler(HTTPException)(http_error)
    api.exception_handler(RequestValidationError)(validation_error)
    api.include_router(router)
    application = Mock(spec=ProjectApplication)
    api.dependency_overrides[get_application] = lambda: application
    with TestClient(api) as client:
        yield client, api, application


@pytest.mark.parametrize(
    "error,status,code,details",
    [
        (
            DomainFailure(FailureReason.PLAN_MISSING),
            400,
            "BAD_REQUEST",
            {"reason": "PLAN_MISSING"},
        ),
        (ProjectNotFound("project"), 404, "NOT_FOUND", {}),
        (ResourceNotFound(FailureReason.RELEASE_MISSING), 404, "NOT_FOUND", {}),
        (
            NativeDxfAssetUnavailable(),
            409,
            "NATIVE_DXF_ASSET_UNAVAILABLE",
            {},
        ),
        (
            ProjectVersionConflict("project", 2, 3),
            409,
            "PROJECT_VERSION_CONFLICT",
            {"project_id": "project", "expected_version": 2, "current_version": 3},
        ),
        (
            PlanVersionConflict(2, 3),
            409,
            "PLAN_VERSION_CONFLICT",
            {"expected_version": 2, "current_version": 3},
        ),
        (
            HTTPException(status_code=418, detail="endpoint HTTP failure"),
            418,
            "HTTP_ERROR",
            {},
        ),
    ],
)
def test_endpoint_business_error_contract(
    contract_api: ContractApi,
    error: Exception,
    status: int,
    code: str,
    details: dict[str, str | int],
) -> None:
    client, _api, application = contract_api
    application.list_projects.side_effect = error
    response = client.get("/api/projects")
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["details"] == details
    assert response.json()["field_errors"] == {}
    assert response.json()["message"]
    if isinstance(error, DomainFailure) and error.reason == FailureReason.PLAN_MISSING:
        assert response.json()["message"] == "План ещё не создан"
    application.list_projects.assert_called_once_with()


@pytest.mark.parametrize(
    "route,zone_ids,reason,explanation",
    [
        (
            "targets",
            ["missing"],
            FailureReason.BUILDING_SCREEN_ZONES_INVALID,
            "Выберите существующие рабочие участки.",
        ),
        (
            "preview",
            ["west", "missing"],
            FailureReason.BUILDING_SCREEN_ZONES_INVALID,
            "Выберите существующие рабочие участки.",
        ),
        (
            "preview",
            ["west"],
            FailureReason.BUILDING_SCREEN_BUILDINGS_MISSING,
            "Рядом с выбранными участками не найдены контуры зданий. Выберите другие участки.",
        ),
    ],
)
def test_building_screen_expected_refusals_are_http_400_without_plan_changes(
    contract_api: ContractApi,
    route: Literal["targets", "preview"],
    zone_ids: list[str],
    reason: FailureReason,
    explanation: str,
) -> None:
    client, _api, application = contract_api
    project = Project(
        name="Building context",
        geometry=GeometrySnapshot(
            feature_collection={"type": "FeatureCollection", "features": []}
        ),
        planting_zones=[
            PlantingZoneAssignment(
                id="west", label="West", geometry=mapping(box(0, 0, 20, 20))
            )
        ],
    )
    application.get.return_value = project
    before = project.model_dump_json()
    if route == "targets":
        response = client.get(
            "/api/projects/project/building-screen/targets",
            params=[("zone_ids", zone_id) for zone_id in zone_ids],
        )
    else:
        response = client.post(
            "/api/projects/project/building-screen/preview",
            json={"base_plan_version": 1, "zone_ids": zone_ids},
        )
    assert response.status_code == 400
    assert response.json()["code"] == "BAD_REQUEST"
    assert response.json()["details"] == {"reason": reason}
    assert response.json()["message"] == explanation
    assert response.json()["field_errors"] == {}
    application.get.assert_called_once_with("project")
    application.preview_pattern.assert_not_called()
    assert project.model_dump_json() == before


@pytest.mark.parametrize(
    "error,status,code",
    [
        (DomainFailure(FailureReason.PLAN_MISSING), 400, "BAD_REQUEST"),
        (ProjectNotFound("project"), 404, "NOT_FOUND"),
    ],
)
def test_async_endpoint_business_error_contract(
    contract_api: ContractApi,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    status: int,
    code: str,
) -> None:
    async def fail_import(*args: object, **kwargs: object) -> NoReturn:
        raise error

    monkeypatch.setattr("app.api.execute_import", fail_import)
    response = contract_api[0].post(
        "/api/projects/project/source-autocad-live",
        files={"file": ("capture.json", b"{}")},
        data={"autocad_version": "2026", "target": "macos-arm64"},
    )
    assert response.status_code == status
    assert response.json()["code"] == code
    assert response.json()["message"] in {"План ещё не создан", "Проект не найден"}


@pytest.mark.parametrize(
    "error_type", [ValueError, RuntimeError, AttributeError, KeyError, OSError]
)
def test_unexpected_failure_is_ambiguous_and_keeps_diagnostics_local(
    contract_api: ContractApi,
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
) -> None:
    private_detail = "synthetic credential /private/source.dwg"
    client, _api, application = contract_api
    application.list_projects.side_effect = error_type(private_detail)
    response = client.get("/api/projects")
    assert response.status_code == 500
    payload = response.json()
    assert payload["code"] == "INTERNAL_ERROR"
    assert private_detail not in response.text
    reference = payload["details"]["diagnostic_id"]
    UUID(reference)
    assert reference in caplog.text
    assert private_detail in caplog.text


@pytest.mark.parametrize("missing", ["reason", "params"])
def test_malformed_refusal_is_an_internal_failure(
    contract_api: ContractApi, missing: str
) -> None:
    failure = DomainFailure(FailureReason.PLAN_MISSING)
    delattr(failure, missing)
    contract_api[2].list_projects.side_effect = failure
    response = contract_api[0].get("/api/projects")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    UUID(response.json()["details"]["diagnostic_id"])


def test_unused_domain_parameters_are_not_published(contract_api: ContractApi) -> None:
    private_detail = "synthetic unused provider token"
    contract_api[2].list_projects.side_effect = DomainFailure(
        FailureReason.RECOGNITION_OPENAI_HTTP_FAILURE,
        {"http_status": 503, "layer_names": [private_detail]},
    )
    response = contract_api[0].get("/api/projects")
    assert response.status_code == 400
    assert response.json()["details"] == {"reason": "RECOGNITION_OPENAI_HTTP_FAILURE"}
    assert "503" in response.json()["message"]
    assert private_detail not in response.text


@pytest.mark.parametrize("refusal_type", ["area", "placement"])
def test_missing_checked_refusal_payload_is_an_internal_failure(
    contract_api: ContractApi, refusal_type: str
) -> None:
    if refusal_type == "area":
        failure: Exception = SourceAreaGroupRejected(
            SourceAreaGroupCheck(valid=False, reason="Разрыв контура")
        )
        delattr(failure, "result")
    else:
        failure = ChangeSetRejected(
            ChangeSetCandidateResult(
                operation_index=0,
                type="add",
                status="blocked",
                code="OUTSIDE_SITE",
                category="constraint",
                reason="За границей участка",
            )
        )
        delattr(failure, "candidate")
    contract_api[2].list_projects.side_effect = failure
    response = contract_api[0].get("/api/projects")
    assert response.status_code == 500
    assert response.json()["code"] == "INTERNAL_ERROR"
    UUID(response.json()["details"]["diagnostic_id"])


def test_validation_keeps_field_and_constraint_without_english_or_input(
    contract_api: ContractApi,
) -> None:
    client, _api, application = contract_api
    response = client.post(
        "/api/projects/project/plan/patterns/preview",
        json={
            "type": "row",
            "base_plan_version": 1,
            "axis": {"type": "LineString", "coordinates": [[0, 0], [10, 0]]},
            "zone_ids": ["zone"],
            "plant_kind": "tree",
            "spacing_m": 0,
        },
    )
    assert response.status_code == 422
    assert response.json()["field_errors"]["row.spacing_m"] == [
        "Значение должно быть не меньше 0.5"
    ]
    application.preview_pattern.assert_not_called()


def test_dependency_failure_stays_outside_business_error_translation(
    contract_api: ContractApi,
) -> None:
    client, api, _application = contract_api

    def unavailable() -> NoReturn:
        raise ValueError("dependency failure")

    api.dependency_overrides[get_application] = unavailable
    with pytest.raises(ValueError, match="dependency failure"):
        client.get("/api/projects")

    def unauthorized() -> NoReturn:
        raise HTTPException(status_code=401, detail="dependency authorization failure")

    api.dependency_overrides[get_application] = unauthorized
    response = client.get("/api/projects")
    assert response.status_code == 401
    assert response.json()["code"] == "HTTP_ERROR"


def test_validation_and_version_dependency_precede_endpoint(
    contract_api: ContractApi,
) -> None:
    client, _api, application = contract_api
    response = client.post("/api/projects", json={"name": []})
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "name" in response.json()["field_errors"]
    application.create_project.assert_not_called()
    response = client.post(
        "/api/projects/project/plan/manual", headers={"If-Match": "invalid"}
    )
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_IF_MATCH"
    application.create_manual_plan.assert_not_called()


def test_response_validation_stays_outside_business_error_translation(
    contract_api: ContractApi,
) -> None:
    client, _api, application = contract_api
    application.list_projects.return_value = [{}]
    with pytest.raises(ResponseValidationError) as failure:
        client.get("/api/projects")
    assert failure.value.body == [{}]
    assert [item["loc"] for item in failure.value.errors()] == [
        ("response", 0, field)
        for field in ("id", "name", "status", "created_at", "updated_at")
    ]


@pytest.mark.parametrize(
    "route",
    [
        "source-dxf/download",
        "exports/artifact/download",
        "releases/release/artifacts/artifact",
    ],
)
def test_download_response_failure_stays_outside_read_error_boundary(
    contract_api: ContractApi, monkeypatch: pytest.MonkeyPatch, route: str
) -> None:
    client, _api, application = contract_api
    application.get.return_value = Project(name="example")
    application.download_export.return_value = b"dxf"
    application.download_release_artifact.return_value = (
        Mock(filename="example"),
        b"dxf",
    )
    application.native_sources = Mock()

    def response_failure(*args: object, **kwargs: object) -> NoReturn:
        raise RuntimeError("response construction failed")

    target = "source_download_response" if route == "source-dxf/download" else "quote"
    monkeypatch.setattr(f"app.api.{target}", response_failure)
    with pytest.raises(RuntimeError, match="response construction failed"):
        client.get(f"/api/projects/project/{route}")


def test_sync_endpoint_keeps_thread_pool_and_async_import_keeps_event_loop(
    contract_api: ContractApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, api, application = contract_api
    dependency_threads: list[int] = []
    endpoint_threads: list[int] = []

    async def dependency() -> Mock:
        dependency_threads.append(get_ident())
        return application

    def read_projects() -> list[Project]:
        endpoint_threads.append(get_ident())
        return []

    async def import_project(*args: object, **kwargs: object) -> Project:
        endpoint_threads.append(get_ident())
        return Project(name="capture")

    api.dependency_overrides[get_application] = dependency
    application.list_projects.side_effect = read_projects
    assert client.get("/api/projects").status_code == 200
    assert endpoint_threads[-1] != dependency_threads[-1]
    monkeypatch.setattr("app.api.execute_import", import_project)
    assert (
        client.post(
            "/api/projects/project/source-autocad-live",
            files={"file": ("capture.json", b"{}")},
            data={"autocad_version": "2026", "target": "macos-arm64"},
        ).status_code
        == 200
    )
    assert endpoint_threads[-1] == dependency_threads[-1]


def test_async_cancellation_is_not_a_business_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def cancelled(*args: object, **kwargs: object) -> NoReturn:
        raise asyncio.CancelledError("cancelled import")

    monkeypatch.setattr("app.api.execute_import", cancelled)
    route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.name == "upload_autocad_live"
    )
    assert inspect.iscoroutinefunction(route.endpoint)
    with pytest.raises(asyncio.CancelledError, match="cancelled import"):
        asyncio.run(
            route.endpoint(
                "project", Mock(filename="capture.json"), "2026", "macos-arm64", Mock()
            )
        )


class ForwardBody(BaseModel):
    value: int


def test_forward_annotations_and_openapi_use_original_endpoint_globals() -> None:
    def endpoint(body: "ForwardBody") -> "ForwardBody":
        return body

    schemas = []
    for route_class in (APIRoute, BusinessErrorRoute):
        api = FastAPI()
        probe = APIRouter(route_class=route_class)
        probe.post("/probe")(endpoint)
        api.include_router(probe)
        schemas.append(api.openapi())
        with TestClient(api) as client:
            assert client.post("/probe", json={"value": 7}).json() == {"value": 7}
            assert client.post("/probe", json={"value": []}).status_code == 422
    assert schemas[0] == schemas[1]


def test_repeated_router_inclusion_keeps_one_error_translation(
    contract_api: ContractApi,
) -> None:
    client, api, application = contract_api
    api.include_router(router, prefix="/nested")
    application.list_projects.side_effect = ProjectVersionConflict("project", 2, 3)
    response = client.get("/nested/api/projects")
    assert response.status_code == 409
    assert response.json()["code"] == "PROJECT_VERSION_CONFLICT"
