"""Default time and identity adapters, injected at application composition."""

from datetime import UTC, datetime
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(UTC)


def random_id() -> str:
    return str(uuid4())
