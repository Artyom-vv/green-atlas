"""Bounded AutoCAD worker writing only a new result from a verified archive."""

import shutil
import subprocess
import tempfile
import time
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
from app.native_query import process as worker
from app.native_query.process_contracts import (
    NativeInputPackage,
    NativeQueryProcessConfig,
)


def write_cad_release(
    package: NativeInputPackage,
    plants: tuple[CadPlant, ...],
    units: int,
    config: NativeQueryProcessConfig,
) -> CadRelease:
    request = encode_plantings(plants, units)
    if package.entry != "host.dwg":
        raise ValueError("Для выпуска требуется архив того же захвата AutoCAD")
    if sum(item.size_bytes for item in package.files) > config.max_package_bytes:
        raise ValueError("CAD-архив превышает лимит выпуска")
    config.job_root.mkdir(parents=True, exist_ok=True)
    required = (
        2 * sum(item.size_bytes for item in package.files) + config.min_free_bytes
    )
    if shutil.disk_usage(config.job_root).free < required:
        raise ValueError("Недостаточно места для отдельного CAD-результата")
    started = time.monotonic()

    def check():
        worker._checkpoint(started, config, None)

    worker._verify(package, package.root, check)
    worker._worker(config.worker_bundle, config)
    job = Path(tempfile.mkdtemp(prefix="ga-cad-release-", dir=config.job_root))
    staged = job / "package"
    output = job / "result"
    output.mkdir()
    child = None
    try:
        bootstrap_size, bootstrap_sha = worker._digest(config.bootstrap_template)
        worker._copy(
            config.bootstrap_template, job / "Bootstrap.dwg", check, bootstrap_size
        )
        for item in package.files:
            source = worker._file(package.root, item.path)
            worker._copy(source, staged / item.path, check, item.size_bytes)
            if item.path != package.entry:
                worker._copy(source, output / item.path, check, item.size_bytes)
        worker._verify(package, staged, check)
        bundle = job / "QueryWorker.dbx"
        worker._stage_worker(config.worker_bundle, bundle, check)
        worker._worker(bundle, config)
        (job / "request.txt").write_bytes(request)
        script = job / "release.scr"
        script.write_text(
            '(setvar "TRUSTEDPATHS" (getvar "DWGPREFIX"))\n'
            '(setvar "FILEDIA" 0)\n'
            '(arxload (strcat (getvar "DWGPREFIX") "QueryWorker.dbx"))\n'
            "GARELEASEPACKAGE\n_QUIT\n_Y\n\n",
            encoding="ascii",
        )
        profile = job / "profile"
        profile.mkdir()
        prefix = (
            ()
            if config.architecture == "native"
            else ("/usr/bin/arch", "-" + config.architecture)
        )
        command = (
            *prefix,
            str(config.core_executable),
            "/i",
            str(job / "Bootstrap.dwg"),
            "/s",
            str(script),
            "/isolate",
            "ga-release-" + uuid4().hex,
            str(profile),
        )
        child = subprocess.Popen(
            command,
            cwd=job,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            shell=False,
            start_new_session=True,
        )
        failure, _log, signals = worker._supervise(
            child,
            job,
            started,
            config,
            None,
            ("release.json", "release.json.tmp"),
        )
        if failure or child.returncode != 0 or signals:
            raise ValueError(
                "AutoCAD не завершил выпуск. Сохранённый проект не изменён"
            )
        worker._verify(package, package.root)
        worker._verify(package, staged)
        worker._worker(bundle, config)
        if worker._digest(job / "Bootstrap.dwg") != (bootstrap_size, bootstrap_sha):
            raise ValueError("AutoCAD изменил служебный документ выпуска")
        request_sha = sha256(request).hexdigest()
        if worker._digest(job / "request.txt")[1] != request_sha:
            raise ValueError("Состав выпуска изменён во время записи")
        receipt_path = worker._file(job, "release.json")
        if (
            receipt_path.stat().st_size > config.max_reply_bytes
            or (job / "release.json.tmp").exists()
        ):
            raise ValueError("Не получено завершённое подтверждение CAD-выпуска")
        receipt = CadWriteReceipt.model_validate_json(receipt_path.read_bytes())
        result_path = worker._file(output, "planting-plan.dwg")
        result_size, result_sha = worker._digest(
            result_path, limit=config.max_package_bytes
        )
        source = next(item for item in package.files if item.path == package.entry)
        validate_receipt(receipt, plants, units, request_sha, source.sha256, result_sha)
        files = {"planting-plan.dwg": result_path.read_bytes()}
        for item in package.files:
            if item.path == package.entry:
                continue
            path = worker._file(output, item.path)
            if worker._digest(path, limit=item.size_bytes) != (
                item.size_bytes,
                item.sha256,
            ):
                raise ValueError("Подоснова результата изменилась при выпуске")
            files[item.path] = path.read_bytes()
        files["cad-receipt.json"] = receipt_path.read_bytes()
        return CadRelease(files=files, entry="planting-plan.dwg", receipt=receipt)
    finally:
        if child is not None and child.poll() is None:
            worker._stop_group(child, config.terminate_grace_seconds, [])
        # Retain the job for diagnostics, never delete a source/package here.
        worker._verify(package, package.root)
