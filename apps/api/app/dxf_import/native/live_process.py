"""Выбор изолированного процесса для крупного захвата AutoCAD и контроль его ресурсного
бюджета.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal, TypeGuard

from app.cad_import.contracts import CadConversionMemoryExceeded
from app.cad_import.legacy.process import run_converter
from app.cad_import.policy import ConversionPolicy
from app.dxf_import import limits
from app.dxf_import.native.live_contracts import (
    LIVE_IMPORT_FAILURE,
    LiveImportConflict,
    LiveImportProjectMissing,
    LiveImportRefusal,
    LiveImportTask,
)
from app.projects.adapters import SqliteProjectRepository
from app.projects.concurrency import (
    ProjectVersionConflict,
    advance_expected_project_version,
    expected_project_version,
)
from app.projects.contracts import Project
from app.shared.failures import DomainFailure, FailureReason, ProjectNotFound
from app.shared.python_worker import worker_command

# Проверенные примеры содержат десятки и сотни тысяч объектов. Малый путь
# остаётся дешёвым, но его лимиты 100 тысяч объектов и 3 миллиона вершин
# нельзя повышать внутри HTTP-процесса без отдельного надзора.
LIVE_PROCESS_THRESHOLD_BYTES = 64 * 1024 * 1024
LIVE_PROCESS_POLICY = ConversionPolicy(
    timeout_seconds=600,
    max_source_bytes=limits.MAX_CAD_SNAPSHOT_BYTES,
    max_output_bytes=64 * 1024,
    # Размер чертежа не обосновывает фиксированный предел RAM. Пик измеряется,
    # но сам по себе не прерывает импорт на компьютере пользователя.
    max_memory_bytes=None,
)
_LOGGER = logging.getLogger(__name__)


def can_supervise_live(repository: object) -> TypeGuard[SqliteProjectRepository]:
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
            repository,
            project_id,
            filename,
            source_path,
            source_hash,
            autocad_version=autocad_version,
            target=target,
            directory=directory,
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
    native_capture_root: Path | None = None,
) -> Project:
    """Импортировать приватный файл, проверенный ticket, без полной копии в RAM."""
    if not can_supervise_live(repository):
        raise ValueError("Локальный захват требует постоянного SQLite-хранилища")
    size = source_path.stat().st_size
    if not 0 < size <= limits.MAX_CAD_SNAPSHOT_BYTES:
        raise ValueError("Недопустимый размер снимка AutoCAD")
    if len(source_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in source_sha256
    ):
        raise ValueError("Недопустимый хеш снимка AutoCAD")
    database = Path(repository.path).resolve()
    with TemporaryDirectory(prefix="ga-live-import-", dir=database.parent) as scratch:
        return _run_live_import(
            repository,
            project_id,
            filename,
            source_path,
            source_sha256,
            autocad_version=autocad_version,
            target=target,
            directory=Path(scratch),
            check_cancelled=check_cancelled,
            native_capture_root=native_capture_root,
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
    native_capture_root: Path | None = None,
) -> Project:
    database = Path(repository.path).resolve()
    if (
        native_capture_root is not None
        and native_capture_root.resolve()
        != (
            Path(str(database) + ".cad-archives") / project_id / source_sha256
        ).resolve()
    ):
        raise ValueError(
            "Semantic evidence must stay in this project's private capture"
        )
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
        native_capture_root=str(native_capture_root)
        if native_capture_root is not None
        else None,
    )
    task_path = directory / "task.json"
    task_path.write_text(task.model_dump_json(), encoding="utf-8")
    try:
        result = run_converter(
            worker_command("app.dxf_import.native.live_worker", str(task_path)),
            receipt_path,
            directory / "worker.log",
            LIVE_PROCESS_POLICY,
            environment={
                **os.environ,
                "PYTHONUTF8": "1",
                "PYTHONPATH": str(Path(__file__).resolve().parents[3]),
            },
            check_cancelled=check_cancelled,
        )
    except CadConversionMemoryExceeded as error:
        raise DomainFailure(FailureReason.NATIVE_IMPORT_MEMORY_EXCEEDED) from error
    _LOGGER.info(
        "Импорт AutoCAD: проект %s, время %.2f с, пик RSS %d байт, код выхода %d",
        project_id,
        result.elapsed_seconds,
        result.peak_memory_bytes,
        result.exit_code,
    )
    if result.exit_code != 0 or not receipt_path.exists():
        raise ValueError(
            "AutoCAD-снимок не опубликован; дочерняя обработка завершилась с ошибкой"
        )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if not receipt.get("ok"):
        if receipt.get("failure") is not None:
            failure = LIVE_IMPORT_FAILURE.validate_python(receipt["failure"])
            if isinstance(failure, LiveImportRefusal):
                raise DomainFailure(failure.reason, failure.params)
            if failure.project_id != project_id:
                raise RuntimeError("Native capture failure belongs to another project")
            if isinstance(failure, LiveImportProjectMissing):
                raise ProjectNotFound(failure.project_id)
            if isinstance(failure, LiveImportConflict):
                raise ProjectVersionConflict(
                    failure.project_id,
                    failure.expected_version,
                    failure.current_version,
                )
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
    # Дочерний процесс владеет полным графом; родитель получает компактную проекцию,
    # чтобы крупный CAD-захват не исчерпал память desktop HTTP-процесса.
    return repository.get(project_id, lightweight=True)
