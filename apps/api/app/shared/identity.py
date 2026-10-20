"""Адаптеры времени UTC и случайной идентичности, внедряемые при сборке приложения."""

from datetime import UTC, datetime
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def random_id() -> str:
    return str(uuid4())
