"""Общее Core-задание с отдельными POSIX и Windows владельцами процесса.

SHA, квитанции и бюджеты общие; файлы принадлежат платформенному job_files.
"""

from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.native_query.contracts import NativeObjectQuery
from app.native_query.transport import job_files
from app.native_query.transport.process_contracts import (
    MAX_LIFECYCLE_RECEIPT_BYTES,
    CoreCommand,
    NativeInputPackage,
    NativeProcessFailure,
    NativeProcessReceipt,
    NativeQueryProcessConfig,
    NativeQueryProcessResult,
    matches_lifecycle_receipt,
)
from app.native_query.transport.process_control import (
    CoreStop as _Stop,
)
from app.native_query.transport.process_control import (
    checkpoint as _checkpoint,
)
from app.native_query.transport.protocol import decode_reply, encode_request
from app.native_query.transport.windows_supervision import (
    CompletedJobCleanup,
    stop_owned,
    supervise,
)
from app.shared.child_dll_search import child_dll_search
from app.shared.windows_process import OwnedWindowsProcess, launch_owned

CoreProcess = subprocess.Popen[bytes] | OwnedWindowsProcess


def _windows_platform() -> bool:
    return os.name == "nt"


def _write_script(job: Path, name: str, command: CoreCommand) -> Path:
    script = job / name
    binary = (
        "QueryWorker/GreenAtlasQuery.crx" if _windows_platform() else "QueryWorker.dbx"
    )
    trusted = (
        '(strcat (getvar "DWGPREFIX") ";" (getvar "DWGPREFIX") "QueryWorker")'
        if _windows_platform()
        else '(getvar "DWGPREFIX")'
    )
    script.write_text(
        f'(setvar "TRUSTEDPATHS" {trusted})\n'
        '(setvar "FILEDIA" 0)\n'
        f'(arxload (strcat (getvar "DWGPREFIX") "{binary}"))\n'
        f"{command.value}\n_QUIT\n_Y\n\n",
        encoding="ascii",
    )
    return script


def _command(
    config: NativeQueryProcessConfig,
    job: Path,
    script: Path,
    profile: Path,
    identity: str,
) -> tuple[str, ...]:
    prefix = (
        ()
        if config.architecture == "native" or _windows_platform()
        else ("/usr/bin/arch", "-" + config.architecture)
    )
    return (
        *prefix,
        str(config.core_executable),
        "/i",
        str(job / "Bootstrap.dwg"),
        "/s",
        str(script),
        "/isolate",
        identity,
        str(profile),
    )


def _spawn(command: tuple[str, ...], job: Path) -> CoreProcess:
    if _windows_platform():
        return launch_owned(command, cwd=job)
    child = None
    try:
        with child_dll_search(external=True):
            child = subprocess.Popen(
                command,
                cwd=job,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                shell=False,
                start_new_session=True,
            )
    except BaseException:
        if child is not None:
            _stop_group(child, 2, [])
        raise
    return child


def _group_alive(pid: int) -> bool:
    if sys.platform == "win32":
        raise OSError("Windows Core требует принадлежащий заданию Job Object")
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS сообщает EPERM при завершении лидера группы. До получения статуса
        # считаем группу существующей, не объявляя успешную остановку.
        return True


def _stop_group(child: CoreProcess, grace: float, signals: list[str]) -> None:
    if isinstance(child, OwnedWindowsProcess):
        if child.closed:
            return
        try:
            stop_owned(child, grace, signals)
        finally:
            child.close()
        return
    if sys.platform == "win32":
        raise OSError("Windows Core требует принадлежащий заданию Job Object")
    # PGID принадлежит только сеансу нашего Popen. Не ищем по имени приложения
    # и не посылаем сигналы группе API, родителя или интерактивного AutoCAD.
    for sig in (signal.SIGTERM, signal.SIGKILL):
        deadline = time.monotonic() + grace
        while True:
            child.poll()
            try:
                os.killpg(child.pid, sig)
                signals.append(sig.name)
                break
            except ProcessLookupError:
                child.wait(timeout=grace)
                return
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(min(0.01, grace))
        if sig == signal.SIGTERM:
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                child.poll()
                if not _group_alive(child.pid):
                    break
                time.sleep(min(0.01, grace))
    child.wait(timeout=grace)


