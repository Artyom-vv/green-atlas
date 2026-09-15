"""Serialize heavy uploads while keeping the HTTP event loop available."""

import asyncio
from collections.abc import Awaitable, Callable

import anyio
from fastapi import UploadFile
from starlette.concurrency import run_in_threadpool

from app.projects.contracts import Project

# One parser at a time per API process; independent requests remain responsive.
IMPORT_CONCURRENCY = 1
_import_capacity = anyio.CapacityLimiter(IMPORT_CONCURRENCY)
_inflight: set[asyncio.Task[Project]] = set()


async def execute_import(
    operation: Callable[[str, str, bytes | bytearray], Project],
    project_id: str,
    filename: str,
    file: UploadFile,
    read_upload: Callable[[UploadFile], Awaitable[bytearray]],
) -> Project:
    borrower = object()
    await _import_capacity.acquire_on_behalf_of(borrower)
    try:
        content = await read_upload(file)
    except BaseException:
        _import_capacity.release_on_behalf_of(borrower)
        raise
    task = asyncio.create_task(
        _complete_import(operation, project_id, filename, content, borrower)
    )
    _inflight.add(task)
    task.add_done_callback(_finished)
    # Disconnecting an upload is not a cancellation of its accepted server
    # operation. Keep its capacity until the worker has actually finished.
    return await asyncio.shield(task)


async def _complete_import(
    operation: Callable[[str, str, bytes | bytearray], Project],
    project_id: str,
    filename: str,
    content: bytearray,
    borrower: object,
) -> Project:
    try:
        # AnyIO propagates If-Match to the worker; the atomic repository check
        # rejects any source changed during parsing.
        return await run_in_threadpool(operation, project_id, filename, content)
    finally:
        _import_capacity.release_on_behalf_of(borrower)


def _finished(task: asyncio.Task[Project]) -> None:
    _inflight.discard(task)
    if not task.cancelled():
        task.exception()  # Observe failures even if the HTTP caller disconnected.
