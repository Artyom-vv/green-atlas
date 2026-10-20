"""Private per-user address-service settings and an optional frozen default.

Credentials never belong to a project or a public settings response. A personal
key deliberately overrides both the environment and the release default; an
upstream refusal must not silently change the account used for a request.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkstemp
from typing import Literal

from app.location.contracts import LocationServiceSettings

MAX_SETTINGS_BYTES = 8192
MAX_KEY_LENGTH = 512
SettingsSource = Literal["personal", "environment", "bundled", "none"]


class LocationProviderError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def dadata_settings_path() -> Path:
    override = os.environ.get("GREEN_ATLAS_LOCATION_SETTINGS")
    if override:
        return Path(override).expanduser()
    if sys.platform == "darwin":
        root = Path.home() / "Library/Application Support/Green Atlas/Location"
    elif sys.platform == "win32":
        root = (
            Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
            / "Green Atlas/Location"
        )
    else:
        root = Path.home() / ".local/share/green-atlas/location"
    return root / "dadata.json"


def bundled_dadata_settings_path() -> Path | None:
    root = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not isinstance(root, str):
        return None
    return Path(root) / "Services/dadata.json"


def _error() -> LocationProviderError:
    return LocationProviderError(
        "settings_unreadable",
        "Проверьте формат и права доступа к настройкам DaData",
    )


def _direct_path(path: Path) -> Path:
    path = path.absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise _error()
    return path


def _key(value: object) -> str:
    if not isinstance(value, str):
        raise _error()
    token = value.strip()
    if not 1 <= len(token) <= MAX_KEY_LENGTH or any(
        not 33 <= ord(char) <= 126 for char in token
    ):
        raise _error()
    return token


def _read(path: Path, *, private: bool) -> str | None:
    """Read one bounded regular inode, never a link or a special device."""
    try:
        path = _direct_path(path)
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        return None
    except OSError:
        raise _error() from None
    try:
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_size > MAX_SETTINGS_BYTES
                or (
                    private
                    and os.name != "nt"
                    and (info.st_mode & 0o077 or info.st_uid != os.getuid())
                )
                or (private and info.st_nlink != 1)
            ):
                raise _error()
            raw = stream.read(MAX_SETTINGS_BYTES + 1)
        if len(raw) > MAX_SETTINGS_BYTES:
            raise _error()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise _error()
        value = data.get("api_key")
        return _key(value) if value is not None else None
    except (OSError, ValueError, UnicodeError):
        raise _error() from None


@dataclass(frozen=True)
class _Settings:
    key: str | None = field(repr=False)
    source: SettingsSource
    personal: bool
    bundled: bool

    def public(self) -> LocationServiceSettings:
        return LocationServiceSettings(
            configured=self.key is not None,
            source=self.source,
            personal_key_configured=self.personal,
            bundled_key_configured=self.bundled,
        )


def _resolved_settings(personal: str | None) -> _Settings:
    environment = os.environ.get("DADATA_API_KEY")
    selected = personal
    source: SettingsSource = "personal" if personal else "none"
    if not selected and environment and environment.strip():
        selected, source = _key(environment), "environment"
    bundled_path = bundled_dadata_settings_path()
    try:
        bundled = _read(bundled_path, private=False) if bundled_path else None
    except LocationProviderError:
        if selected is None:
            raise
        bundled = None
    if selected is None and bundled:
        selected, source = bundled, "bundled"
    return _Settings(selected, source, bool(personal), bool(bundled))


def _settings() -> _Settings:
    personal = _read(dadata_settings_path(), private=True)
    return _resolved_settings(personal)


def load_dadata_token() -> str | None:
    return _settings().key


def get_location_service_settings() -> LocationServiceSettings:
    return _settings().public()


def _directory(path: Path) -> Path:
    """Create only missing private directories; do not chmod existing ancestors."""
    path = _direct_path(path)
    missing: list[Path] = []
    current = path.parent
    while not current.exists():
        missing.append(current)
        current = current.parent
    if not current.is_dir():
        raise _error()
    for parent in reversed(missing):
        parent.mkdir(mode=0o700)
    path = _direct_path(path)
    if not path.parent.is_dir():
        raise _error()
    return path


def _sync_directory(path: Path) -> None:
    # Windows does not provide the POSIX directory-fsync operation.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def save_dadata_token(token: str) -> LocationServiceSettings:
    """Commit a private, fsynced replacement; failed staging leaves the old file."""
    temporary: Path | None = None
    try:
        token = _key(token)
        path = _directory(dadata_settings_path())
        # Validate the current inode before replacing it; an unreadable personal
        # setting must be fixed explicitly rather than overwritten through a link.
        _read(path, private=True)
        descriptor, name = mkstemp(prefix=".dadata-", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(json.dumps({"api_key": token}).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(path.parent)
        _direct_path(path)
        os.replace(temporary, path)
        temporary = None
        # The replacement is the commit point. A directory-sync failure after
        # it cannot safely be described as a failed update of the old setting.
        try:
            _sync_directory(path.parent)
        except OSError:
            pass
        return get_location_service_settings()
    except (OSError, ValueError):
        raise _error() from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def reset_dadata_token() -> LocationServiceSettings:
    try:
        path = _direct_path(dadata_settings_path())
        _read(path, private=True)
        result = _resolved_settings(None).public()
        # Check directory fsync support before the unlink commit point.
        if path.parent.is_dir():
            _sync_directory(path.parent)
        path.unlink(missing_ok=True)
        if path.parent.is_dir():
            try:
                _sync_directory(path.parent)
            except OSError:
                pass
        return result
    except (OSError, ValueError):
        raise _error() from None
