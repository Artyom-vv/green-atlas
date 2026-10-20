"""Desktop-адаптер распознавания передаёт классификационный вход; ключ OpenAI остаётся
на relay-сервере.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel

from app.layer_recognition.budget import RecognitionUnavailable
from app.layer_recognition.providers import (
    MAX_OUTPUT_BYTES,
    FullModelReply,
    OpenAiLayerProvider,
    ReplyMode,
)
from app.layer_recognition.recovery import RELAY_FAILURE_MAX_BYTES, RelayFailure
from app.layer_recognition.relay_settings import RelaySettings
from app.layer_recognition.request_payload import compact_json, model_request_body
from app.shared.failures import FailureReason


class RelayLayerProvider(OpenAiLayerProvider):
    def __init__(self, settings: RelaySettings) -> None:
        self.relay_settings = settings

    def _request(
        self,
        instructions: str,
        source: str,
        schema: type[BaseModel],
        name: str,
        max_output_tokens: int,
    ) -> str:
        if name not in {"layer_roles", "layer_conflicts"}:
            raise ValueError("Неизвестная операция распознавания")
        model_request_body(instructions, source, schema, name, max_output_tokens)
        payload = {"task": name, "source": source}
        if schema is FullModelReply:
            payload["reply_mode"] = ReplyMode.COMPLETE
        try:
            with httpx.Client(
                trust_env=False, follow_redirects=False, timeout=190
            ) as client:
                response = client.post(
                    self.relay_settings.url,
                    content=compact_json(payload).encode(),
                    headers={
                        "Authorization": "Bearer " + self.relay_settings.token,
                        "Content-Type": "application/json",
                    },
                )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_RELAY_UNAVAILABLE, retryable=True
            ) from None
        except httpx.HTTPError:
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_RELAY_UNAVAILABLE
            ) from None
        if response.status_code == 401:
            raise RecognitionUnavailable(FailureReason.RECOGNITION_RELAY_ACCESS_DENIED)
        if "reply_mode" in payload and _reply_mode_is_unsupported(response):
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_RELAY_PROTOCOL_UNSUPPORTED
            )
        if response.status_code != 200:
            if response.status_code == 503:
                failure = _known_server_failure(response)
                if failure is not None:
                    raise failure
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_RELAY_HTTP_FAILURE,
                {"http_status": response.status_code},
            )
        if len(response.content) > MAX_OUTPUT_BYTES:
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_RELAY_RESPONSE_TOO_LARGE
            )
        try:
            output = response.json()["output"]
        except (ValueError, KeyError, TypeError):
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_RELAY_RESPONSE_INVALID
            ) from None
        if not isinstance(output, str) or len(output.encode()) > MAX_OUTPUT_BYTES:
            raise RecognitionUnavailable(
                FailureReason.RECOGNITION_RELAY_RESPONSE_INVALID
            )
        return output


def _known_server_failure(response: httpx.Response) -> RecognitionUnavailable | None:
    """Старый общий 503 не отличает сбой сети от отказа бюджета и не повторяется."""
    if len(response.content) > RELAY_FAILURE_MAX_BYTES:
        return None
    try:
        return RelayFailure.model_validate(response.json()["detail"]).to_error()
    except (ValueError, KeyError, TypeError):
        return None


def _reply_mode_is_unsupported(response: httpx.Response) -> bool:
    """Выделять точный отказ старой схемы без переноса серверных данных в журнал."""
    if response.status_code != 422 or len(response.content) > MAX_OUTPUT_BYTES:
        return False
    try:
        errors = response.json()["detail"]
    except (ValueError, KeyError, TypeError):
        return False
    return isinstance(errors, list) and any(
        isinstance(error, dict)
        and error.get("type") == "extra_forbidden"
        and error.get("loc") == ["body", "reply_mode"]
        for error in errors
    )
