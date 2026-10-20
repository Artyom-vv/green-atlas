"""Ограниченное Core-задание готовит исходные области из отдельной копии CAD-архива."""

import json
import shutil
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.native_query.archive.archive_faces import MAX_FACE_REQUEST_BYTES
from app.native_query.preparation.face_contracts import FacePreparation
from app.native_query.transport import job_files
from app.native_query.transport import process as worker
from app.native_query.transport.process_contracts import (
    MAX_LIFECYCLE_RECEIPT_BYTES,
    CoreCommand,
    NativeInputPackage,
    NativeQueryProcessConfig,
    matches_lifecycle_receipt,
)
from app.operations.progress import check_operation_cancelled
from app.projects.resource_scope import register_project_resource
from app.shared.operation_diagnostics import (
    NativeFailureDiagnostic,
    NativeOperationError,
)


def _read_completed(path: Path, limit: int) -> bytes:
    with job_files.open_regular(path) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("Результат Core превышает допустимый размер")
    return data


def _without_pending(job: Path, name: str) -> None:
    try:
        (job / name).lstat()
    except FileNotFoundError:
        return
    raise ValueError("Core оставил незавершённую атомарную запись")


def _validate_completed_faces(
    package: NativeInputPackage,
    staged: Path,
    bundle: Path,
    job: Path,
    request: bytes,
    entry: bytes,
    bootstrap: tuple[int, str],
    session_id: str,
    layers: tuple[str, ...],
    companion_layers: tuple[str, ...],
    config: NativeQueryProcessConfig,
    check: Callable[[], None],
) -> FacePreparation:
    """Полная проверка по заново прочитанным файлам до/после owned-очистки."""
    check()
    job_files.verify_package(package, package.root, check)
    job_files.verify_package(package, staged, check)
    job_files.verify_worker(config.worker_bundle, config)
    job_files.verify_worker(bundle, config)
    if job_files.file_identity(job / "Bootstrap.dwg") != bootstrap:
        raise ValueError("AutoCAD изменил служебный документ подготовки")
    request_sha = sha256(request).hexdigest()
    if job_files.file_identity(job / "request.txt", limit=MAX_FACE_REQUEST_BYTES) != (
        len(request),
        request_sha,
    ) or job_files.file_identity(job / "package-entry.txt", limit=len(entry)) != (
        len(entry),
        sha256(entry).hexdigest(),
    ):
        raise ValueError("Исходные сведения CAD-задания изменены")
    _without_pending(job, "native.json.tmp")
    _without_pending(job, "lifecycle.json.tmp")
    reply_bytes = _read_completed(
        job_files.package_file(job, "native.json"), config.max_reply_bytes
    )
    reply = FacePreparation.model_validate_json(reply_bytes)
    lifecycle = json.loads(
        _read_completed(
            job_files.package_file(job, "lifecycle.json"), MAX_LIFECYCLE_RECEIPT_BYTES
        )
    )
    source = next(item for item in package.files if item.path == package.entry)
    if (
        not matches_lifecycle_receipt(
            lifecycle,
            request_sha256=request_sha,
            source_sha256=source.sha256,
            reply_sha256=sha256(reply_bytes).hexdigest(),
        )
        or reply.session_id != session_id
        or reply.request_sha256 != request_sha
        or reply.layers != layers
        or reply.companion_layers != companion_layers
    ):
        raise ValueError("Результат фоновой подготовки относится к другому заданию")
    check()
    return reply


