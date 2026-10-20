"""Распознавание: ограниченные повторы временных отказов и безопасный контракт шлюза."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.layer_recognition.budget import RecognitionUnavailable
from app.shared.failures import FailureParams, FailureReason

RETRY_DELAYS_SECONDS = (2.0, 5.0, 10.0)
TRANSIENT_HTTP_STATUSES = frozenset({500, 502, 503, 504})
RELAY_FAILURE_MAX_BYTES = 4096


def temporary_failure(error: RecognitionUnavailable) -> bool:
    """Разрешать повтор только отмеченного транспортного или временного HTTP-отказа."""
    return error.retryable and (
        error.reason
        in {
            FailureReason.RECOGNITION_OPENAI_NO_RESPONSE,
            FailureReason.RECOGNITION_RELAY_UNAVAILABLE,
        }
        or (
            error.reason == FailureReason.RECOGNITION_OPENAI_HTTP_FAILURE
            and error.params.get("http_status") in TRANSIENT_HTTP_STATUSES
        )
    )


class RelayFailure(BaseModel):
    """Только известный код и HTTP-статус, без текста или данных ответа модели."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["green-atlas.recognition-failure/1"]
    code: FailureReason
    retryable: StrictBool = False
    http_status: StrictInt | None = Field(default=None, ge=100, le=599)

    @classmethod
    def from_error(cls, error: RecognitionUnavailable) -> "RelayFailure":
        return cls(
            schema_version="green-atlas.recognition-failure/1",
            code=error.reason,
            retryable=temporary_failure(error),
            http_status=error.params.get("http_status"),
        )

    def to_error(self) -> RecognitionUnavailable:
        if not self.code.value.startswith("RECOGNITION_"):
            raise ValueError("Неизвестный отказ распознавания")
        params: FailureParams | None = (
            {"http_status": self.http_status} if self.http_status else None
        )
        error = RecognitionUnavailable(self.code, params, retryable=self.retryable)
        error.retryable = temporary_failure(error)
        return error
