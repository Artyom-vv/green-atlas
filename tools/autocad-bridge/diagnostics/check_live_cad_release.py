"""Fresh AutoCAD capture -> product import -> same-capture archive -> saved-plan CAD.

All writes use a new private test directory and database. A stored plan is
copied solely to test CAD output; this does not certify planting feasibility.
"""
import argparse
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
from pathlib import Path
from uuid import uuid4

from app.composition import create_runtime
from app.native_query.live_client import LiveQueryClient
from app.planning.contracts import Plan


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--worker", type=Path, required=True)
    parser.add_argument("--plan-database", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    # /tmp is a symlink on macOS; production capture storage rejects paths
    # below symlinked parents. Resolve before creating the private test root.
    root = args.output.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    if not str(root).isascii():
        raise ValueError("Use an ASCII scratch directory for AutoCAD script encoding")
    before = {str(path): digest(path) for path in args.source.parent.rglob("*.dwg")}
    shutil.copytree(args.source.parent, root / "input")
    drawing = root / "input" / args.source.name
    bundle = root / "Bridge.dbx"
    shutil.copytree(args.bundle, bundle)
    profile = root / "profile"
    profile.mkdir()
    script = root / "live.scr"
    script.write_text('(setvar "TRUSTEDPATHS" "' + str(root) + '")\n'
                      '(setvar "FILEDIA" 0)\n'
                      '(arxload "' + str(bundle) + '")\nGADEMOLOOP\n_QUIT\n_Y\n\n', encoding="ascii")
    core = Path("/Applications/Autodesk/AutoCAD 2027/AutoCAD 2027.app/Contents/Helpers/AcCoreConsole.app/Contents/MacOS/AcCoreConsole")
    command = ["/usr/bin/arch", "-x86_64", str(core), "/i", str(drawing),
               "/s", str(script), "/isolate", "ga-release-live-" + uuid4().hex, str(profile)]
    runtime = None
    with (root / "core.log").open("xb") as log:
        child = subprocess.Popen(command, cwd=root, stdin=subprocess.DEVNULL,
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        client = LiveQueryClient(pid=child.pid, timeout_seconds=600)
        try:
            print("Opening a new same-capture session", flush=True)
            session = client.open()
            (root / "session.json").write_text(session.model_dump_json(indent=2))
            os.environ["GREEN_ATLAS_CAD_RELEASE_WORKER"] = str(args.worker)
            runtime = create_runtime(str(root / "test.sqlite3"))
            app = runtime.application
            project = app.create_project("Проверка выпуска Кустанайской")
            project = app.import_autocad_live_file(project.id, drawing.name, Path(session.snapshot_path),
                session.snapshot_sha256, autocad_version="2027", target="macos-x86_64")
            print("Snapshot imported; attaching a copy of the saved test plan", flush=True)
            with sqlite3.connect(f"file:{args.plan_database}?mode=ro", uri=True) as connection:
                raw = connection.execute("SELECT payload FROM projects WHERE id=?", (args.project_id,)).fetchone()
            if raw is None:
                raise ValueError("Saved test plan not found")
            project.plan = Plan.model_validate(json.loads(raw[0])["plan"])
            project.source_file.native_session = session
            runtime.project_repository.save(project)
            print(f"Exporting {len(project.plan.objects)} saved plantings", flush=True)
            artifact = app.export(project.id)
            content = app.download_export(project.id, artifact.id)
            (root / "result.zip").write_bytes(content)
            # A repeat uses the retained archive, not the (possibly invalidated
            # by XREF clone notifications) editor session.
            repeated = app.export(project.id)
            result = {"project_id": project.id, "artifact": artifact.model_dump(),
                      "repeat_artifact": repeated.model_dump(), "plantings": len(project.plan.objects),
                      "source_unchanged": all(digest(Path(path)) == value for path, value in before.items())}
            (root / "receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            if runtime:
                runtime.close()
            if child.poll() is None:
                client.queue.mkdir(mode=0o700, exist_ok=True)
                (client.queue / "stop-live-demo").touch()
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    os.killpg(child.pid, signal.SIGTERM)
                    try:
                        child.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid, signal.SIGKILL)
                        child.wait()
            if not all(digest(Path(path)) == value for path, value in before.items()):
                raise RuntimeError("Input file changed during qualification")
            print(f"Owned AutoCAD exit: {child.returncode}", flush=True)


if __name__ == "__main__":
    main()
