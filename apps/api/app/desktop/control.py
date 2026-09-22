"""Desktop-native control channel, deliberately absent from public API routes."""

import asyncio
import json
import re
from pathlib import Path

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse


class DesktopControl:
    def __init__(self, app, handoff):
        self.app, self.handoff = app, handoff

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if not path.startswith("/_desktop/handoffs"):
            return await self.app(scope, receive, send)
        if not scope.get("green_atlas.native"):
            return await JSONResponse(
                {"code": "NATIVE_CHANNEL_REQUIRED"}, status_code=403
            )(scope, receive, send)
        status = 200
        try:
            method = scope["method"]
            if path == "/_desktop/handoffs" and method == "POST":
                request = Request(scope, receive)
                body = bytearray()
                async with asyncio.timeout(10):
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > 8192:
                            raise ValueError("Слишком большой запрос передачи")
                value = json.loads(body)
                if (
                    not isinstance(value, dict)
                    or set(value) != {"ticket"}
                    or not isinstance(value["ticket"], str)
                ):
                    raise ValueError("Ожидается снимок AutoCAD")
                result = await run_in_threadpool(
                    self.handoff.submit, Path(value["ticket"])
                )
                status = 202
            elif match := re.fullmatch(
                r"/_desktop/handoffs/([a-f0-9]{64})(/cancel)?", path
            ):
                identifier, cancel = match.groups()
                if method == "POST" and cancel:
                    result = await run_in_threadpool(self.handoff.cancel, identifier)
                elif method == "GET" and not cancel:
                    result = await run_in_threadpool(self.handoff.get, identifier)
                else:
                    raise KeyError()
            else:
                raise KeyError()
        except KeyError:
            result, status = (
                {"code": "NOT_FOUND", "message": "Передача или проект не найдены"},
                404,
            )
        except (ValueError, OSError, TimeoutError):
            result, status = (
                {
                    "code": "TICKET_NOT_ACCEPTED",
                    "message": "Снимок не принят. Подготовьте его заново в AutoCAD.",
                },
                400,
            )
        await JSONResponse(
            result, status_code=status, headers={"Cache-Control": "no-store"}
        )(scope, receive, send)
