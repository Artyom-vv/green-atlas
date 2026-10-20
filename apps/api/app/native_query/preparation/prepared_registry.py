"""Проверенные хеши производных расчётных ресурсов принадлежат базе проекта.

Соседний JSON-файл не может назначить собственный SHA ожидаемым значением.
После восстановления без производных файлов запись реестра вызывает их
повторное построение из сохранённого авторитетного снимка.
"""

import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.schema import CreateTable

from app.desktop.tickets import direct_path
from app.operations.progress import check_operation_cancelled
from app.projects.workspace_backup import backup_workspace
from app.shared.sqlite_sql import configure_sqlite_connection, sqlite_sql
from app.shared.storage_schema import PREPARED_BINARY_RESOURCES


def read_prepared_registration(
    database_path: Path, key: str, source_sha256: str
) -> dict | None:
    database_path = direct_path(
        database_path.absolute(), database_path.parent.absolute()
    )
    if not database_path.exists():
        return None
    with closing(
        sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True)
    ) as connection:
        check_operation_cancelled()
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PREPARED_BINARY_RESOURCES.name,),
        ).fetchone()
        if not exists:
            return None
        sql, parameters = sqlite_sql(
            select(PREPARED_BINARY_RESOURCES).where(
                PREPARED_BINARY_RESOURCES.c.key == key,
                PREPARED_BINARY_RESOURCES.c.source_sha256 == source_sha256,
            )
        )
        cursor = connection.execute(sql, parameters)
        row = cursor.fetchone()
        return (
            dict(zip((column[0] for column in cursor.description), row, strict=True))
            if row is not None
            else None
        )


def save_prepared_registration(
    database_path: Path,
    registration: dict,
    *,
    prepared_directory: Path | None = None,
) -> None:
    database_path = direct_path(
        database_path.absolute(), database_path.parent.absolute()
    )
    exists = database_path.exists()
    connection = sqlite3.connect(database_path)
    try:
        configure_sqlite_connection(connection)
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (PREPARED_BINARY_RESOURCES.name,),
        ).fetchone()
        if not table_exists:
            # Частный каталог тестового/самостоятельного PreparedStore не имеет
            # таблицы проектов. Рабочий профиль резервируется до первой миграции.
            has_projects = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'"
            ).fetchone()
            if exists and has_projects:
                backup_workspace(
                    database_path,
                    reason="prepared-binary-resources-v1",
                    connection=connection,
                    prepared_directory=prepared_directory,
                )
        check_operation_cancelled()
        with connection:
            connection.execute(
                *sqlite_sql(CreateTable(PREPARED_BINARY_RESOURCES, if_not_exists=True))
            )
            statement = insert(PREPARED_BINARY_RESOURCES).values(**registration)
            statement = statement.on_conflict_do_update(
                index_elements=[
                    PREPARED_BINARY_RESOURCES.c.key,
                    PREPARED_BINARY_RESOURCES.c.source_sha256,
                ],
                set_={
                    name: getattr(statement.excluded, name)
                    for name in ("payload_sha256", "sha256", "bytes")
                },
            )
            connection.execute(*sqlite_sql(statement))
            check_operation_cancelled()
    finally:
        connection.close()
