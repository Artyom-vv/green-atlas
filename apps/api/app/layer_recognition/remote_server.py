"""Узкий аутентифицированный шлюз OpenAI для распознавания слоёв с проверкой входа и
учётом бюджета.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.base import RequestResponseEndpoint

from app.layer_recognition.api_settings import ApiSettings
from app.layer_recognition.budget import RecognitionBudget, RecognitionUnavailable
from app.layer_recognition.payload_admission import anonymous_target_ids
from app.layer_recognition.providers import (
    COMPLETE_INSTRUCTIONS,
    CONFLICT_INSTRUCTIONS,
    INSTRUCTIONS,
    ConflictReply,
    FullModelReply,
    ModelReply,
    OpenAiLayerProvider,
    ReplyMode,
)
from app.layer_recognition.recovery import RelayFailure
from app.layer_recognition.relay_auth import RelayAccess
from app.shared.failures import FailureReason


class RelayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: Literal["layer_roles", "layer_conflicts"]
    source: str = Field(max_length=65_536)
    reply_mode: ReplyMode = ReplyMode.INITIAL


def _target_ids(task: str, source: str) -> set[str]:
    try:
        return anonymous_target_ids(task, source)
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=422, detail="Неверный пакет слоёв") from None


def create_app(
    provider: OpenAiLayerProvider,
    client_token: str,
    *,
    release_token_file: Path | None = None,
) -> FastAPI:
    access = RelayAccess(client_token, release_token_file)
    application = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @application.middleware("http")
    async def require_token(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.method == "POST" and request.url.path == "/recognize":
            supplied = request.headers.get("authorization", "")
            if not access.allows(supplied):
                return JSONResponse(status_code=401, content={"detail": "Нет доступа"})
        return await call_next(request)

    @application.get("/health")
    def health() -> dict[str, str]:
        provider.budget.total()
        return {"status": "ok"}

    @application.post("/recognize")
    def recognize(request: RelayRequest) -> dict[str, str]:
        if (
            request.task == "layer_conflicts"
            and request.reply_mode != ReplyMode.INITIAL
        ):
            raise HTTPException(status_code=422, detail="Неверный режим ответа")
        ids = _target_ids(request.task, request.source)
        schema: type[ModelReply] | type[FullModelReply] | type[ConflictReply]
        if request.task == "layer_roles" and request.reply_mode == ReplyMode.COMPLETE:
            instructions, schema, max_tokens = (
                COMPLETE_INSTRUCTIONS,
                FullModelReply,
                16384,
            )
        elif request.task == "layer_roles":
            instructions, schema, max_tokens = INSTRUCTIONS, ModelReply, 16384
        else:
            instructions, schema, max_tokens = (
                CONFLICT_INSTRUCTIONS,
                ConflictReply,
                8192,
            )
        try:
            output = provider._request(
                instructions, request.source, schema, request.task, max_tokens
            )
            parsed = schema.model_validate_json(output)
            proposals = (
                parsed.proposals
                if isinstance(parsed, (ModelReply, FullModelReply))
                else parsed.decisions
            )
            if (
                len(proposals) != len(ids)
                or {item.layer_id for item in proposals} != ids
            ):
                raise ValueError("different layers")
        except RecognitionUnavailable as error:
            raise HTTPException(
                status_code=503,
                detail=RelayFailure.from_error(error).model_dump(mode="json"),
            ) from None
        except ValueError:
            raise HTTPException(
                status_code=502, detail="Неверный ответ модели"
            ) from None
        return {"output": output}

    return application


def create_app_from_environment() -> FastAPI:
    directory = Path(os.environ["CREDENTIALS_DIRECTORY"])
    key = (directory / "openai_key").read_text().strip()
    proxy = (directory / "proxy_url").read_text().strip() or None
    if proxy is None:
        raise RecognitionUnavailable(FailureReason.RECOGNITION_SERVER_PROXY_MISSING)
    client_token = (directory / "client_token").read_text().strip()
    ledger = Path("/var/lib/green-atlas-luna/budget.sqlite3")
    settings = ApiSettings(key=key, proxy=proxy, ledger=ledger)
    provider = OpenAiLayerProvider(settings)
    RecognitionBudget(ledger).total()
    release_token_path = os.environ.get("GREEN_ATLAS_RELAY_RELEASE_TOKEN_FILE")
    return create_app(
        provider,
        client_token,
        release_token_file=Path(release_token_path) if release_token_path else None,
    )
