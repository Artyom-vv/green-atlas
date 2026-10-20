"""Домен HTTP API: перевод доменных отказов в единый публичный контракт.

Метаданные маршрута сохраняют локальные границы ошибок; адаптер FastAPI
отвечает за проверку аргументов и выполнение синхронных обработчиков.
"""

import logging
from collections.abc import Callable
from functools import wraps
from inspect import iscoroutinefunction
from typing import Any, ParamSpec, TypeVar
from uuid import uuid4

from fastapi import HTTPException
from fastapi.routing import APIRoute
from pydantic import ValidationError

from app.contracts import ApiError
from app.dxf_import.native.asset_contracts import NativeDxfAssetUnavailable
from app.dxf_import.review.contracts import (
    SourceAreaGroupCheck,
    SourceAreaGroupRejected,
)
from app.operations.progress import OperationCancelled
from app.planning.changes.contracts import ChangeSetCandidateResult, ChangeSetRejected
from app.planning.domain import PlanVersionConflict
from app.projects.concurrency import ProjectVersionConflict
from app.shared.error_messages import message, refusal_message
from app.shared.failures import DomainFailure, ProjectNotFound, ResourceNotFound

_LOCAL_BOUNDARY = "_green_atlas_local_error_boundary"
_TRANSLATED_BOUNDARY = "_green_atlas_translated_error_boundary"
P = ParamSpec("P")
R = TypeVar("R")
_LOGGER = logging.getLogger(__name__)


def local_error_boundary(endpoint: Callable[P, R]) -> Callable[P, R]:
    """Сохраняет собственную границу отказов обработчика при подключении маршрута."""
    setattr(endpoint, _LOCAL_BOUNDARY, True)
    return endpoint


class BusinessErrorRoute(APIRoute):
    """Переводит отказ после проверки входных DTO средствами FastAPI."""

    def __init__(self, path: str, endpoint: Callable[..., Any], **options: Any) -> None:
        # Типы отражают расширяемую границу FastAPI. wraps сохраняет исходную
        # сигнатуру; разные sync/async адаптеры сохраняют выполнение
        # синхронного обработчика в пуле потоков.
        super().__init__(path, _business_endpoint(endpoint), **options)


def handle(error: Exception) -> HTTPException:
    if isinstance(error, HTTPException):
        return error
    if isinstance(error, OperationCancelled):
        return HTTPException(
            status_code=499,
            detail=ApiError(
                code="REQUEST_CANCELLED", message="Расчёт отменён"
            ).model_dump(),
        )
    checked_refusal = _checked_result_refusal(error)
    if checked_refusal is not None:
        checked_explanation, details = checked_refusal
        return HTTPException(
            status_code=400,
            detail=ApiError(
                code="BAD_REQUEST",
                message=checked_explanation,
                details=details,
            ).model_dump(),
        )
    if isinstance(error, NativeDxfAssetUnavailable):
        return HTTPException(
            status_code=409,
            detail=ApiError(
                code="NATIVE_DXF_ASSET_UNAVAILABLE",
                message=message("NATIVE_DXF_ASSET_UNAVAILABLE"),
            ).model_dump(),
        )
    if isinstance(error, ProjectVersionConflict):
        return HTTPException(
            status_code=409,
            detail=ApiError(
                code="PROJECT_VERSION_CONFLICT",
                message=message("project_version_conflict"),
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
                message=message("plan_version_conflict"),
                details={
                    "expected_version": error.expected_version,
                    "current_version": error.current_version,
                },
            ).model_dump(),
        )
    explanation = refusal_message(error)
    if (
        isinstance(error, (ProjectNotFound, ResourceNotFound))
        and explanation is not None
    ):
        return HTTPException(
            status_code=404,
            detail=ApiError(
                code="NOT_FOUND", message=explanation, details={}
            ).model_dump(),
        )
    if isinstance(error, DomainFailure) and explanation is not None:
        return HTTPException(
            status_code=400,
            detail=ApiError(
                code="BAD_REQUEST",
                message=explanation,
                details={"reason": error.reason},
            ).model_dump(),
        )
    diagnostic_id = str(uuid4())
    _LOGGER.error(
        "Unhandled request failure [%s]",
        diagnostic_id,
        exc_info=(type(error), error, error.__traceback__),
    )
    return HTTPException(
        status_code=500,
        detail=ApiError(
            code="INTERNAL_ERROR",
            message=message("internal_error"),
            details={"diagnostic_id": diagnostic_id},
        ).model_dump(),
    )


def _business_endpoint(endpoint: Callable[..., Any]) -> Callable[..., Any]:
    # Повторное подключение маршрута сохраняет уже оформленную границу:
    # второй перевод ошибки превратил бы подтверждённый конфликт 409 в отказ 400.
    if getattr(endpoint, _LOCAL_BOUNDARY, False) or getattr(
        endpoint, _TRANSLATED_BOUNDARY, False
    ):
        return endpoint
    if iscoroutinefunction(endpoint):

        @wraps(endpoint)
        async def async_endpoint(*args: Any, **kwargs: Any) -> Any:
            try:
                return await endpoint(*args, **kwargs)
            except Exception as error:
                raise handle(error) from error

        wrapped: Callable[..., Any] = async_endpoint
    else:

        @wraps(endpoint)
        def sync_endpoint(*args: Any, **kwargs: Any) -> Any:
            try:
                return endpoint(*args, **kwargs)
            except Exception as error:
                raise handle(error) from error

        wrapped = sync_endpoint
    setattr(wrapped, _TRANSLATED_BOUNDARY, True)
    return wrapped


def _checked_result_refusal(error: Exception) -> tuple[str, dict[str, str]] | None:
    """Повторно проверяет изменяемый DTO перед публикацией известного отказа."""
    try:
        if isinstance(error, SourceAreaGroupRejected):
            result = SourceAreaGroupCheck.model_validate(error.result.model_dump())
            if not result.valid and result.reason:
                return result.reason, {}
        if isinstance(error, ChangeSetRejected):
            candidate = ChangeSetCandidateResult.model_validate(
                error.candidate.model_dump()
            )
            if candidate.status == "blocked" and candidate.reason:
                return candidate.reason, {"reason": candidate.code}
    except (AttributeError, ValidationError, TypeError, ValueError):
        pass
    return None
