"""Choose the supervised process for captures larger than the HTTP budget."""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from app.cad_import.policy import ConversionPolicy
from app.cad_import.process import run_converter
from app.dxf_import import limits
from app.dxf_import.live_contracts import LiveImportTask
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    advance_expected_project_version,
    expected_project_version,
)
from app.projects.contracts import Project
from app.shared.python_worker import worker_command

# The verified Kustanayskaya capture is 99 MB / 55,804 features; Berzarina
# and Kamchatskaya have >100k features. Keep the small path inexpensive, but
# never lift its 100k/3m guard inside an unsupervised HTTP process.
LIVE_PROCESS_THRESHOLD_BYTES = 64 * 1024 * 1024
LIVE_PROCESS_POLICY = ConversionPolicy(
    timeout_seconds=600,
    max_source_bytes=limits.MAX_CAD_SNAPSHOT_BYTES,
    max_output_bytes=64 * 1024,
    max_memory_bytes=6 * 1024**3,
)


def can_supervise_live(repository: object) -> bool:
    return (
        isinstance(repository, SqliteProjectRepository)
        and repository.path != ":memory:"
    )


def import_autocad_live_supervised(
    repository: SqliteProjectRepository,
    project_id: str,
    filename: str,
    content: bytes | bytearray,
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
) -> Project:
    if not content or len(content) > limits.MAX_CAD_SNAPSHOT_BYTES:
        raise ValueError("Недопустимый размер снимка AutoCAD")
    if repository.path == ":memory:":
        raise ValueError("Большой снимок AutoCAD требует локального хранилища")
    source_hash = sha256(content).hexdigest()
    database = Path(repository.path).resolve()
    with TemporaryDirectory(prefix="ga-live-import-", dir=database.parent) as scratch:
        directory = Path(scratch)
        source_path = directory / "capture.json"
        source_path.write_bytes(content)
        return _run_live_import(
            repository, project_id, filename, source_path, source_hash,
            autocad_version=autocad_version, target=target, directory=directory,
        )


def import_autocad_live_file_supervised(
    repository: SqliteProjectRepository,
    project_id: str,
    filename: str,
    source_path: Path,
    source_sha256: str,
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    check_cancelled: Callable[[], None] | None = None,
) -> Project:
    """Import the ticket-verified private file without copying it into RAM."""
    if not can_supervise_live(repository):
        raise ValueError("Локальный захват требует постоянного SQLite-хранилища")
    size = source_path.stat().st_size
    if not 0 < size <= limits.MAX_CAD_SNAPSHOT_BYTES:
        raise ValueError("Недопустимый размер снимка AutoCAD")
    if len(source_sha256) != 64 or any(char not in "0123456789abcdef" for char in source_sha256):
        raise ValueError("Недопустимый хеш снимка AutoCAD")
    database = Path(repository.path).resolve()
    with TemporaryDirectory(prefix="ga-live-import-", dir=database.parent) as scratch:
        return _run_live_import(
            repository, project_id, filename, source_path, source_sha256,
            autocad_version=autocad_version, target=target,
            directory=Path(scratch),
            check_cancelled=check_cancelled,
        )


def _run_live_import(
    repository: SqliteProjectRepository,
    project_id: str,
    filename: str,
    source_path: Path,
    source_sha256: str,
    *,
    autocad_version: str,
    target: Literal["macos-arm64", "macos-x86_64", "windows-x86_64"],
    directory: Path,
    check_cancelled: Callable[[], None] | None = None,
) -> Project:
    database = Path(repository.path).resolve()
    current = repository.get(project_id, lightweight=True)
    receipt_path = directory / "receipt.json"
    task = LiveImportTask(
        database_path=str(database),
        project_id=project_id,
        filename=filename,
        source_path=str(source_path),
        source_sha256=source_sha256,
        autocad_version=autocad_version,
        target=target,
        expected_state_version=expected_project_version() or current.state_version,
        receipt_path=str(receipt_path),
    )
    task_path = directory / "task.json"
    task_path.write_text(task.model_dump_json(), encoding="utf-8")
    result = run_converter(
        worker_command("app.dxf_import.live_worker", str(task_path)),
        receipt_path,
        directory / "worker.log",
        LIVE_PROCESS_POLICY,
        environment={
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONPATH": str(Path(__file__).resolve().parents[2]),
        },
        check_cancelled=check_cancelled,
    )
    if result.exit_code != 0 or not receipt_path.exists():
        raise ValueError(
            "AutoCAD-снимок не опубликован; дочерняя обработка завершилась с ошибкой"
        )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not receipt.get("ok"):
        raise ValueError(
            "AutoCAD-снимок не опубликован: "
            + str(receipt.get("error", "неизвестная ошибка"))
        )
    if (
        receipt.get("project_id") != project_id
        or receipt.get("source_sha256") != source_sha256
        or not isinstance(receipt.get("state_version"), int)
    ):
        raise ValueError("Квитанция дочерней обработки AutoCAD недостоверна")
    advance_expected_project_version(receipt["state_version"])
    # The child owns the complete graph; the parent only retrieves the compact
    # projection so 100k+ CAD objects cannot exhaust the desktop HTTP process.
    return repository.get(project_id, lightweight=True)
