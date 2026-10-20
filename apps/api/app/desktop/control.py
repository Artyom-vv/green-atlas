"""Приватный канал команд native-оболочки к desktop runtime, отделённый от публичных
HTTP-маршрутов.
"""

import asyncio
import json
import re
from collections.abc import Callable
from pathlib import Path
from threading import Event

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.desktop.handoff import HandoffReceipt, LocalHandoff


class DesktopControl:
    def __init__(
        self,
        app: ASGIApp,
        handoff: LocalHandoff | None = None,
        *,
        shutdown: Callable[[], None] | None = None,
    ) -> None:
        self.app, self.handoff = app, handoff
        self.shutdown = shutdown

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if path == "/_desktop/dependencies" or path.startswith("/_desktop/dependencies/"):
            from app.desktop.dependency_control import dependency_settings

            return await dependency_settings(scope, receive, send)
        if path == "/_desktop/location/dadata":
            return await self.dadata_settings(scope, receive, send)
        if path == "/_desktop/shutdown" and self.shutdown is not None:
            return await self.request_shutdown(scope, receive, send)
        if not path.startswith("/_desktop/handoffs") or self.handoff is None:
            return await self.app(scope, receive, send)
        if not scope.get("green_atlas.native"):
            return await JSONResponse(
                {"code": "NATIVE_CHANNEL_REQUIRED"}, status_code=403
            )(scope, receive, send)
        status = 200
        result: object
        try:
            method = scope["method"]
            if path == "/_desktop/handoffs" and method == "GET":
                result = {"handoffs": await run_in_threadpool(self.handoff.list)}
            elif path == "/_desktop/handoffs" and method == "POST":
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
                r"/_desktop/handoffs/([a-f0-9]{64})(/(?:cancel|resume))?", path
            ):
                identifier, action = match.groups()
                if method == "POST" and action == "/cancel":
                    result = await run_in_threadpool(self.handoff.cancel, identifier)
                elif method == "POST" and action == "/resume":
                    result = await run_in_threadpool(self.handoff.resume, identifier)
                    status = 202
                elif method == "GET" and action is None:
                    query = Request(scope).query_params
                    if not query:
                        result = await run_in_threadpool(self.handoff.get, identifier)
                    elif (
                        list(query) == ["after"]
                        and len(query.getlist("after")) == 1
                        and re.fullmatch(r"[0-9]{1,19}", query["after"])
                        and int(query["after"]) <= 2**63 - 1
                    ):
                        result = await self.wait_receipt(
                            identifier, int(query["after"]), receive
                        )
                        if result is None:
                            return
                    else:
                        raise ValueError("Ожидается ревизия квитанции")
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

    async def dadata_settings(self, scope: Scope, receive: Receive, send: Send) -> None:
        from app.location.providers import load_dadata_token, save_dadata_token

        method = scope.get("method")
        if method == "GET":
            try:
                configured = bool(await run_in_threadpool(load_dadata_token))
            except ValueError:
                configured = False
            response = JSONResponse({"configured": configured})
        elif method == "POST":
            try:
                body = bytearray()
                async with asyncio.timeout(10):
                    async for chunk in Request(scope, receive).stream():
                        body.extend(chunk)
                        if len(body) > 1024:
                            raise ValueError()
                payload = json.loads(body)
                if not isinstance(payload, dict) or set(payload) != {"api_key"}:
                    raise ValueError()
                await run_in_threadpool(save_dadata_token, payload["api_key"])
                response = JSONResponse({"configured": True})
            except (ValueError, OSError, TimeoutError, TypeError):
                response = JSONResponse(
                    {
                        "code": "DADATA_SETTINGS_INVALID",
                        "message": "Не удалось сохранить ключ DaData",
                    },
                    status_code=400,
                )
        else:
            response = JSONResponse({"code": "NOT_FOUND"}, status_code=404)
        response.headers["Cache-Control"] = "no-store"
        await response(scope, receive, send)

    async def request_shutdown(
        self, scope: Scope, receive: Receive, send: Send
    ) -> None:
        if not scope.get("green_atlas.native"):
            return await JSONResponse(
                {"code": "NATIVE_CHANNEL_REQUIRED"}, status_code=403
            )(scope, receive, send)
        if scope.get("method") != "POST":
            return await JSONResponse({"code": "NOT_FOUND"}, status_code=404)(
                scope, receive, send
            )
        try:
            body = bytearray()
            async with asyncio.timeout(10):
                async for chunk in Request(scope, receive).stream():
                    body.extend(chunk)
                    if len(body) > 64:
                        raise ValueError("Слишком большой запрос завершения")
            if json.loads(body) != {}:
                raise ValueError("Ожидается пустой объект")
        except (ValueError, TimeoutError):
            return await JSONResponse(
                {"code": "DESKTOP_CONTROL_INVALID"}, status_code=400
            )(scope, receive, send)
        # Оболочка получает квитанцию до остановки listener и освобождения lease.
        await JSONResponse(
            {"state": "stopping"},
            status_code=202,
            headers={"Cache-Control": "no-store"},
        )(scope, receive, send)
        assert self.shutdown is not None
        self.shutdown()

    async def wait_receipt(
        self, identifier: str, after: int, receive: Receive
    ) -> HandoffReceipt | None:
        """Отдать JSON после изменения; разрыв соединения освобождает ожидающий поток."""
        assert self.handoff is not None
        stop = Event()
        waiting = asyncio.create_task(
            run_in_threadpool(self.handoff.wait, identifier, after, stop)
        )

        async def disconnected() -> None:
            while (await receive())["type"] != "http.disconnect":
                pass

        connection = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait(
                {waiting, connection}, return_when=asyncio.FIRST_COMPLETED
            )
            return None if connection in done else await waiting
        finally:
            # SQLite может удерживать блокировку журнала; cleanup не останавливает API loop.
            stop.set()
            waiting.cancel()
            connection.cancel()
            try:
                await run_in_threadpool(self.handoff.cancel_wait, stop)
            finally:
                await asyncio.gather(waiting, connection, return_exceptions=True)
