"""Explicit recovery of a stopped demo capture, not a source migration.

Default is read-only. --commit updates ONLY source_file.native_session, under
SQLite compare-and-swap. Every source/capture/inventory hash must match. The
receipt retains the previous session and all plans/decisions remain unchanged.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from app.native_query.live_client import LiveQueryClient, LiveSession
from app.native_query.live_inventory import load_inventory

IDENTITY = ("source_sha256", "snapshot_sha256", "inventory_sha256", "units_code",
            "watched_databases", "unavailable_xrefs", "plugin_version")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database", type=Path)
    parser.add_argument("project")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--commit", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        raw = db.execute("SELECT payload FROM projects WHERE id=?", (args.project,)).fetchone()[0]
    value = json.loads(raw)
    old = LiveSession.model_validate(value["source_file"]["native_session"])
    try:
        os.kill(old.pid, 0)
    except ProcessLookupError:
        pass
    else:
        raise ValueError("Прежний процесс ещё работает, автоматическая замена запрещена")
    client = LiveQueryClient(pid=args.pid, timeout_seconds=240)
    if args.receipt.exists():
        evidence = json.loads(args.receipt.read_text())
        current = client.reconnect(evidence["session"]["session_id"])
    else:
        current = client.open()
    differences = [field for field in IDENTITY if getattr(old, field) != getattr(current, field)]
    if differences or current.snapshot_sha256 != value["source_file"]["content_sha256"]:
        raise ValueError(f"Исходные данные изменились: {differences}; переподключение отклонено")
    load_inventory(current)
    with Path(current.snapshot_path).open("rb") as stream:
        assert hashlib.file_digest(stream, "sha256").hexdigest() == current.snapshot_sha256
    client.inspect(current)
    evidence = {"project_id": args.project, "previous_session": old.model_dump(),
                "session": current.model_dump(), "identity_fields": list(IDENTITY),
                "payload_sha256_before": hashlib.sha256(raw.encode()).hexdigest(),
                "committed": False}
    if args.commit:
        with sqlite3.connect(args.database) as db:
            changed = db.execute(
                "UPDATE projects SET payload=json_set(payload, '$.source_file.native_session', json(?)) WHERE id=? AND payload=?",
                (current.model_dump_json(), args.project, raw),
            )
            if changed.rowcount != 1:
                raise ValueError("Проект изменился во время переподключения")
            after = json.loads(db.execute("SELECT payload FROM projects WHERE id=?", (args.project,)).fetchone()[0])
            after["source_file"]["native_session"] = value["source_file"]["native_session"]
            if after != value:
                raise ValueError("Переподключение изменило данные проекта")
        evidence["committed"] = True
    args.receipt.write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(json.dumps({"project": args.project, "committed": args.commit,
                      "session_id": current.session_id, "pid": current.pid,
                      "identity_verified": True, "receipt": str(args.receipt)}))


if __name__ == "__main__":
    main()
