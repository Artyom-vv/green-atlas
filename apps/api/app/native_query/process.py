"""POSIX Core supervisor for an explicitly inventoried package, not a capture.

No CAD parsing, reference discovery/repath, project metadata or fallback. The
caller supplies a curated worker binary hash and a package whose CAD reference
resolution is established elsewhere. Jobs retain independent copies and bounded
diagnostics under job_root; their lifecycle belongs to the caller. User inputs
are never removed, linked into jobs, saved, or chmod'ed.
"""

from __future__ import annotations

import json
import os
import plistlib
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Callable
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from app.native_query.contracts import NativeObjectQuery
from app.native_query.process_contracts import (
    FailureCode,
    NativeInputPackage,
    NativeProcessFailure,
    NativeProcessReceipt,
    NativeQueryProcessConfig,
    NativeQueryProcessResult,
)
from app.native_query.protocol import decode_reply, encode_request

WORKER_ID = "ru.green-atlas.native-query-worker"
WORKER_BINARY = "Contents/MacOS/GreenAtlasBridge"


class _Stop(Exception):
    def __init__(self, code: FailureCode, message: str):
        self.failure = NativeProcessFailure(code, message)
        super().__init__(message)


def _checkpoint(started: float, config: NativeQueryProcessConfig,
                cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise _Stop("cancelled", "Native query cancelled")
    if time.monotonic() - started >= config.timeout_seconds:
        raise _Stop("timeout", "Native query exceeded its time budget")


def _file(root: Path, relative: str) -> Path:
    path = root
    for part in Path(relative).parts:
        path = path / part
        if path.is_symlink():
            raise ValueError(f"Symlink is not a package file: {relative}")
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"Not a regular file: {relative}")
    return path


def _open(path: Path, *, writable: bool = False):
    flags = (os.O_RDWR if writable else os.O_RDONLY) | os.O_NOFOLLOW | os.O_NONBLOCK
    fd = os.open(path, flags)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or (writable and info.st_nlink != 1):
        os.close(fd)
        raise ValueError("Expected an independent regular file")
    return os.fdopen(fd, "r+b" if writable else "rb")


def _digest(path: Path, check: Callable[[], None] = lambda: None,
            limit: int = 256 * 1024**2) -> tuple[int, str]:
    digest, size = sha256(), 0
    with _open(path) as stream:
        if os.fstat(stream.fileno()).st_size > limit:
            raise ValueError("File exceeds its declared byte budget")
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                raise ValueError("File grew beyond its declared byte budget")
            digest.update(chunk)
            check()
    return size, digest.hexdigest()


def _verify(package: NativeInputPackage, root: Path,
            check: Callable[[], None] = lambda: None) -> None:
    for item in package.files:
        check()
        if _digest(_file(root, item.path), check, item.size_bytes) != (item.size_bytes, item.sha256):
            raise ValueError(f"Package file changed: {item.path}")


def _copy(source: Path, destination: Path, check: Callable[[], None], limit: int) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with _open(source) as reader, destination.open("xb") as writer:
        size = 0
        while chunk := reader.read(1024 * 1024):
            check()
            size += len(chunk)
            if size > limit:
                raise ValueError("Staged file grew beyond its declared byte budget")
            writer.write(chunk)
    # A byte copy creates an independent inode, even if the source has links.
    if destination.stat().st_nlink != 1 or os.path.samestat(source.stat(), destination.stat()):
        raise ValueError("Worker staging did not produce an independent copy")


def _worker(bundle: Path, config: NativeQueryProcessConfig) -> None:
    info_path = _file(bundle, "Contents/Info.plist")
    if info_path.stat().st_size > 64 * 1024:
        raise ValueError("Worker metadata exceeds limit")
    with _open(info_path) as stream:
        info = plistlib.load(stream)
    if (not isinstance(info, dict) or info.get("CFBundleIdentifier") != WORKER_ID
            or info.get("CFBundleExecutable") != "GreenAtlasBridge"
            or info.get("CFBundleShortVersionString") != config.plugin_version):
        raise ValueError("Expected the curated query worker, not the GUI plugin")
    if _digest(_file(bundle, WORKER_BINARY))[1] != config.worker_binary_sha256:
        raise ValueError("Curated worker binary SHA mismatch")


def _stage_worker(bundle: Path, destination: Path, check: Callable[[], None]) -> None:
    total = 0
    for index, source in enumerate(bundle.rglob("*")):
        check()
        if index >= 1024 or source.is_symlink():
            raise ValueError("Worker bundle has too many files or a symlink")
        if source.is_dir():
            continue
        source = _file(bundle, source.relative_to(bundle).as_posix())
        total += source.stat().st_size
        if total > 256 * 1024**2:
            raise ValueError("Worker bundle exceeds staging budget")
        target = destination / source.relative_to(bundle)
        _copy(source, target, check, source.stat().st_size)
        target.chmod(stat.S_IMODE(source.stat().st_mode) & 0o777)
        if _digest(source, check) != _digest(target, check):
            raise ValueError("Worker bundle changed during staging")


