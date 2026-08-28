from __future__ import annotations

import os
from pathlib import Path
from tempfile import gettempdir


TEST_DATABASE = Path(gettempdir()) / f"green-atlas-pytest-{os.getpid()}.sqlite3"
os.environ["GREEN_ATLAS_DB_PATH"] = str(TEST_DATABASE)


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    del session, exitstatus
    for suffix in ("", "-shm", "-wal"):
        candidate = Path(f"{TEST_DATABASE}{suffix}")
        if candidate.exists():
            candidate.unlink()
