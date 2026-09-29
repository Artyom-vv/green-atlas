"""Per-user secrets live outside projects, source control and application bundles."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from app.layer_recognition.budget import RecognitionUnavailable


def settings_directory() -> Path:
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Green Atlas/AI"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Green Atlas/AI"
    return Path.home() / ".local/share/green-atlas/ai"


@dataclass(frozen=True)
class ApiSettings:
    key: str = field(repr=False)
    proxy: str | None = field(default=None, repr=False)
    ledger: Path = field(
        default_factory=lambda: settings_directory() / "budget.sqlite3"
    )

    def __post_init__(self):
        if not self.key or not isinstance(self.key, str):
            raise RecognitionUnavailable("Не настроен ключ OpenAI")
        if self.proxy:
            try:
                parsed = urlsplit(self.proxy)
                valid = (
                    parsed.scheme in {"socks5", "socks5h"}
                    and parsed.hostname
                    and parsed.port
                )
            except (TypeError, ValueError):
                valid = False
            if not valid:
                raise RecognitionUnavailable(
                    "Неверная настройка SOCKS-прокси распознавания"
                )


def load_api_settings() -> ApiSettings | None:
    path = settings_directory() / "openai.json"
    data = {}
    if path.exists():
        try:
            if os.name != "nt" and path.stat().st_mode & 0o077:
                raise ValueError("permissions")
            data = json.loads(path.read_text())
            if not isinstance(data, dict):
                raise ValueError("format")
        except (OSError, ValueError):
            raise RecognitionUnavailable(
                "Не удалось прочитать защищённую настройку OpenAI"
            ) from None
    key = os.environ.get("OPENAI_API_KEY") or data.get("api_key")
    if not key:
        return None
    return ApiSettings(
        key=key, proxy=os.environ.get("OPENAI_PROXY_URL") or data.get("proxy_url")
    )