def _group_alive(pid: int) -> bool:
    try:
        os.killpg(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS reports EPERM while a signalled group leader is exiting.
        # Treat it as still present until reaped, never as a successful stop.
        return True


def _stop_group(child: subprocess.Popen, grace: float, signals: list[str]) -> None:
    # This PGID is exclusively the session created by our Popen. Never search
    # by application name or signal the parent/API/interactive AutoCAD group.
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


def _output_size(job: Path) -> int:
    total = 0
    for name in ("native.json", "native.json.tmp"):
        path = job / name
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise _Stop("invalid_reply", "Native output is not an independent file")
        total += info.st_size
    return total


def _supervise(child: subprocess.Popen, job: Path, started: float,
               config: NativeQueryProcessConfig, cancelled: Callable[[], bool] | None):
    log, signals = bytearray(), []
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
                if _output_size(job) > config.max_reply_bytes:
                    raise _Stop("output_limit", "Native output exceeded its byte budget")
                if child.poll() is not None:
                    if _group_alive(child.pid):
                        raise _Stop("process_group_alive", "Core left a live process group")
                    break
        except _Stop as error:
            failure = error.failure
        finally:
            try:
                _stop_group(child, config.terminate_grace_seconds, signals)
                # Drain the finite pipe backlog after stopping the owned group.
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
    """Retain bounded output, including an unfinished native atomic-write file."""
    data, truncated, remaining = b"", False, limit
    for name in ("native.json", "native.json.tmp"):
        path = job / name
        if not path.exists() and not path.is_symlink():
            continue
        # Only our own independent output may be truncated, after group stop.
        with _open(path, writable=True) as stream:
            size = os.fstat(stream.fileno()).st_size
            chunk = stream.read(remaining)
            if size > remaining:
                stream.truncate(remaining)
                truncated = True
            remaining -= len(chunk)
            if not data:
                data = chunk
    return data, truncated


def run_native_query(
    package: NativeInputPackage,
    query: NativeObjectQuery,
    config: NativeQueryProcessConfig,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> NativeQueryProcessResult:
    """Return success only for exit 0 + cleanup receipt + unchanged package.

    A valid answer from exit 254 is retained solely in receipt.diagnostic_reply.
    The retained job directory is new and private; this function deletes nothing.
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
    diagnostics: list[str] = []
    source_root = package.root
    def check() -> None:
        _checkpoint(started, config, cancelled)

    try:
        check()
        if os.name != "posix":
            raise ValueError("This Core supervisor requires POSIX process groups")
        source_root = package.root.resolve(strict=True)
        bundle = config.worker_bundle.resolve(strict=True)
        root = config.job_root.resolve(strict=True)
        if not root.is_dir() or root.is_relative_to(source_root) or root.is_relative_to(bundle):
            raise ValueError("Job root must be outside the input package and worker bundle")
        if not config.core_executable.is_file() or not os.access(config.core_executable, os.X_OK):
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
        _verify(package, source_root, check)
        source_before = True
        _worker(bundle, config)
        bootstrap_identity = _digest(config.bootstrap_template, check, 32 * 1024**2)
        job = Path(tempfile.mkdtemp(prefix="ga-native-query-", dir=root))
        _copy(config.bootstrap_template, job / "Bootstrap.dwg", check, bootstrap_identity[0])
        if _digest(job / "Bootstrap.dwg", check) != bootstrap_identity:
            raise ValueError("Bootstrap template changed during staging")
        staged = job / "package"
        for item in package.files:
            _copy(_file(source_root, item.path), staged / item.path, check, item.size_bytes)
        _verify(package, staged, check)
        staged_before = True
        # Qualified Core runner loads the worker-only bundle under .dbx.
        bundle_copy = job / "QueryWorker.dbx"
        _stage_worker(bundle, bundle_copy, check)
        _worker(bundle_copy, config)
        profile = job / "profile"
        profile.mkdir()
        request_bytes = encode_request(query, job / "native.json")
        if len(request_bytes) > config.max_request_bytes:
            raise ValueError("Native request exceeds process budget")
        request_hash = sha256(request_bytes).hexdigest()
        (job / "request.txt").write_bytes(request_bytes)
        (job / "package-entry.txt").write_text(str(staged / package.entry) + "\n", encoding="utf-8")
        (job / "query.scr").write_text(
            '(setvar "TRUSTEDPATHS" (getvar "DWGPREFIX"))\n'
            '(setvar "FILEDIA" 0)\n'
            '(arxload (strcat (getvar "DWGPREFIX") "QueryWorker.dbx"))\nGAQUERYPACKAGE\n'
            '_QUIT\n_Y\n\n', encoding="utf-8",
        )
        prefix = () if config.architecture == "native" else ("/usr/bin/arch", "-" + config.architecture)
        command = (*prefix, str(config.core_executable), "/i", str(job / "Bootstrap.dwg"),
                   "/s", str(job / "query.scr"), "/isolate", "ga-query-" + uuid4().hex, str(profile))
        _verify(package, source_root, check)
        check()
        try:
            child = subprocess.Popen(command, cwd=job, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     shell=False, start_new_session=True)
        except OSError as error:
            raise _Stop("launch_failed", str(error)) from error
        failure, log, signals = _supervise(child, job, started, config, cancelled)
        if failure is None and child.returncode != 0:
            failure = NativeProcessFailure("process_exit", f"Core exited with {child.returncode}")
    except _Stop as error:
        failure = error.failure
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        failure = NativeProcessFailure("io_failure" if child else "invalid_input", str(error)[:1024])
    finally:
        if child is not None and child.poll() is None:
            stopped = list(signals)
            try:
                _stop_group(child, config.terminate_grace_seconds, stopped)
            except (OSError, subprocess.SubprocessError) as error:
                failure = NativeProcessFailure("io_failure", "Could not stop owned process group")
                diagnostics.append(str(error)[:1024])
            signals = tuple(stopped)
        for directory, label in ((source_root, "source"), (staged, "staged")):
            if directory is None:
                continue
            try:
                _verify(package, directory)
                if label == "source":
                    source_after = True
                else:
                    staged_after = True
            except (OSError, ValueError) as error:
                diagnostics.append(f"{label}: {error}"[:1024])
        if source_before and (not source_after or (staged_before and not staged_after)):
            if failure:
                diagnostics.append(failure.message)
            failure = NativeProcessFailure("integrity_failure", "CAD package changed during job")
    if job is not None and (child is None or child.poll() is not None):
        try:
            diagnostic, truncated = _diagnostic(job, config.max_reply_bytes)
            if truncated:
                failure = failure or NativeProcessFailure("output_limit", "Native output was truncated")
            if request_hash is not None and _digest(
                job / "request.txt", limit=config.max_request_bytes
            )[1] != request_hash:
                raise ValueError("Native request changed during execution")
            if bundle_copy is not None and staged_before:
                _worker(bundle_copy, config)
            if diagnostic and not truncated:
                decoded = decode_reply(diagnostic, query, request_bytes=request_bytes)
                if (decoded.plugin_version != config.plugin_version
                        or decoded.database_modified_flags != 0):
                    decoded = None
                    raise ValueError("Native reply worker version or unmodified-database identity mismatch")
                if failure is None and (
                    not (job / "native.json").is_file() or (job / "native.json.tmp").exists()
                ):
                    raise ValueError("Native reply was not atomically published")
            elif failure is None:
                raise ValueError("Native worker produced no reply")
            if failure is None:
                if _digest(job / "Bootstrap.dwg") != bootstrap_identity:
                    raise ValueError("Bootstrap database was saved or changed")
                path = _file(job, "lifecycle.json")
                if path.stat().st_size > 4096 or (job / "lifecycle.json.tmp").exists():
                    raise ValueError("Invalid lifecycle receipt publication")
                with _open(path) as stream:
                    lifecycle = json.load(stream)
                expected = {
                    "schema": "green-atlas.native-query-lifecycle/1",
                    "request_sha256": request_hash, "source_sha256": query.source_sha256,
                    "reply_sha256": sha256(diagnostic).hexdigest(),
                    "database_destroyed": True, "working_database_restored": True,
                    "field_evaluation_restored": True,
                }
                if (lifecycle != expected or any(type(lifecycle.get(key)) is not bool for key in (
                    "database_destroyed", "working_database_restored", "field_evaluation_restored",
                ))):
                    raise ValueError("Native lifecycle receipt does not match the completed query")
                lifecycle_verified = True
        except (OSError, ValueError) as error:
            diagnostics.append(str(error)[:1024])
            failure = failure or NativeProcessFailure("invalid_reply", str(error)[:1024])
    receipt = NativeProcessReceipt(
        job, command, child.returncode if child else None, time.monotonic() - started,
        package.sha256, source_before, source_after, staged_before, staged_after,
        request_hash, config.worker_binary_sha256, signals, log, diagnostic, truncated,
        decoded, tuple(diagnostics), lifecycle_verified=lifecycle_verified,
    )
    return NativeQueryProcessResult(receipt, decoded if failure is None else None, failure)