def prepare_archive_faces(
    package: NativeInputPackage,
    request: bytes,
    session_id: str,
    layers: tuple[str, ...],
    config: NativeQueryProcessConfig,
    *,
    companion_layers: tuple[str, ...] = (),
) -> FacePreparation:
    if not request or len(request) > MAX_FACE_REQUEST_BYTES:
        raise ValueError("Некорректный объём входных данных подготовки CAD")
    job_files.prepare_job_root(config.job_root)
    size = sum(item.size_bytes for item in package.files)
    if size > config.max_package_bytes:
        raise ValueError("CAD-архив превышает лимит подготовки")
    if (
        shutil.disk_usage(config.job_root).free
        < size + len(request) + config.min_free_bytes
    ):
        raise ValueError("Недостаточно места для фоновой подготовки CAD")
    started = time.monotonic()

    def cancelled() -> bool:
        check_operation_cancelled()
        return False

    def check() -> None:
        worker._checkpoint(started, config, cancelled)

    job_files.verify_package(package, package.root, check)
    job_files.verify_worker(config.worker_bundle, config)
    job = Path(tempfile.mkdtemp(prefix="ga-native-query-", dir=config.job_root))
    staged = job / "package"
    child = None
    cleanup = None
    signals: tuple[str, ...] = ()
    failure = None
    phase = "staging"
    try:
        register_project_resource("cad-preparation-job", job.name, root=config.job_root)
        bootstrap = job_files.file_identity(config.bootstrap_template)
        job_files.copy_independent(
            config.bootstrap_template, job / "Bootstrap.dwg", check, bootstrap[0]
        )
        for item in package.files:
            job_files.copy_independent(
                job_files.package_file(package.root, item.path),
                staged / item.path,
                check,
                item.size_bytes,
            )
        job_files.verify_package(package, staged, check)
        bundle = job_files.worker_destination(job)
        job_files.stage_worker(config.worker_bundle, bundle, check)
        job_files.verify_worker(bundle, config)
        (job / "request.txt").write_bytes(request)
        entry = (str(staged / package.entry) + "\n").encode("utf-8")
        (job / "package-entry.txt").write_bytes(entry)
        script = worker._write_script(job, "query.scr", CoreCommand.FACES)
        profile = job / "profile"
        profile.mkdir()
        command = worker._command(
            config, job, script, profile, "ga-faces-" + uuid4().hex
        )

        def completed_result() -> FacePreparation:
            return _validate_completed_faces(
                package,
                staged,
                bundle,
                job,
                request,
                entry,
                bootstrap,
                session_id,
                layers,
                companion_layers,
                config,
                check,
            )

        def validate_completed() -> None:
            completed_result()

        cleanup = worker.CompletedJobCleanup(validate_completed)
        check()
        phase = "supervision"
        child = worker._spawn(command, job)
        failure, _log, signals = worker._supervise(
            child, job, started, config, cancelled, completed_cleanup=cleanup
        )
        if (
            failure
            or child.returncode != 0
            or (signals and not cleanup.accepts_signals(signals))
        ):
            raise ValueError(
                "Фоновая подготовка AutoCAD не завершена. Повторите подготовку проекта"
            )
        phase = "completion_validation"
        return completed_result()
    except (OSError, ValueError, worker._Stop) as error:
        message = (
            "Результат фоновой подготовки относится к другому заданию"
            if str(error) == "Результат фоновой подготовки относится к другому заданию"
            else "Фоновая подготовка AutoCAD не завершена. Повторите подготовку проекта"
        )
        raise NativeOperationError(
            message,
            diagnostic=NativeFailureDiagnostic(
                failure_code=error.failure.code
                if isinstance(error, worker._Stop)
                else failure.code
                if failure
                else "io_failure"
                if isinstance(error, OSError)
                else "invalid_reply",
                exit_code=child.returncode if child is not None else None,
                tree_empty=cleanup.tree_empty if cleanup is not None else None,
                signals=cleanup.signals
                if cleanup is not None and cleanup.signals
                else signals,
                core_log_path=job / "core.log",
                phase=cleanup.phase
                if cleanup is not None and phase == "supervision"
                else phase,
                cleanup_attempted=cleanup.attempted if cleanup is not None else False,
                cleanup_confirmed=cleanup.confirmed if cleanup is not None else False,
            ),
        ) from error
    finally:
        if child is not None and child.poll() is None:
            worker._stop_group(child, config.terminate_grace_seconds, [])
        job_files.verify_package(package, package.root)
