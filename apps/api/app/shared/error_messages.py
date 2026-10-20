"""Домен публичных отказов: объяснения исключений и ошибок проверки DTO.

Тексты принадлежат messages; этот модуль связывает их с доменными контрактами.
Диагностические сведения об исключениях остаются на границе HTTP.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.planning.domain import PlanVersionConflict
from app.projects.concurrency import ProjectVersionConflict
from app.shared.failures import (
    DomainFailure,
    FailureParams,
    FailureReason,
    ProjectNotFound,
    ResourceNotFound,
)
from app.shared.messages import has_message
from app.shared.messages import message as message

_PARAMS = TypeAdapter(FailureParams)


def refusal_message(error: Exception) -> str | None:
    if isinstance(error, (DomainFailure, ResourceNotFound)):
        try:
            if not isinstance(error.reason, FailureReason):
                return None
            params = _PARAMS.validate_python(error.params, strict=True)
            return message(error.reason, params)
        except (ValidationError, KeyError, TypeError, ValueError, AttributeError):
            return None
    if isinstance(error, ProjectNotFound):
        return message("project_missing")
    if isinstance(error, ProjectVersionConflict):
        return message("project_version_conflict")
    if isinstance(error, PlanVersionConflict):
        return message("plan_version_conflict")
    return None


def public_failure_message(error: Exception, fallback: str = "internal_error") -> str:
    return refusal_message(error) or message(fallback)


def validation_messages(errors: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Объясняет структурированные причины Pydantic без раскрытия входных данных."""
    fields: dict[str, list[str]] = {}
    for error in errors:
        field = (
            ".".join(
                str(part)
                for part in error["loc"]
                if part not in {"body", "query", "path"}
            )
            or "request"
        )
        context = error.get("ctx") or {}
        cause = context.get("error")
        if isinstance(cause, DomainFailure):
            explanation = refusal_message(cause) or message("validation.invalid")
        else:
            key = f"validation.{error['type']}"
            explanation = message(
                key if has_message(key) else "validation.invalid", context
            )
        fields.setdefault(field, []).append(explanation)
    return fields
