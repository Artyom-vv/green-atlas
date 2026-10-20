"""Ограниченный процесс AutoCAD создаёт отдельный результат из проверенного архива и
подтверждает квитанцию выпуска.
"""

import shutil
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.exporting.cad_contracts import (
    CadPlant,
    CadRelease,
    CadWriteReceipt,
    encode_plantings,
    validate_receipt,
)
from app.exporting.cad_process_diagnostics import (
    record_interrupted_process,
    record_process,
)
from app.native_query.transport import job_files
from app.native_query.transport import process as worker
from app.native_query.transport.process_contracts import (
    CoreCommand,
    NativeInputPackage,
    NativeQueryProcessConfig,
)
from app.native_query.transport.windows_supervision import CompletedJobCleanup
from app.shared.failures import DomainFailure, FailureReason


def _verify_archive(
    package: NativeInputPackage,
    root: Path,
    check: Callable[[], None] = lambda: None,
) -> None:
    try:
        job_files.verify_package(package, root, check)
    except DomainFailure:
        raise
    except (OSError, ValueError) as error:
        raise DomainFailure(FailureReason.CAD_RELEASE_ARCHIVE_INVALID) from error


def _verify_worker(bundle: Path, config: NativeQueryProcessConfig) -> None:
    try:
        job_files.verify_worker(bundle, config)
    except (OSError, ValueError) as error:
        raise DomainFailure(FailureReason.CAD_RELEASE_WORKER_DAMAGED) from error


def write_cad_release(
    package: NativeInputPackage,
    plants: tuple[CadPlant, ...],
    units: int,
    config: NativeQueryProcessConfig,
) -> CadRelease:
    request = encode_plantings(plants, units)
    if package.entry != "host.dwg":
        raise DomainFailure(FailureReason.CAD_RELEASE_ARCHIVE_INVALID)
    if sum(item.size_bytes for item in package.files) > config.max_package_bytes:
        raise DomainFailure(FailureReason.CAD_RELEASE_PACKAGE_TOO_LARGE)
    job_files.prepare_job_root(config.job_root)
    required = (
        2 * sum(item.size_bytes for item in package.files) + config.min_free_bytes
    )
    if shutil.disk_usage(config.job_root).free < required:
        raise DomainFailure(FailureReason.CAD_RELEASE_DISK_SPACE)
    started = time.monotonic()

    def check() -> None:
        try:
            worker._checkpoint(started, config, None)
        except worker._Stop as error:
            if error.failure.code != "timeout":
                raise
            raise DomainFailure(FailureReason.CAD_RELEASE_TIMEOUT) from error

    _verify_archive(package, package.root, check)
    _verify_worker(config.worker_bundle, config)
    job = Path(tempfile.mkdtemp(prefix="ga-cad-release-", dir=config.job_root))
    from app.projects.resource_scope import register_project_resource

    register_project_resource("cad-export-job", job.name, root=config.job_root)
    staged = job / "package"
    output = job / "result"
    output.mkdir()
    child = None
    try:
        bootstrap_size, bootstrap_sha = job_files.file_identity(
            config.bootstrap_template
        )
        job_files.copy_independent(
            config.bootstrap_template, job / "Bootstrap.dwg", check, bootstrap_size
        )
        for item in package.files:
            source = job_files.package_file(package.root, item.path)
            job_files.copy_independent(
                source, staged / item.path, check, item.size_bytes
            )
            if item.path != package.entry:
                job_files.copy_independent(
                    source, output / item.path, check, item.size_bytes
                )
        _verify_archive(package, staged, check)
        bundle = job_files.worker_destination(job)
        job_files.stage_worker(config.worker_bundle, bundle, check)
        _verify_worker(bundle, config)
        (job / "request.txt").write_bytes(request)
        script = worker._write_script(job, "release.scr", CoreCommand.RELEASE)
        profile = job / "profile"
        profile.mkdir()
        command = worker._command(
            config, job, script, profile, "ga-release-" + uuid4().hex
        )
        try:
            child = worker._spawn(command, job)
        except OSError as error:
            raise DomainFailure(FailureReason.CAD_RELEASE_CORE_UNAVAILABLE) from error
        request_sha = sha256(request).hexdigest()

        def completed_result() -> None:
            # Worker публикует release.json после освобождения обеих баз и
            # восстановления контекста. Проверяем весь контракт, а не имя потомка.
            check()
            _verify_archive(package, package.root, check)
            _verify_archive(package, staged, check)
            _verify_worker(bundle, config)
            if job_files.file_identity(job / "Bootstrap.dwg") != (
                bootstrap_size,
                bootstrap_sha,
            ):
                raise DomainFailure(FailureReason.CAD_RELEASE_RESULT_INVALID)
            if job_files.file_identity(job / "request.txt")[1] != request_sha:
                raise DomainFailure(FailureReason.CAD_RELEASE_RESULT_INVALID)
            _validate_completed_release(
                job, output, package, plants, units, config, request_sha
            )

        cleanup = (
            CompletedJobCleanup(completed_result)
            if isinstance(child, worker.OwnedWindowsProcess)
            else None
        )
        options = {"completed_cleanup": cleanup} if cleanup is not None else {}
        try:
            failure, log, signals = worker._supervise(
                child,
                job,
                started,
                config,
                None,
                ("release.json", "release.json.tmp"),
                **options,
            )
        except Exception as error:
            record_interrupted_process(
                job,
                command,
                process_id=child.pid,
                exit_code=child.returncode,
                error=error,
                signals=cleanup.signals if cleanup is not None else (),
                elapsed_seconds=time.monotonic() - started,
                max_log_bytes=config.max_log_bytes,
            )
            raise
        cleanup_verified = cleanup is not None and cleanup.accepts_signals(signals)
        startup_failed = record_process(
            job,
            command,
            process_id=child.pid,
            exit_code=child.returncode,
            failure=failure,
            signals=signals,
            elapsed_seconds=time.monotonic() - started,
            log=log,
            completed_cleanup_verified=cleanup_verified,
        )
        if failure or child.returncode != 0 or (signals and not cleanup_verified):
            if failure is not None and failure.code == "cancelled":
                raise worker._Stop(failure.code, failure.message)
            reason = (
                FailureReason.CAD_RELEASE_TIMEOUT
                if failure is not None and failure.code == "timeout"
                else FailureReason.CAD_RELEASE_STARTUP_FAILED
                if startup_failed
                else FailureReason.CAD_RELEASE_UNFINISHED
            )
            raise DomainFailure(reason)
        _verify_archive(package, package.root)
        _verify_archive(package, staged)
        _verify_worker(bundle, config)
        if job_files.file_identity(job / "Bootstrap.dwg") != (
            bootstrap_size,
            bootstrap_sha,
        ):
            raise DomainFailure(FailureReason.CAD_RELEASE_RESULT_INVALID)
        request_sha = sha256(request).hexdigest()
        if job_files.file_identity(job / "request.txt")[1] != request_sha:
            raise DomainFailure(FailureReason.CAD_RELEASE_RESULT_INVALID)
        return _accepted_release(
            job, output, package, plants, units, config, request_sha
        )
    finally:
        if child is not None and child.poll() is None:
            worker._stop_group(child, config.terminate_grace_seconds, [])
        # Задание сохраняется для диагностики; исходник и CAD-комплект здесь не удаляются.
        _verify_archive(package, package.root)


