from __future__ import annotations

import os
import sys
from pathlib import Path
from tempfile import gettempdir


TEST_DATABASE = Path(gettempdir()) / f"green-atlas-pytest-{os.getpid()}.sqlite3"
os.environ["GREEN_ATLAS_DB_PATH"] = str(TEST_DATABASE)


def pytest_sessionfinish(session: object, exitstatus: int) -> None:
    del session, exitstatus
    # SQLite files cannot be removed on Windows while the application-level
    # connections remain open. Only clean up modules the tests actually used;
    # importing the API here would create a database for pure unit-test runs.
    composition = sys.modules.get("app.composition")
    if composition is not None:
        composition.close_runtime()
    for suffix in ("", "-shm", "-wal"):
        candidate = Path(f"{TEST_DATABASE}{suffix}")
        if candidate.exists():
            candidate.unlink()
