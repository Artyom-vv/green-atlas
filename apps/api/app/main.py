from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent_conversations import router as agent_conversations_router
from app.agent_runtime.preview_routes import router as agent_preview_router
from app.agent_runtime.routes import router as agent_runtime_router
from app.api import router
from app.cad_delivery.routes import router as cad_delivery_router
from app.cad_intake.asset_routes import router as cad_asset_router
from app.cad_intake.routes import router as cad_intake_router
from app.composition import get_application
from app.contracts import ApiError
from app.planning_assistant import router as planning_assistant_router
from app.project_assistant import router as project_assistant_router
from app.projects.concurrency import ProjectVersionConflict

app = FastAPI(
    title="Green Atlas API",
    version="0.1.0",
    description="Contract-first modular API for the deterministic greening prototype.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:5174", "http://127.0.0.1:5174"],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["ETag", "X-Project-State-Version"],
)


@app.middleware("http")
async def project_version_headers(request: Request, call_next):
    response = await call_next(request)
    if response.status_code >= 400:
        return response
    # Immutable CAD assets use their own byte hash ETag and operation scope.
    # Reading a Project here would load unrelated state for every file request.
    route = request.scope.get("route")
    if getattr(route, "name", None) in {"cad_asset_metadata", "cad_asset_file", "native_dxf_asset", "source_dxf_download"}:
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
    payload = error.detail if isinstance(error.detail, dict) else ApiError(code="HTTP_ERROR", message=str(error.detail)).model_dump()
    return JSONResponse(status_code=error.status_code, content=payload, headers=error.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    field_errors: dict[str, list[str]] = {}
    for item in error.errors():
        field = ".".join(str(part) for part in item["loc"] if part not in {"body", "query", "path"}) or "request"
        field_errors.setdefault(field, []).append(str(item["msg"]))
    payload = ApiError(code="VALIDATION_ERROR", message="Проверьте заполнение полей", field_errors=field_errors).model_dump()
    return JSONResponse(status_code=422, content=payload)


app.include_router(router)
app.include_router(cad_delivery_router)
app.include_router(cad_intake_router)
app.include_router(cad_asset_router)
app.include_router(planning_assistant_router)
app.include_router(project_assistant_router)
app.include_router(agent_conversations_router)
app.include_router(agent_runtime_router)
app.include_router(agent_preview_router)
