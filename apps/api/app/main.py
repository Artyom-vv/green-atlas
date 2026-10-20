"""Сборка HTTP-приложения: маршруты, локальные CORS-правила, заголовки ревизий и
публичные ошибки запросов.
"""

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.responses import Response

from app.api import router
from app.cad_intake.assets.routes import router as cad_asset_router
from app.composition import get_application
from app.contracts import ApiError
from app.location.routes import router as location_router
from app.location.settings_routes import router as location_settings_router
from app.projects.concurrency import ProjectVersionConflict
from app.projects.maintenance_worker import foreground_activity
from app.rendering.routes import router as scene_render_router
from app.shared.error_messages import message, validation_messages

app = FastAPI(
    title="Green Atlas API",
    version="0.1.0",
    description="Contract-first modular API for the deterministic greening prototype.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag", "X-Project-State-Version"],
)


@app.middleware("http")
async def project_version_headers(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    with foreground_activity():
        return await _project_version_response(request, call_next)


async def _project_version_response(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    response = await call_next(request)
    if response.status_code >= 400:
        return response
    # Неизменяемый CAD-файл использует ETag по байтам и область своей операции.
    # Чтение Project загрузило бы постороннее состояние на каждый запрос файла.
    route = request.scope.get("route")
    if getattr(route, "name", None) in {
        "cad_asset_metadata",
        "cad_asset_file",
        "native_dxf_asset",
        "source_dxf_download",
        # Render jobs own immutable inputs and do not advance project versions.
        "create_scene_render",
        "prepare_scene_render",
        "get_scene_render_preparation",
        "get_scene_render_preparation_asset",
        "get_scene_view",
        "get_scene_view_asset",
        "get_scene_render",
        "cancel_scene_render",
        "scene_render_image",
        "scene_render_receipt",
        "get_project_location",
        "suggest_project_location",
        "resolve_project_location",
        "align_project_location",
        "apply_project_location",
    }:
        return response
    parts = request.url.path.split("/")
    if len(parts) < 4 or parts[1:3] != ["api", "projects"]:
        return response
    project_id = parts[3]
    try:
        version = get_application().get(project_id, lightweight=True).state_version
    except (KeyError, ProjectVersionConflict):
        return response
    response.headers["ETag"] = f'"{version}"'
    response.headers["X-Project-State-Version"] = str(version)
    return response


@app.exception_handler(HTTPException)
async def http_error(_: Request, error: HTTPException) -> JSONResponse:
    payload = (
        error.detail
        if isinstance(error.detail, dict)
        else ApiError(code="HTTP_ERROR", message=str(error.detail)).model_dump()
    )
    return JSONResponse(
        status_code=error.status_code, content=payload, headers=error.headers
    )


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    payload = ApiError(
        code="VALIDATION_ERROR",
        message=message("validation_summary"),
        field_errors=validation_messages(error.errors()),
    ).model_dump()
    return JSONResponse(status_code=422, content=payload)


app.include_router(router)
app.include_router(cad_asset_router)
app.include_router(scene_render_router)
app.include_router(location_router)
app.include_router(location_settings_router)
