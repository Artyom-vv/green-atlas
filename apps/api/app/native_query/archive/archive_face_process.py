"""Ограниченное Core-задание готовит исходные области из отдельной копии CAD-архива."""

import json
import shutil
import tempfile
import time
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
        check()
        child = worker._spawn(command, job)
        failure, _log, signals = worker._supervise(
            child, job, started, config, cancelled
        )
        if failure or child.returncode != 0 or signals:
            raise ValueError(
                "Фоновая подготовка AutoCAD не завершена. Повторите подготовку проекта"
            )
        check()
        job_files.verify_package(package, staged, check)
        job_files.verify_worker(bundle, config)
        if job_files.file_identity(job / "Bootstrap.dwg") != bootstrap:
            raise ValueError("AutoCAD изменил служебный документ подготовки")
        request_sha = sha256(request).hexdigest()
        if (
            job_files.file_identity(job / "request.txt", limit=MAX_FACE_REQUEST_BYTES)[
                1
            ]
            != request_sha
            or (job / "package-entry.txt").read_bytes() != entry
        ):
            raise ValueError("Исходные сведения CAD-задания изменены")
        output = job_files.package_file(job, "native.json")
        if (
            output.stat().st_size > config.max_reply_bytes
            or (job / "native.json.tmp").exists()
        ):
            raise ValueError("Не получен завершённый результат подготовки CAD")
        reply_bytes = output.read_bytes()
        reply = FacePreparation.model_validate_json(reply_bytes)
        lifecycle_path = job_files.package_file(job, "lifecycle.json")
        if (
            lifecycle_path.stat().st_size > MAX_LIFECYCLE_RECEIPT_BYTES
            or (job / "lifecycle.json.tmp").exists()
        ):
            raise ValueError("Некорректное подтверждение завершения CAD-задания")
        lifecycle = json.loads(lifecycle_path.read_bytes())
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
        return reply
    except OSError as error:
        raise ValueError(
            "Файлы фоновой подготовки недоступны. Повторите подготовку проекта"
        ) from error
    finally:
        if child is not None and child.poll() is None:
            worker._stop_group(child, config.terminate_grace_seconds, [])
        job_files.verify_package(package, package.root)