def _validate_completed_release(
    job: Path,
    output: Path,
    package: NativeInputPackage,
    plants: tuple[CadPlant, ...],
    units: int,
    config: NativeQueryProcessConfig,
    request_sha: str,
) -> tuple[CadWriteReceipt, Path, Path]:
    """Проверить завершённость без сборки всего DWG-комплекта в памяти."""
    try:
        receipt_path = job_files.package_file(job, "release.json")
        if (
            receipt_path.stat().st_size > config.max_reply_bytes
            or (job / "release.json.tmp").exists()
        ):
            raise ValueError("Не получено завершённое подтверждение CAD-выпуска")
        receipt = CadWriteReceipt.model_validate_json(receipt_path.read_bytes())
        result_path = job_files.package_file(output, "planting-plan.dwg")
        _, result_sha = job_files.file_identity(
            result_path, limit=config.max_package_bytes
        )
        source_file = next(item for item in package.files if item.path == package.entry)
        validate_receipt(
            receipt, plants, units, request_sha, source_file.sha256, result_sha
        )
        for item in package.files:
            if item.path == package.entry:
                continue
            path = job_files.package_file(output, item.path)
            if job_files.file_identity(path, limit=item.size_bytes) != (
                item.size_bytes,
                item.sha256,
            ):
                raise ValueError("Подоснова результата изменилась при выпуске")
        return receipt, receipt_path, result_path
    except (OSError, ValueError) as error:
        raise DomainFailure(FailureReason.CAD_RELEASE_RESULT_INVALID) from error


def _accepted_release(
    job: Path,
    output: Path,
    package: NativeInputPackage,
    plants: tuple[CadPlant, ...],
    units: int,
    config: NativeQueryProcessConfig,
    request_sha: str,
) -> CadRelease:
    # Публикуемый набор собирается только после остановки собственного задания
    # и независимой повторной проверки результата.
    receipt, receipt_path, result_path = _validate_completed_release(
        job, output, package, plants, units, config, request_sha
    )
    try:
        files = {"planting-plan.dwg": result_path.read_bytes()}
        for item in package.files:
            if item.path != package.entry:
                path = job_files.package_file(output, item.path)
                files[item.path] = path.read_bytes()
        files["cad-receipt.json"] = receipt_path.read_bytes()
        return CadRelease(files=files, entry="planting-plan.dwg", receipt=receipt)
    except (OSError, ValueError) as error:
        raise DomainFailure(FailureReason.CAD_RELEASE_RESULT_INVALID) from error
