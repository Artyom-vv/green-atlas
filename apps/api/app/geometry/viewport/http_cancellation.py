"""Следить за соединением одного запроса, сохраняя синхронный обработчик карты."""

import asyncio
from collections.abc import AsyncIterator
from threading import Event

from fastapi import Request

from app.geometry.viewport.cancellation import (
    ViewportRequestCancelled,
    viewport_cancellation,
)


async def viewport_connection(request: Request) -> AsyncIterator[None]:
    stopped = Event()

    def check() -> None:
        if stopped.is_set():
            raise ViewportRequestCancelled()

    async def watch() -> None:
        try:
            while (await request.receive())["type"] != "http.disconnect":
                pass
        finally:
            stopped.set()

    # Этот GET не читает тело. Один receive-владелец ждёт настоящий ASGI disconnect;
    # polling is_disconnected может не дойти до receive через BaseHTTPMiddleware.
    with viewport_cancellation(check):
        watcher = asyncio.create_task(watch(), name="viewport-disconnect")
        try:
            yield
        finally:
            stopped.set()
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
