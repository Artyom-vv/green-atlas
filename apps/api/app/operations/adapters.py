"""Домен журнала операций: прогресс, единственная активная запись и восстановление.

SQLAlchemy строит запросы и ограничения. Собственное sqlite3-соединение
сохраняет блокировку и сравнение исходного payload перед публикацией прогресса.
"""

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from threading import Condition, Event, RLock

from sqlalchemy import insert, select, update
from sqlalchemy.schema import CreateIndex, CreateTable

from app.operations.contracts import (
    OperationError,
    OperationKind,
    OperationStatus,
    ProjectOperation,
)
from app.operations.policy import (
    ACTIVE_OPERATION_STATUSES,
    OPERATION_EVENT_KEEPALIVE_SECONDS,
)
from app.shared.failures import FailureReason, ResourceNotFound
from app.shared.messages import message
from app.shared.sqlite_sql import configure_sqlite_connection, sqlite_sql
from app.shared.storage_schema import (
    PROJECT_OPERATIONS as operations,
)
from app.shared.storage_schema import (
    PROJECT_OPERATIONS_ACTIVE_INDEX,
    PROJECT_OPERATIONS_LATEST_INDEX,
)


def interrupted(operation: ProjectOperation) -> ProjectOperation:
    now = datetime.now(UTC).isoformat()
    operation.status = OperationStatus.INTERRUPTED
    operation.stage = message("operation.interrupted")
    operation.error = OperationError(
        code="OPERATION_INTERRUPTED",
        message=message("operation.interrupted_progress"),
    )
    operation.completed_at = now
    operation.updated_at = now
    return operation


