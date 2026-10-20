"""Private relay overrides and an explicitly provisioned frozen release default.

The release default is used only when no personal or environment relay was
selected. An invalid explicit account must never silently switch to that default.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.layer_recognition.api_settings import settings_directory
from app.layer_recognition.budget import RecognitionUnavailable
from app.shared.failures import FailureReason

MAX_SETTINGS_BYTES = 8192
MAX_URL_LENGTH = 2048
MAX_TOKEN_LENGTH = 512
MIN_RELEASE_TOKEN_LENGTH = 32


def _invalid() -> RecognitionUnavailable:
    return RecognitionUnavailable(FailureReason.RECOGNITION_RELAY_SETTINGS_INVALID)


def _unreadable() -> RecognitionUnavailable:
    return RecognitionUnavailable(FailureReason.RECOGNITION_RELAY_SETTINGS_UNREADABLE)


@dataclass(frozen=True)
class RelaySettings:
    url: str
    token: str = field(repr=False)
    bundled: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.url, str)
            or not 1 <= len(self.url) <= MAX_URL_LENGTH
            or any(not 33 <= ord(char) <= 126 for char in self.url)
            or not isinstance(self.token, str)
            or not 1 <= len(self.token) <= MAX_TOKEN_LENGTH
            or any(not 33 <= ord(char) <= 126 for char in self.token)
        ):
            raise _invalid()
        try:
            parsed = urlsplit(self.url)
            valid = (
                parsed.scheme == "https"
                and parsed.hostname
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
                and parsed.path == "/api/luna/recognize"
                and (parsed.port is None or 1 <= parsed.port <= 65535)
            )
        except ValueError:
            valid = False
        if not valid:
            raise _invalid() from None


def bundled_relay_settings_path() -> Path | None:
    root = getattr(sys, "_MEIPASS", None)
    if not getattr(sys, "frozen", False) or not isinstance(root, str):
        return None
    return Path(root) / "Services/relay.json"


def _read(path: Path, *, private: bool) -> dict[str, object] | None:
    """Read a bounded regular inode without creating any personal settings."""
    try:
        path = path.absolute()
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise _unreadable()
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except FileNotFoundError:
        return None
    except OSError:
        raise _unreadable() from None
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
                raise _unreadable()
            raw = stream.read(MAX_SETTINGS_BYTES + 1)
        if len(raw) > MAX_SETTINGS_BYTES:
            raise _unreadable()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise _unreadable()
        return data
    except (OSError, ValueError, UnicodeError):
        raise _unreadable() from None


def _settings(data: dict[str, object], *, release: bool = False) -> RelaySettings:
    url, token = data.get("url"), data.get("token")
    if not isinstance(url, str) or not isinstance(token, str):
        raise _invalid()
    if release and len(token) < MIN_RELEASE_TOKEN_LENGTH:
        raise _invalid()
    return RelaySettings(url=url, token=token, bundled=release)


def load_relay_settings() -> RelaySettings | None:
    personal = _read(settings_directory() / "relay.json", private=True)
    names = ("GREEN_ATLAS_LUNA_RELAY_URL", "GREEN_ATLAS_LUNA_RELAY_TOKEN")
    if any(name in os.environ for name in names):
        data = dict(personal or {})
        for name, key in zip(names, ("url", "token"), strict=True):
            if name in os.environ:
                data[key] = os.environ[name]
        return _settings(data)
    if personal is not None:
        return _settings(personal)
    bundled_path = bundled_relay_settings_path()
    bundled = _read(bundled_path, private=False) if bundled_path else None
    return _settings(bundled, release=True) if bundled is not None else None
