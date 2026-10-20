"""Authenticated desktop-only settings endpoints (never added to the public API)."""

import asyncio
import json

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import Receive, Scope, Send

from app.desktop import dependencies


async def dependency_settings(scope: Scope, receive: Receive, send: Send) -> None:
    action = scope["path"].removeprefix("/_desktop/dependencies")
    status = 200
    try:
        if scope["method"] == "GET" and not action:
            result = await run_in_threadpool(dependencies.discover)
        elif scope["method"] == "POST" and action in {"/check", "/save"}:
            raw = bytearray()
            async with asyncio.timeout(15):
                async for chunk in Request(scope, receive).stream():
                    raw.extend(chunk)
                    if len(raw) > 65536:
                        raise ValueError("Слишком большой запрос настройки")
            function = dependencies.save if action == "/save" else dependencies.check
            result = await run_in_threadpool(function, json.loads(raw))
            status = 200 if result["valid"] else 422
        else:
            result, status = {"message": "Настройка не найдена"}, 404
    except (ValueError, TypeError, OSError, TimeoutError) as error:
        result, status = (
            {"message": str(error) or "Не удалось проверить компоненты"},
            400,
        )
    await JSONResponse(
        result, status_code=status, headers={"Cache-Control": "no-store"}
    )(scope, receive, send)
