"""Отдельный отзываемый доступ выпуска к тому же relay и журналу расходов."""

from __future__ import annotations

import hmac
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path

_MIN_RELEASE_TOKEN_BYTES = 32
_MAX_RELEASE_TOKEN_BYTES = 512


@dataclass(frozen=True)
class RelayAccess:
    client_token: str = field(repr=False)
    release_token_file: Path | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        # Совместимость существующего персонального доступа не меняется.
        if len(self.client_token) < 32:
            raise ValueError("Relay token is missing or too short")

    def _release_token(self) -> bytes | None:
        if self.release_token_file is None:
            return None
        try:
            descriptor = os.open(
                self.release_token_file,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0),
            )
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o027:
                    return None
                # Одно завершающее LF разрешает безопасную запись token-файла.
                token = stream.read(_MAX_RELEASE_TOKEN_BYTES + 2).removesuffix(b"\n")
        except OSError:
            return None
        if not _MIN_RELEASE_TOKEN_BYTES <= len(token) <= _MAX_RELEASE_TOKEN_BYTES:
            return None
        if any(value < 33 or value > 126 for value in token):
            return None
        if hmac.compare_digest(token, self.client_token.encode("utf-8")):
            return None
        return token

    def allows(self, authorization: str) -> bool:
        supplied = authorization.encode("utf-8")
        owner_matches = hmac.compare_digest(
            supplied, b"Bearer " + self.client_token.encode("utf-8")
        )
        # Читаем на каждый запрос: атомарная замена или удаление файла отзывает
        # только доступ выпуска без перезапуска и без смены персонального токена.
        release_token = self._release_token()
        release_matches = hmac.compare_digest(
            supplied, b"Bearer " + (release_token or b"")
        )
        return owner_matches | (release_token is not None and release_matches)
