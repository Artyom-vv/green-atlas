"""Активные состояния журнала операций для сценариев и ограничения хранилища.

Кортеж задаёт одно правило активности, не владея переходами или соединениями.
"""

from typing import Final

from app.operations.contracts import OperationStatus

ACTIVE_OPERATION_STATUSES: Final[tuple[OperationStatus, ...]] = (
    OperationStatus.QUEUED,
    OperationStatus.RUNNING,
    OperationStatus.CANCELLING,
)

OPERATION_EVENT_KEEPALIVE_SECONDS: Final = 15.0