def _output_size(
    job: Path, names: tuple[str, ...] = ("native.json", "native.json.tmp")
) -> int:
    total = 0
    for name in names:
        path = job / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if _windows_platform():
            try:
                job_files.package_file(job, name)
            except FileNotFoundError:
                # Собственный worker мог уже атомарно опубликовать временный файл.
                continue
            except ValueError as error:
                raise _Stop("invalid_reply", str(error)) from error
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise _Stop("invalid_reply", "Native output is not an independent file")
        total += info.st_size
    return total


def _supervise(
    child: CoreProcess,
    job: Path,
    started: float,
    config: NativeQueryProcessConfig,
    cancelled: Callable[[], bool] | None,
    output_names: tuple[str, ...] = (
        "native.json",
        "native.json.tmp",
        "faces.json",
        "faces.json.tmp",
    ),
    *,
    completed_cleanup: CompletedJobCleanup | None = None,
) -> tuple[NativeProcessFailure | None, bytes, tuple[str, ...]]:
    if isinstance(child, OwnedWindowsProcess):
        return supervise(
            child,
            job,
            started,
            config,
            cancelled,
            output_names,
            _output_size,
            completed_cleanup=completed_cleanup,
        )
    log = bytearray()
    signals: list[str] = []
    failure = None
    assert child.stdout is not None
    os.set_blocking(child.stdout.fileno(), False)
    with selectors.DefaultSelector() as selector:
        selector.register(child.stdout, selectors.EVENT_READ)

        def drain() -> None:
            for key, _ in selector.select(config.poll_seconds):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                remaining = config.max_log_bytes - len(log)
                log.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    raise _Stop("log_limit", "Core log exceeded its byte budget")

        try:
            while True:
                drain()
                _checkpoint(started, config, cancelled)
                if _output_size(job, output_names) > config.max_reply_bytes:
                    raise _Stop(
                        "output_limit", "Native output exceeded its byte budget"
                    )
                if child.poll() is not None:
                    if _group_alive(child.pid):
                        raise _Stop(
                            "process_group_alive", "Core left a live process group"
                        )
                    break
        except _Stop as error:
            failure = error.failure
        finally:
            try:
                _stop_group(child, config.terminate_grace_seconds, signals)
                # После остановки своей группы дочитываем конечный остаток канала.
                deadline = time.monotonic() + config.terminate_grace_seconds
                while selector.get_map() and time.monotonic() < deadline:
                    try:
                        drain()
                    except _Stop as error:
                        failure = failure or error.failure
                        break
            finally:
                child.stdout.close()
                with (job / "core.log").open("xb") as stream:
                    stream.write(log)
    return failure, bytes(log), tuple(signals)


def _diagnostic(job: Path, limit: int) -> tuple[bytes, bool]:
    """Сохранять ограниченный выход, включая незавершённый native-файл атомарной записи."""
    data, truncated, remaining = b"", False, limit
    for name in ("native.json", "native.json.tmp"):
        path = job / name
        if not path.exists() and not path.is_symlink():
            continue
        # Обрезать можно лишь собственный независимый выход после остановки группы.
        with job_files.open_regular(path, writable=True) as stream:
            size = os.fstat(stream.fileno()).st_size
            chunk = stream.read(remaining)
            if size > remaining:
                stream.truncate(remaining)
                truncated = True
            remaining -= len(chunk)
            if not data:
                data = chunk
    return data, truncated


