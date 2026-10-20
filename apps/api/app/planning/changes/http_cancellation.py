"""Передавать разрыв HTTP-предпросмотра синхронному расчёту через его контекст."""

import asyncio
from collections.abc import AsyncIterator
from threading import Event

from fastapi import Request

from app.operations.progress import OperationCancelled, operation_cancellation


async def preview_connection(request: Request) -> AsyncIterator[None]:
    """FastAPI уже прочитал POST body перед исполнением зависимостей маршрута."""
    stopped = Event()

    def check() -> None:
        if stopped.is_set():
            raise OperationCancelled("Соединение предпросмотра закрыто")

    async def watch() -> None:
        try:
            while (await request.receive())["type"] != "http.disconnect":
                pass
        finally:
            stopped.set()

    with operation_cancellation(check):
        watcher = asyncio.create_task(watch(), name="preview-disconnect")
        try:
            yield
        finally:
            stopped.set()
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
