"""Настройка существующего SQLite-соединения и компиляция выражений SQLAlchemy.

Соединение, блокировка и транзакция остаются у вызывающего репозитория.
Параметры PRAGMA проверены отдельно; значения пользователя не встраиваются в SQL.
"""

import sqlite3
from importlib.resources import files
from time import monotonic, sleep
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.dialects.sqlite import dialect
from sqlalchemy.sql import ClauseElement

SQLITE_DIALECT = dialect(paramstyle="named")


class SqliteConnectionConfig(BaseModel):
    """Прежние параметры локального хранилища с явными единицами ожидания."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    busy_timeout_ms: int = Field(ge=0)
    journal_mode: Literal["WAL"]
    synchronous: Literal["NORMAL"]


_CONNECTION_CONFIG = SqliteConnectionConfig.model_validate_json(
    files("app.shared").joinpath("storage-config.json").read_bytes()
)


def configure_sqlite_connection(connection: sqlite3.Connection) -> None:
    """Применить PRAGMA без открытия, закрытия или фиксации чужого соединения."""
    connection.execute(f"PRAGMA busy_timeout={_CONNECTION_CONFIG.busy_timeout_ms}")
    _configure_journal_mode(connection)
    connection.execute(f"PRAGMA synchronous={_CONNECTION_CONFIG.synchronous}")


def _configure_journal_mode(connection: sqlite3.Connection) -> None:
    # При одновременном первом открытии SQLite может отказать в переходе в WAL
    # немедленно, не дожидаясь busy_timeout. Повтор относится только к этому
    # переходу и использует прежнее суммарное время ожидания соединения.
    deadline = monotonic() + _CONNECTION_CONFIG.busy_timeout_ms / 1000
    timeout_changed = False
    try:
        while True:
            try:
                connection.execute(
                    f"PRAGMA journal_mode={_CONNECTION_CONFIG.journal_mode}"
                )
                return
            except sqlite3.OperationalError as error:
                code = getattr(error, "sqlite_errorcode", None)
                if code is None or code & 0xFF not in {
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                }:
                    raise
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise
                sleep(min(0.01, remaining))
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise
                connection.execute(f"PRAGMA busy_timeout={int(remaining * 1000)}")
                timeout_changed = True
    finally:
        if timeout_changed:
            connection.execute(
                f"PRAGMA busy_timeout={_CONNECTION_CONFIG.busy_timeout_ms}"
            )


def sqlite_sql(statement: ClauseElement) -> tuple[str, dict[str, Any]]:
    compiled = statement.compile(
        dialect=SQLITE_DIALECT, compile_kwargs={"render_postcompile": True}
    )
    return str(compiled), compiled.params or {}