class SqliteOperationRepository:
    """Постоянный журнал прогресса с единственной активной записью и точным CAS."""

    def __init__(self, path: str | Path, *, recover: bool = True) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._changes = Condition(self._lock)
        self._revisions: dict[str, int] = {}
        self._closed = Event()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        with self._connection:
            # Веб- и фоновый работники могут писать одновременно. После ожидания
            # действует устойчивое ограничение активности вместо временного сбоя блокировки.
            configure_sqlite_connection(self._connection)
            self._connection.execute(
                *sqlite_sql(CreateTable(operations, if_not_exists=True))
            )
            self._connection.execute(
                *sqlite_sql(
                    CreateIndex(PROJECT_OPERATIONS_LATEST_INDEX, if_not_exists=True)
                )
            )
            # Прежние базы могли принять одну команду дважды. Сохраняем новейшую
            # активную запись до создания индекса, который защищает последующие записи.
            self._collapse_duplicate_active_operations()
            self._connection.execute(
                *sqlite_sql(
                    CreateIndex(PROJECT_OPERATIONS_ACTIVE_INDEX, if_not_exists=True)
                )
            )
        if recover:
            self.recover_incomplete()

    def close(self) -> None:
        with self._changes:
            if self._closed.is_set():
                return
            self._closed.set()
            self._changes.notify_all()
            self._connection.close()

    def create(self, operation: ProjectOperation) -> ProjectOperation:
        try:
            with self._changes:
                with self._connection:
                    active = self._active(operation.project_id, operation.kind)
                    if active is not None:
                        return active
                    self._connection.execute(
                        *sqlite_sql(
                            insert(operations).values(
                                **_progress_values(operation),
                                id=operation.id,
                                project_id=operation.project_id,
                                kind=operation.kind.value,
                                created_at=operation.created_at,
                            )
                        )
                    )
                self._notify(operation.id)
        except sqlite3.IntegrityError as error:
            # Другой работник может создать запись между чтением и вставкой.
            # Частичный уникальный индекс возвращает ту же активную операцию
            # вместо двух расчётов одной карты.
            with self._lock:
                active = self._active(operation.project_id, operation.kind)
            if active is not None:
                return active
            raise ValueError(f"Operation {operation.id} already exists") from error
        return operation.model_copy(deep=True)

    def get(self, operation_id: str) -> ProjectOperation:
        with self._lock:
            row = self._connection.execute(
                *sqlite_sql(
                    select(operations.c.payload).where(operations.c.id == operation_id)
                )
            ).fetchone()
        if row is None:
            raise ResourceNotFound(FailureReason.OPERATION_MISSING)
        return ProjectOperation.model_validate_json(row["payload"])

    def save(self, operation: ProjectOperation) -> ProjectOperation:
        with self._changes:
            with self._connection:
                cursor = self._connection.execute(
                    *sqlite_sql(
                        update(operations)
                        .where(operations.c.id == operation.id)
                        .values(_progress_values(operation))
                    )
                )
            if cursor.rowcount == 0:
                raise ResourceNotFound(FailureReason.OPERATION_MISSING)
            self._notify(operation.id)
        return operation.model_copy(deep=True)

    def get_latest(
        self, project_id: str, kind: OperationKind
    ) -> ProjectOperation | None:
        with self._lock:
            row = self._connection.execute(
                *sqlite_sql(
                    select(operations.c.payload)
                    .where(
                        operations.c.project_id == project_id,
                        operations.c.kind == kind.value,
                    )
                    .order_by(operations.c.created_at.desc())
                    .limit(1)
                )
            ).fetchone()
        return ProjectOperation.model_validate_json(row["payload"]) if row else None

    def compare_and_save(
        self, expected: ProjectOperation, operation: ProjectOperation
    ) -> ProjectOperation | None:
        if expected.id != operation.id:
            raise ValueError("Cannot change operation identity")
        with self._changes:
            with self._connection:
                row = self._connection.execute(
                    *sqlite_sql(
                        select(operations.c.payload).where(
                            operations.c.id == expected.id
                        )
                    )
                ).fetchone()
                if (
                    row is None
                    or ProjectOperation.model_validate_json(row["payload"]) != expected
                ):
                    return None
                cursor = self._connection.execute(
                    *sqlite_sql(
                        update(operations)
                        .where(
                            operations.c.id == operation.id,
                            operations.c.payload == row["payload"],
                        )
                        .values(_progress_values(operation))
                    )
                )
            if cursor.rowcount:
                self._notify(operation.id)
        return operation.model_copy(deep=True) if cursor.rowcount else None

    def recover_incomplete(self) -> list[ProjectOperation]:
        with self._changes:
            with self._connection:
                rows = self._connection.execute(
                    *sqlite_sql(
                        select(operations.c.payload).where(
                            operations.c.status.in_(
                                [status.value for status in ACTIVE_OPERATION_STATUSES]
                            )
                        )
                    )
                ).fetchall()
                recovered = []
                for row in rows:
                    while row is not None:
                        current = ProjectOperation.model_validate_json(row["payload"])
                        if current.status not in ACTIVE_OPERATION_STATUSES:
                            break
                        operation = interrupted(current)
                        # Работник может опубликовать результат после чтения восстановления.
                        # Его сохранённая квитанция имеет приоритет над устаревшим снимком.
                        updated = self._connection.execute(
                            *sqlite_sql(
                                update(operations)
                                .where(
                                    operations.c.id == operation.id,
                                    operations.c.payload == row["payload"],
                                )
                                .values(_progress_values(operation))
                            )
                        )
                        if updated.rowcount:
                            recovered.append(operation)
                            break
                        # Изменённый активный прогресс ещё требует восстановления;
                        # новая завершённая квитанция остаётся без изменения.
                        row = self._connection.execute(
                            *sqlite_sql(
                                select(operations.c.payload).where(
                                    operations.c.id == operation.id
                                )
                            )
                        ).fetchone()
            for operation in recovered:
                self._notify(operation.id)
        return [item.model_copy(deep=True) for item in recovered]

    def watch(
        self,
        operation_id: str,
        *,
        stop: Event,
        keepalive_seconds: float = OPERATION_EVENT_KEEPALIVE_SECONDS,
    ) -> Iterator[ProjectOperation | None]:
        """Читать начальный снимок, затем изменения этого экземпляра после commit.

        Основной API владеет всеми записями прогресса. Другие соединения
        SQLite не создают уведомления и не являются писателями этого потока.
        """
        if keepalive_seconds <= 0:
            raise ValueError("Интервал ожидания должен быть положительным")
        with self._changes:
            initial = self.get(operation_id)
            revision = self._revisions.get(operation_id, 0)
        return self._watch(initial, revision, stop, keepalive_seconds)

    def cancel_watch(self, stop: Event) -> None:
        stop.set()
        with self._changes:
            self._changes.notify_all()

    def _notify(self, operation_id: str) -> None:
        # Вызывается с тем же RLock после выхода из транзакции SQLite.
        self._revisions[operation_id] = self._revisions.get(operation_id, 0) + 1
        self._changes.notify_all()

    def _watch(
        self,
        current: ProjectOperation,
        revision: int,
        stop: Event,
        keepalive_seconds: float,
    ) -> Iterator[ProjectOperation | None]:
        operation_id = current.id
        with self._changes:
            if stop.is_set() or self._closed.is_set():
                return
            if self._revisions.get(operation_id, 0) != revision:
                current = self.get(operation_id)
                revision = self._revisions.get(operation_id, 0)
        yield current
        while current.status in ACTIVE_OPERATION_STATUSES:
            with self._changes:
                changed = self._changes.wait_for(
                    lambda: (
                        stop.is_set()
                        or self._closed.is_set()
                        or self._revisions.get(operation_id, 0) != revision
                    ),
                    timeout=keepalive_seconds,
                )
                if stop.is_set() or self._closed.is_set():
                    return
                snapshot = None
                if changed:
                    revision = self._revisions.get(operation_id, 0)
                    current = self.get(operation_id)
                    snapshot = current
            yield snapshot

    def _collapse_duplicate_active_operations(self) -> None:
        rows = self._connection.execute(
            *sqlite_sql(
                select(operations.c.payload)
                .where(
                    operations.c.status.in_(
                        [status.value for status in ACTIVE_OPERATION_STATUSES]
                    )
                )
                .order_by(
                    operations.c.project_id,
                    operations.c.kind,
                    operations.c.created_at.desc(),
                )
            )
        ).fetchall()
        seen: set[tuple[str, OperationKind]] = set()
        for row in rows:
            operation = ProjectOperation.model_validate_json(row["payload"])
            key = (operation.project_id, operation.kind)
            if key not in seen:
                seen.add(key)
                continue
            stopped = interrupted(operation)
            self._connection.execute(
                *sqlite_sql(
                    update(operations)
                    .where(operations.c.id == stopped.id)
                    .values(_progress_values(stopped))
                )
            )

    def _active(self, project_id: str, kind: OperationKind) -> ProjectOperation | None:
        row = self._connection.execute(
            *sqlite_sql(
                select(operations.c.payload)
                .where(
                    operations.c.project_id == project_id,
                    operations.c.kind == kind.value,
                    operations.c.status.in_(
                        [status.value for status in ACTIVE_OPERATION_STATUSES]
                    ),
                )
                .order_by(operations.c.created_at.desc())
                .limit(1)
            )
        ).fetchone()
        return ProjectOperation.model_validate_json(row["payload"]) if row else None


def _progress_values(operation: ProjectOperation) -> dict[str, str]:
    return {
        "status": operation.status.value,
        "payload": operation.model_dump_json(),
        "updated_at": operation.updated_at,
    }
