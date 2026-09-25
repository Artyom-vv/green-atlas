"""Open one real DXF/session in the ordinary local runtime (explicit demo entry).

The installed GAOPEN package is not silently replaced. No old project/map is
borrowed and no layer decisions or test working areas are manufactured here.
"""

import argparse
import hashlib
from pathlib import Path

from app.composition import create_runtime
from app.native_query.live_client import LiveQueryClient
from app.native_query.live_inventory import load_inventory
from app.native_query.live_runtime import LiveBinding, binding_path, save_binding


def rebind_project(database, project_id, client):
    """Reopen exactly the same captured basis, preserving plan and user decisions."""
    path = binding_path(database)
    previous = LiveBinding.model_validate_json(path.read_bytes())
    if project_id != previous.project_id:
        raise ValueError("Переподключение относится к другому проекту")
    session = client.open()
    for field in ("source_sha256", "snapshot_sha256", "inventory_sha256",
                  "units_code", "watched_databases", "unavailable_xrefs"):
        if getattr(session, field) != getattr(previous.session, field):
            raise ValueError(f"Исходные данные изменились: {field}")
    load_inventory(session)
    runtime = create_runtime(database)
    try:
        project = runtime.application.get(project_id, lightweight=True)
        if not project.source_file or project.source_file.content_sha256 != session.snapshot_sha256:
            raise ValueError("Карта проекта относится к другому захвату")
        client.inspect(session)
        save_binding(database, previous.model_copy(update={"session": session}))
        print(f"Reconnected: {project.id}; plan unchanged; PID={session.pid}")
    finally:
        runtime.close()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--pid", required=True, type=int)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--name", default="Кустанайская")
    parser.add_argument("--session-id")
    parser.add_argument("--project-id")
    parser.add_argument("--rebind", action="store_true")
    args = parser.parse_args()
    client = LiveQueryClient(pid=args.pid, timeout_seconds=240)
    if args.rebind:
        if not args.project_id or args.session_id:
            parser.error("Rebind requires --project-id, without --session-id")
        rebind_project(args.database, args.project_id, client)
        return
    if binding_path(args.database).exists():
        parser.error("This database already has a live binding; do not replace a project implicitly")
    session = client.reconnect(args.session_id) if args.session_id else client.open()
    if Path(session.source_path).suffix.lower() != ".dxf":
        raise ValueError("Для этого демо откройте DXF в AutoCAD")
    content = Path(session.snapshot_path).read_bytes()
    if hashlib.sha256(content).hexdigest() != session.snapshot_sha256:
        raise ValueError("Снимок AutoCAD изменён после захвата")
    runtime = create_runtime(args.database)
    try:
        project = runtime.application.get(args.project_id) if args.project_id else runtime.application.create_project(args.name)
        runtime.application.import_autocad_live(project.id, Path(session.source_path).name,
            content, autocad_version="2027.0.1", target="macos-arm64")
        client.inspect(session)
        save_binding(args.database, LiveBinding(project_id=project.id, session=session))
        print(f"Project: {project.id}\nDatabase: {args.database}\nSource: {session.source_path}")
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
