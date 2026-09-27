"""Local worker inputs/receipts, NOT a GAOPEN capture or XREF-completeness claim."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import Literal

from app.native_query.contracts import NativeObjectReply


def relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        value in {"", "."} or path.is_absolute() or path.as_posix() != value
        or any(part in {".", ".."} for part in path.parts)
        or any(ord(char) < 32 or char in "\\:" for char in value)
    ):
        raise ValueError("Expected a canonical relative package path")
    return value


def valid_sha(value: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("Expected a lowercase SHA-256")


@dataclass(frozen=True)
class CadPackageFile:
    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        relative_path(self.path)
        valid_sha(self.sha256)
        if PurePosixPath(self.path).suffix.lower() not in {".dwg", ".dxf"}:
            raise ValueError("Package catalogue accepts only DWG/DXF files")
        if not isinstance(self.size_bytes, int) or self.size_bytes <= 0:
            raise ValueError("Expected a positive CAD file size")


@dataclass(frozen=True)
class NativeInputPackage:
    root: Path
    entry: str
    files: tuple[CadPackageFile, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root))
        object.__setattr__(self, "files", tuple(self.files))
        if not self.root.is_absolute():
            raise ValueError("Package root must be explicit and absolute")
        relative_path(self.entry)
        if not 1 <= len(self.files) <= 4096:
            raise ValueError("Package catalogue size is invalid")
        names = [unicodedata.normalize("NFC", item.path).casefold() for item in self.files]
        if len(set(names)) != len(names) or self.entry not in {f.path for f in self.files}:
            raise ValueError("Ambiguous package catalogue or missing entry")

    @property
    def sha256(self) -> str:
        """Digest of the declared catalogue; run_native_query verifies its bytes."""
        data = {"entry": self.entry, "files": [
            [f.path, f.size_bytes, f.sha256] for f in sorted(self.files, key=lambda f: f.path)
        ]}
        return sha256(json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class NativeQueryProcessConfig:
    core_executable: Path
    worker_bundle: Path
    job_root: Path
    worker_binary_sha256: str
    plugin_version: str
    bootstrap_template: Path
    architecture: Literal["native", "x86_64", "arm64"] = "native"
    timeout_seconds: float = 180
    terminate_grace_seconds: float = 2
    poll_seconds: float = 0.05
    max_package_bytes: int = 8 * 1024**3
    max_reply_bytes: int = 96 * 1024**2
    max_log_bytes: int = 4 * 1024**2
    max_request_bytes: int = 4 * 1024**2
    min_free_bytes: int = 64 * 1024**2

    def __post_init__(self) -> None:
        for field in ("core_executable", "worker_bundle", "job_root", "bootstrap_template"):
            path = Path(getattr(self, field))
            object.__setattr__(self, field, path)
            if not path.is_absolute() or any(ord(c) < 32 for c in str(path)):
                raise ValueError("Worker paths must be explicit, absolute and single-line")
        valid_sha(self.worker_binary_sha256)
        if not self.plugin_version or self.architecture not in {"native", "x86_64", "arm64"}:
            raise ValueError("Invalid worker identity/architecture")
        budgets = (self.timeout_seconds, self.terminate_grace_seconds, self.poll_seconds,
                   self.max_package_bytes, self.max_reply_bytes, self.max_log_bytes,
                   self.max_request_bytes, self.min_free_bytes)
        if any(not math.isfinite(value) or value <= 0 for value in budgets):
            raise ValueError("Worker budgets must be finite and positive")
        if any(type(value) is not int for value in budgets[3:]):
            raise ValueError("Byte budgets must be integers")
        if self.max_reply_bytes > 96 * 1024**2 or self.max_request_bytes > 4 * 1024**2:
            raise ValueError("Process budget exceeds native protocol limit")


FailureCode = Literal[
    "invalid_input", "cancelled", "timeout", "launch_failed", "process_exit",
    "process_group_alive", "output_limit", "log_limit", "invalid_reply",
    "integrity_failure", "io_failure",
]


@dataclass(frozen=True)
class NativeProcessFailure:
    code: FailureCode
    message: str


@dataclass(frozen=True)
class NativeProcessReceipt:
    job_directory: Path | None
    command: tuple[str, ...]
    exit_code: int | None
    elapsed_seconds: float
    package_sha256: str
    source_verified_before: bool
    source_verified_after: bool
    staged_verified_before: bool
    staged_verified_after: bool
    request_sha256: str | None
    worker_binary_sha256: str
    signals: tuple[str, ...]
    log: bytes
    diagnostic_output: bytes
    diagnostic_truncated: bool
    diagnostic_reply: NativeObjectReply | None
    diagnostics: tuple[str, ...]
    lifecycle_verified: bool = False
    scope: str = "selected_instance_measurements_only; not a capture receipt"


@dataclass(frozen=True)
class NativeQueryProcessResult:
    receipt: NativeProcessReceipt
    reply: NativeObjectReply | None = None
    failure: NativeProcessFailure | None = None