def _validate_completed_query(
    package: NativeInputPackage,
    source_root: Path,
    staged: Path,
    bundle: Path,
    job: Path,
    query: NativeObjectQuery,
    request: bytes,
    bootstrap: tuple[int, str],
    face_request: bytes | None,
    config: NativeQueryProcessConfig,
    check: Callable[[], None],
) -> None:
    """Проверять завершённый запрос независимо до и после очистки своего job."""
    check()
    job_files.verify_package(package, source_root, check)
    job_files.verify_package(package, staged, check)
    job_files.verify_worker(config.worker_bundle, config)
    job_files.verify_worker(bundle, config)
    if job_files.file_identity(job / "Bootstrap.dwg") != bootstrap:
        raise ValueError("Bootstrap database was saved or changed")
    request_hash = sha256(request).hexdigest()
    if job_files.file_identity(job / "request.txt", limit=config.max_request_bytes) != (
        len(request),
        request_hash,
    ):
        raise ValueError("Native request changed during execution")
    entry = (str(staged / package.entry) + "\n").encode("utf-8")
    if job_files.file_identity(job / "package-entry.txt", limit=len(entry)) != (
        len(entry),
        sha256(entry).hexdigest(),
    ):
        raise ValueError("Native package entry changed during execution")
    if face_request is not None and job_files.file_identity(
        job / "face-request.txt", limit=96 * 1024**2
    ) != (len(face_request), sha256(face_request).hexdigest()):
        raise ValueError("Captured face input changed during query")
    for name in ("native.json.tmp", "lifecycle.json.tmp"):
        try:
            (job / name).lstat()
        except FileNotFoundError:
            continue
        raise ValueError("Native result was not atomically published")

    def read(name: str, limit: int) -> bytes:
        with job_files.open_regular(job_files.package_file(job, name)) as stream:
            result = stream.read(limit + 1)
        if len(result) > limit:
            raise ValueError("Completed native result exceeds its byte budget")
        return result

    reply_bytes = read("native.json", config.max_reply_bytes)
    decoded = decode_reply(reply_bytes, query, request_bytes=request)
    if (
        decoded.plugin_version != config.plugin_version
        or decoded.database_modified_flags != 0
    ):
        raise ValueError("Native reply worker or unmodified-database identity mismatch")
    lifecycle = json.loads(read("lifecycle.json", MAX_LIFECYCLE_RECEIPT_BYTES))
    if not matches_lifecycle_receipt(
        lifecycle,
        request_sha256=request_hash,
        source_sha256=query.source_sha256,
        reply_sha256=sha256(reply_bytes).hexdigest(),
    ):
        raise ValueError("Native lifecycle receipt does not match the completed query")
    check()


