"""Translation of domain failures to the public HTTP error contract."""

from fastapi import HTTPException

from app.contracts import ApiError
from app.dxf_import.native_contracts import NativeDxfAssetUnavailable
from app.planning.domain import PlanVersionConflict
from app.projects.concurrency import ProjectVersionConflict


def handle(error: Exception) -> HTTPException:
    if isinstance(error, NativeDxfAssetUnavailable):
        return HTTPException(
            status_code=409,
            detail=ApiError(
                code="NATIVE_DXF_ASSET_UNAVAILABLE", message=str(error)
            ).model_dump(),
        )
    if isinstance(error, ProjectVersionConflict):
        return HTTPException(
            status_code=409,
            detail=ApiError(
                code="PROJECT_VERSION_CONFLICT",
                message="Проект изменён в другой вкладке. Обновите данные перед повтором действия.",
                details={
                    "project_id": error.project_id,
                    "expected_version": error.expected_version,
                    "current_version": error.current_version,
                },
            ).model_dump(),
        )
    if isinstance(error, PlanVersionConflict):
        return HTTPException(
            status_code=409,
            detail=ApiError(
                code="PLAN_VERSION_CONFLICT",
                message="План изменился после предпросмотра. Рассчитайте изменения ещё раз.",
                details={
                    "expected_version": error.expected_version,
                    "current_version": error.current_version,
                },
            ).model_dump(),
        )
    if isinstance(error, KeyError):
        return HTTPException(
            status_code=404,
            detail=ApiError(
                code="NOT_FOUND", message=str(error).strip("'"), details={}
            ).model_dump(),
        )
    return HTTPException(
        status_code=400,
        detail=ApiError(
            code="BAD_REQUEST", message=str(error), details={}
        ).model_dump(),
    )
