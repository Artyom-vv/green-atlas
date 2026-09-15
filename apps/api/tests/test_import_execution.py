import asyncio
import io
from threading import Event

import pytest
from fastapi import UploadFile

from app.dxf_import.http_execution import execute_import
from app.projects.adapters import InMemoryProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    expected_project_version,
    reset_expected_project_version,
    set_expected_project_version,
)
from app.projects.contracts import Project


def test_import_yields_http_loop_and_preserves_if_match() -> None:
    started, release = Event(), Event()
    parsed_versions: list[int | None] = []

    def parse(project_id: str, filename: str, content: bytes | bytearray) -> Project:
        parsed_versions.append(expected_project_version())
        started.set()
        assert release.wait(timeout=3), "Import blocked the event loop"
        return Project(id=project_id, name=filename)

    async def read(file: UploadFile) -> bytearray:
        return bytearray(await file.read())

    async def scenario() -> None:
        token = set_expected_project_version('"7"')
        try:
            request = asyncio.create_task(
                execute_import(
                    parse,
                    "one",
                    "source.dxf",
                    UploadFile(io.BytesIO(b"source")),
                    read,
                )
            )
            await asyncio.wait_for(asyncio.to_thread(started.wait, 2), timeout=3)
            assert started.is_set()
            # This task represents an independent health/map/status request.
            release.set()
            result = await request
            assert result.id == "one"
            assert parsed_versions == [7]
        finally:
            release.set()
            reset_expected_project_version(token)

    asyncio.run(scenario())


def test_change_during_import_cannot_replace_source() -> None:
    repository = InMemoryProjectRepository()
    project = repository.create(Project(name="original"))
    repository.save_source(project.id, b"original source")

    def parse(project_id: str, filename: str, content: bytes | bytearray) -> Project:
        revision = repository.get(project_id)
        other = repository.get(project_id)
        other.name = "concurrent edit"
        repository.save(other)
        revision.name = filename
        return repository.save(revision, source=content)

    async def read(file: UploadFile) -> bytearray:
        return bytearray(await file.read())

    async def scenario() -> None:
        with pytest.raises(ProjectVersionConflict):
            await execute_import(
                parse,
                project.id,
                "replacement",
                UploadFile(io.BytesIO(b"new source")),
                read,
            )

    asyncio.run(scenario())
    assert repository.get_source(project.id) == b"original source"
    assert repository.get(project.id).name == "concurrent edit"


def test_cancelled_http_request_keeps_parser_capacity_until_completion() -> None:
    started, release, second_started = Event(), Event(), Event()

    def parse(project_id: str, filename: str, content: bytes | bytearray) -> Project:
        if project_id == "first":
            started.set()
            assert release.wait(timeout=3)
        else:
            second_started.set()
        return Project(id=project_id, name=filename)

    async def read(file: UploadFile) -> bytearray:
        return bytearray(await file.read())

    async def scenario() -> None:
        first = asyncio.create_task(
            execute_import(parse, "first", "a.dxf", UploadFile(io.BytesIO(b"a")), read)
        )
        await asyncio.to_thread(started.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(
            execute_import(parse, "second", "b.dxf", UploadFile(io.BytesIO(b"b")), read)
        )
        try:
            assert not await asyncio.to_thread(second_started.wait, 0.15)
        finally:
            release.set()
        assert (await second).id == "second"

    asyncio.run(scenario())