def run_native_query(
    package: NativeInputPackage,
    query: NativeObjectQuery,
    config: NativeQueryProcessConfig,
    *,
    cancelled: Callable[[], bool] | None = None,
    face_request: bytes | None = None,
) -> NativeQueryProcessResult:
    """Возвращать успех только при exit 0, квитанции очистки и неизменном комплекте.

    Ответ при exit 254 остаётся только в receipt.diagnostic_reply. Каталог задания
    новый и приватный; эта функция ничего не удаляет.
    """
    started = time.monotonic()
    job = staged = bundle_copy = None
    child = None
    command: tuple[str, ...] = ()
    log = diagnostic = b""
    signals: tuple[str, ...] = ()
    failure = decoded = request_hash = None
    request_bytes = b""
    source_before = source_after = staged_before = staged_after = truncated = False
    bootstrap_identity = None
    lifecycle_verified = False
    cleanup: CompletedJobCleanup | None = None
    diagnostics: list[str] = []
    source_root = package.root

    def check() -> None:
        _checkpoint(started, config, cancelled)

    try:
        check()
        if os.name not in {"posix", "nt"}:
            raise ValueError("Unsupported Core execution platform")
        if _windows_platform():
            for input_directory in (
                package.root,
                config.worker_bundle,
                config.job_root,
            ):
                job_files.validate_directory(input_directory)
            source_root = package.root.absolute()
            bundle = config.worker_bundle.absolute()
            root = config.job_root.absolute()
        else:
            source_root = package.root.resolve(strict=True)
            bundle = config.worker_bundle.resolve(strict=True)
            root = config.job_root.resolve(strict=True)
        if (
            not root.is_dir()
            or root.is_relative_to(source_root)
            or root.is_relative_to(bundle)
        ):
            raise ValueError(
                "Job root must be outside the input package and worker bundle"
            )
        if not config.core_executable.is_file() or not os.access(
            config.core_executable, os.X_OK
        ):
            raise ValueError("Explicit Core executable is unavailable")
        total = sum(item.size_bytes for item in package.files)
        if total > config.max_package_bytes:
            raise ValueError("CAD package exceeds staging budget")
        if shutil.disk_usage(root).free < total + 256 * 1024**2 + config.min_free_bytes:
            raise ValueError("Insufficient free space for the independent job")
        entry = next(item for item in package.files if item.path == package.entry)
        if Path(entry.path).suffix.lower() != ".dwg":
            raise ValueError("Native package worker requires a captured DWG database")
        if entry.sha256 != query.source_sha256:
            raise ValueError("Query source SHA differs from package entry")
        job_files.verify_package(package, source_root, check)
        source_before = True
        job_files.verify_worker(bundle, config)
        bootstrap_identity = job_files.file_identity(
            config.bootstrap_template, check, 32 * 1024**2
        )
        job = Path(tempfile.mkdtemp(prefix="ga-native-query-", dir=root))
        from app.projects.resource_scope import register_project_resource

        kind = (
            "cad-preparation-job"
            if config.job_root.name.endswith(".cad-preparation-jobs")
            else "cad-query-job"
        )
        register_project_resource(kind, job.name, root=config.job_root)
        job_files.copy_independent(
            config.bootstrap_template,
            job / "Bootstrap.dwg",
            check,
            bootstrap_identity[0],
        )
        if job_files.file_identity(job / "Bootstrap.dwg", check) != bootstrap_identity:
            raise ValueError("Bootstrap template changed during staging")
        staged = job / "package"
        for item in package.files:
            job_files.copy_independent(
                job_files.package_file(source_root, item.path),
                staged / item.path,
                check,
                item.size_bytes,
            )
        job_files.verify_package(package, staged, check)
        staged_before = True
        bundle_copy = job_files.worker_destination(job)
        job_files.stage_worker(bundle, bundle_copy, check)
        job_files.verify_worker(bundle_copy, config)
        profile = job / "profile"
        profile.mkdir()
        request_bytes = encode_request(query, job / "native.json")
        if len(request_bytes) > config.max_request_bytes:
            raise ValueError("Native request exceeds process budget")
        request_hash = sha256(request_bytes).hexdigest()
        (job / "request.txt").write_bytes(request_bytes)
        if face_request is not None:
            if len(face_request) > 96 * 1024**2:
                raise ValueError("Captured face request exceeds process budget")
            (job / "face-request.txt").write_bytes(face_request)
        (job / "package-entry.txt").write_text(
            str(staged / package.entry) + "\n", encoding="utf-8"
        )
        script = _write_script(job, "query.scr", CoreCommand.QUERY)
        command = _command(config, job, script, profile, "ga-query-" + uuid4().hex)
        job_files.verify_package(package, source_root, check)
        check()
        try:
            child = _spawn(command, job)
        except OSError as error:
            raise _Stop("launch_failed", str(error)) from error
        cleanup = CompletedJobCleanup(
            lambda: _validate_completed_query(
                package,
                source_root,
                staged,
                bundle_copy,
                job,
                query,
                request_bytes,
                bootstrap_identity,
                face_request,
                config,
                check,
            )
        )
        failure, log, signals = _supervise(
            child,
            job,
            started,
            config,
            cancelled,
            ("native.json", "native.json.tmp", "faces.json", "faces.json.tmp"),
            completed_cleanup=cleanup,
        )
        if failure is None and child.returncode != 0:
            failure = NativeProcessFailure(
                "process_exit", f"Core exited with {child.returncode}"
            )
        if failure is None and signals and not cleanup.accepts_signals(signals):
            failure = NativeProcessFailure(
                "process_group_alive",
                "Core termination lacks verified completion cleanup",
            )
    except _Stop as error:
        failure = error.failure
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        failure = NativeProcessFailure(
            "io_failure" if child else "invalid_input", str(error)[:1024]
        )
    finally:
        if cleanup is not None and cleanup.signals:
            signals = cleanup.signals
        if child is not None and child.poll() is None:
            stopped = list(signals)
            try:
                _stop_group(child, config.terminate_grace_seconds, stopped)
            except (OSError, subprocess.SubprocessError) as error:
                failure = NativeProcessFailure(
                    "io_failure", "Could not stop owned process group"
                )
                diagnostics.append(str(error)[:1024])
            signals = tuple(stopped)
        for directory, label in ((source_root, "source"), (staged, "staged")):
            if directory is None:
                continue
            try:
                job_files.verify_package(package, directory)
                if label == "source":
                    source_after = True
                else:
                    staged_after = True
            except (OSError, ValueError) as error:
                diagnostics.append(f"{label}: {error}"[:1024])
        if source_before and (not source_after or (staged_before and not staged_after)):
            if failure:
                diagnostics.append(failure.message)
            failure = NativeProcessFailure(
                "integrity_failure", "CAD package changed during job"
            )
    if job is not None and (child is None or child.poll() is not None):
        try:
            diagnostic, truncated = _diagnostic(job, config.max_reply_bytes)
            if truncated:
                failure = failure or NativeProcessFailure(
                    "output_limit", "Native output was truncated"
                )
            if (
                request_hash is not None
                and job_files.file_identity(
                    job / "request.txt", limit=config.max_request_bytes
                )[1]
                != request_hash
            ):
                raise ValueError("Native request changed during execution")
            if bundle_copy is not None and staged_before:
                job_files.verify_worker(bundle_copy, config)
            if face_request is not None and job_files.file_identity(
                job / "face-request.txt", limit=96 * 1024**2
            ) != (len(face_request), sha256(face_request).hexdigest()):
                raise ValueError("Captured face input changed during query")
            if diagnostic and not truncated:
                decoded = decode_reply(diagnostic, query, request_bytes=request_bytes)
                if (
                    decoded.plugin_version != config.plugin_version
                    or decoded.database_modified_flags != 0
                ):
                    decoded = None
                    raise ValueError(
                        "Native reply worker version or unmodified-database identity mismatch"
                    )
                if failure is None and (
                    not (job / "native.json").is_file()
                    or (job / "native.json.tmp").exists()
                ):
                    raise ValueError("Native reply was not atomically published")
            elif failure is None:
                raise ValueError("Native worker produced no reply")
            if failure is None:
                if job_files.file_identity(job / "Bootstrap.dwg") != bootstrap_identity:
                    raise ValueError("Bootstrap database was saved or changed")
                path = job_files.package_file(job, "lifecycle.json")
                if (
                    path.stat().st_size > MAX_LIFECYCLE_RECEIPT_BYTES
                    or (job / "lifecycle.json.tmp").exists()
                ):
                    raise ValueError("Invalid lifecycle receipt publication")
                with job_files.open_regular(path) as stream:
                    lifecycle = json.load(stream)
                if request_hash is None or not matches_lifecycle_receipt(
                    lifecycle,
                    request_sha256=request_hash,
                    source_sha256=query.source_sha256,
                    reply_sha256=sha256(diagnostic).hexdigest(),
                ):
                    raise ValueError(
                        "Native lifecycle receipt does not match the completed query"
                    )
                lifecycle_verified = True
        except (OSError, ValueError) as error:
            diagnostics.append(str(error)[:1024])
            failure = failure or NativeProcessFailure(
                "invalid_reply", str(error)[:1024]
            )
    receipt = NativeProcessReceipt(
        job,
        command,
        child.returncode if child else None,
        time.monotonic() - started,
        package.sha256,
        source_before,
        source_after,
        staged_before,
        staged_after,
        request_hash,
        config.worker_binary_sha256,
        signals,
        log,
        diagnostic,
        truncated,
        decoded,
        tuple(diagnostics),
        lifecycle_verified=lifecycle_verified,
        cleanup_attempted=cleanup.attempted if cleanup is not None else False,
        cleanup_confirmed=cleanup.confirmed if cleanup is not None else False,
        tree_empty=cleanup.tree_empty if cleanup is not None else None,
    )
    return NativeQueryProcessResult(
        receipt, decoded if failure is None else None, failure
    )
