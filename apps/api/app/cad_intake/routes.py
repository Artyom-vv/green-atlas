from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request

from app.api import project_version_scope
from app.cad_intake.application import CadIntakeApplication
from app.cad_intake.contracts import (
    CadDirectory,
    CadFingerprint,
    CadIntakeRequest,
    CadRoot,
)
from app.cad_intake.preview_application import CadPreviewApplication
from app.cad_intake.preview_contracts import CadPreviewRequest
from app.composition import get_cad_intake, get_cad_preview
from app.http_errors import handle
from app.operations.contracts import OperationStatus, ProjectOperation

router = APIRouter(
    prefix="/api", dependencies=[Depends(project_version_scope)], tags=["CAD intake"]
)


@router.get("/cad/roots", response_model=list[CadRoot])
def roots(application: CadIntakeApplication = Depends(get_cad_intake)) -> list[CadRoot]:
    return application.discovery.roots()


@router.get("/cad/roots/{root_id}/entries", response_model=CadDirectory)
def entries(
    root_id: str,
    path: str = Query(default=".", max_length=2048),
    application: CadIntakeApplication = Depends(get_cad_intake),
) -> CadDirectory:
    try:
        return application.discovery.directory(root_id, path)
    except (ValueError, OSError) as error:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CAD_PATH_UNAVAILABLE",
                "message": "Каталог недоступен внутри разрешённого CAD-комплекта",
            },
        ) from error


@router.get("/cad/roots/{root_id}/fingerprint", response_model=CadFingerprint)
def fingerprint(
    root_id: str,
    path: str = Query(min_length=1, max_length=2048),
    application: CadIntakeApplication = Depends(get_cad_intake),
) -> CadFingerprint:
    try:
        return application.discovery.fingerprint(root_id, path)
    except (ValueError, OSError) as error:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "CAD_FILE_UNAVAILABLE",
                "message": "Чертёж недоступен, изменился или превышает бюджет чтения",
            },
        ) from error


@router.post(
    "/projects/{project_id}/operations/cad-intake",
    response_model=ProjectOperation,
    status_code=202,
)
def start(
    project_id: str,
    body: CadIntakeRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    application: CadIntakeApplication = Depends(get_cad_intake),
) -> ProjectOperation:
    if not request.headers.get("If-Match"):
        raise HTTPException(
            status_code=428,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "Обновите проект перед проверкой CAD-комплекта",
            },
        )
    try:
        operation = application.start(project_id, body)
        if operation.status == OperationStatus.QUEUED:
            background_tasks.add_task(application.run, operation.id)
        return operation
    except Exception as error:
        raise handle(error) from error


@router.post(
    "/projects/{project_id}/operations/cad-preview",
    response_model=ProjectOperation,
    status_code=202,
)
def start_preview(
    project_id: str,
    body: CadPreviewRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    application: CadPreviewApplication = Depends(get_cad_preview),
) -> ProjectOperation:
    if not request.headers.get("If-Match"):
        raise HTTPException(
            status_code=428,
            detail={
                "code": "PROJECT_VERSION_REQUIRED",
                "message": "Обновите проект перед подготовкой территории CAD",
            },
        )
    try:
        operation = application.start(project_id, body)
        if operation.status == OperationStatus.QUEUED:
            background_tasks.add_task(application.run, operation.id)
        return operation
    except Exception as error:
        raise handle(error) from error
