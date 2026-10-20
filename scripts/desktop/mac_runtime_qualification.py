"""Изолированная приёмка установленного Mac runtime и снимки live-путей без записи."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps/api"))

from scripts.desktop.windows_product_install_smoke import verify_web_http  # noqa: E402
from scripts.desktop.windows_qualification import ready_record, smoke_http  # noqa: E402

MARKER = ".green-atlas-installer-qualification.json"
SCHEMA = "green-atlas.macos-installer-qa-root/1"


def plain(path: Path, *, directory: bool = False) -> Path:
    if not path.is_absolute() or str(path) != os.path.abspath(path):
        raise ValueError("Нужен канонический абсолютный путь приёмки")
    for part in reversed((path, *path.parents)):
        info = part.lstat()
        expected = stat.S_ISDIR if part != path or directory else stat.S_ISREG
        if not expected(info.st_mode):
            raise ValueError("Путь приёмки содержит ссылку или неверный тип")
    return path


def read_object(path: Path, *, maximum: int = 65536) -> dict[str, Any]:
    plain(path)
    with path.open("rb") as stream:
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("JSON приёмки превысил предел чтения")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Некорректный JSON приёмки")
    return value


def guard_root(root: Path, *, installed: bool = False) -> dict[str, Any]:
    plain(root, directory=True)
    info = root.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError("Корень приёмки должен принадлежать пользователю с правами0700")
    prefix = "GA-Mac-Qualification-"
    session = root.name.removeprefix(prefix)
    if not root.name.startswith(prefix) or str(uuid.UUID(session)).upper() != session:
        raise ValueError("Нужен точный UUID в имени корня приёмки")
    marker = plain(root / MARKER)
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    with os.fdopen(os.open(marker, flags), "rb") as stream:
        meta = os.fstat(stream.fileno())
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
                or meta.st_nlink != 1 or stat.S_IMODE(meta.st_mode) != 0o600
                or not 0 < meta.st_size <= 65536):
            raise ValueError("Неверный собственник, тип или права маркера приёмки")
        raw = stream.read(65537)
    state = json.loads(raw)
    phases = {"installed", "updated", "rolled_back"} if installed else {
        "init", "installed", "updated", "rolled_back", "uninstalled", "purged"}
    if (not isinstance(state, dict) or state.get("schema") != SCHEMA
            or type(state.get("uid")) is not int or state["uid"] != os.getuid()
            or state.get("session_uuid") != session or state.get("phase") not in phases
            or not (state.get("backup") is None or isinstance(state.get("backup"), str))):
        raise ValueError("Маркер не соответствует собственной фазе приёмки")
    return state


def new_output(root: Path, output: Path) -> None:
    guard_root(root)
    if output.parent != root or output.exists() or output.is_symlink():
        raise ValueError("Нужен новый файл отчёта непосредственно в корне приёмки")


def write_report(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    path.chmod(0o600)


def clean_environment(profile: Path) -> dict[str, str]:
    # HOME и CFFIXED_USER_HOME не подменяются; данные задаются аргументами API.
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "LANG": "en_US.UTF-8",
            "TMPDIR": str(profile / "Temp"), "TMP": str(profile / "Temp"),
            "TEMP": str(profile / "Temp"), "GREEN_ATLAS_LAYER_MODEL_PROVIDER": "names"}


class OwnedPosixPopen:
    """Только новая сессия собственного Popen, без поиска процессов по имени."""

    def __init__(self, command: list[str], profile: Path) -> None:
        self.process = subprocess.Popen(command, cwd=profile, env=clean_environment(profile),
                                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, start_new_session=True)
        try:
            assert self.process.stdout is not None
            os.set_blocking(self.process.stdout.fileno(), False)
        except BaseException as error:
            try:
                self.close()
            except (OSError, RuntimeError, subprocess.SubprocessError) as cleanup:
                error.add_note("Ошибка очистки нового frozen процесса: " + type(cleanup).__name__)
            raise

    def poll(self) -> int | None:
        return self.process.poll()

    def read_available(self, maxbytes: int) -> bytes:
        assert self.process.stdout is not None
        try:
            return os.read(self.process.stdout.fileno(), maxbytes)
        except BlockingIOError:
            return b""

    def active_processes(self) -> int:
        from app.native_query.transport.process import _group_alive

        self.process.poll()
        return int(_group_alive(self.process.pid))

    def wait(self, timeout: float | None = None) -> int:
        return self.process.wait(timeout=timeout)

    def close(self) -> None:
        from app.native_query.transport.process import _stop_group

        try:
            if self.active_processes():
                _stop_group(self.process, 2, [])
            if self.active_processes():
                raise RuntimeError("Собственная группа frozen API не завершилась")
        finally:
            if self.process.stdout is not None:
                self.process.stdout.close()


def frozen_api(package: Path, profile: Path, output: Path) -> dict[str, Any]:
    root = profile.parent
    state = guard_root(root, installed=True)
    new_output(root, output)
    if profile.name != "FrozenProfile" or profile.exists() or profile.is_symlink():
        raise ValueError("Нужен новый собственный FrozenProfile")
    if package != root / "ApplicationAddins/GreenAtlasBridge.bundle":
        raise ValueError("Проверяется только установленный пакет собственного корня")
    app = package / "Contents/Applications/Green Atlas.app/Contents/Resources"
    executable = plain(app / "Runtime/GreenAtlasRuntime")
    web = plain(app / "Web", directory=True)
    worker = plain(package / "Contents/Workers/GreenAtlasQuery.bundle", directory=True)
    worker_executable = plain(worker / "Contents/MacOS/GreenAtlasBridge")
    profile.mkdir(mode=0o700)
    for name in ("Temp", "Transfers"):
        (profile / name).mkdir(mode=0o700)
    command = [str(executable), "--data-dir", str(profile / "Workspace"), "--web-dir", str(web),
               "--ticket-root", str(profile / "Transfers"), "--cad-worker", str(worker)]
    report: dict[str, Any] = {"schema": "green-atlas.macos-frozen-api-qualification/1",
                              "passed": False, "phase": state["phase"], "installed_executable": True,
                              "runtime_sha256": file_hash(executable),
                              "worker_sha256": file_hash(worker_executable),
                              "synthetic_profile": str(profile), "cad_executed": False,
                              "blender_executed": False, "browser_javascript_executed": False}
    child = None
    try:
        child = OwnedPosixPopen(command, profile)
        ready = ready_record(child, on_failure=lambda text: write_failure(root, output, text))
        origin, secret = str(ready["origin"]), str(ready["session_token"])
        if smoke_http(origin, secret, "/api/projects", authenticated=False)[0] != 403:
            raise ValueError("Frozen API доступен без приватного токена")
        if smoke_http(origin, secret, "/_desktop/health") != (200, {"service": "green-atlas-desktop", "protocol": 1}):
            raise ValueError("Некорректный health frozen API")
        if smoke_http(origin, secret, "/api/projects") != (200, []):
            raise ValueError("Новый профиль frozen API не пуст")
        report["web"] = verify_web_http(origin, secret, web)
        if smoke_http(origin, secret, "/_desktop/shutdown", method="POST") != (202, {"state": "stopping"}):
            raise ValueError("Frozen API не принял native shutdown")
        if child.wait(20) != 0 or child.active_processes():
            raise ValueError("Frozen API не завершил собственное дерево")
        database = plain(profile / "Workspace/projects.sqlite3")
        report.update(passed=True, auth_403=True, health=True, empty_projects=True,
                      shutdown_202_exit_0=True, database_sha256=file_hash(database),
                      database_bytes=database.stat().st_size, clean_environment=True)
    except Exception as error:
        # Сообщения HTTP/READY не содержат origin/token; сырой вывод пишет callback.
        report["error"] = re.sub(r"[A-Za-z0-9_-]{43,}", "[скрыто]", str(error))
        raise
    finally:
        failure = sys.exception()
        try:
            if child is not None:
                child.close()
        except Exception as error:
            report["passed"] = False
            report["cleanup_error"] = type(error).__name__
            if failure is not None:
                failure.add_note("Ошибка очистки frozen API: " + type(error).__name__)
            else:
                raise
        finally:
            write_report(output, report)
    return report


def write_failure(root: Path, output: Path, text: str) -> None:
    path = root / (output.stem + ".startup.log")
    with path.open("x", encoding="utf-8") as stream:
        stream.write(text)
    path.chmod(0o600)


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def tree_snapshot(path: Path, *, hashes: bool) -> dict[str, Any]:
    """Рабочие БД никогда не открываются: для них сохраняется только lstat inventory."""
    rows: dict[str, Any] = {}
    for parent in path.parents:
        if parent.is_symlink():
            return {".": {"ancestor_symlink": str(parent)}}
    pending = [path]
    while pending:
        current = pending.pop()
        if len(rows) >= 100000:
            raise ValueError("Live inventory превысил предел")
        relative = current.relative_to(path).as_posix()
        try:
            info = current.lstat()
        except FileNotFoundError:
            rows[relative] = {"missing": True}
            continue
        row: dict[str, Any] = {"mode": info.st_mode, "uid": info.st_uid, "dev": info.st_dev,
               "ino": info.st_ino, "bytes": info.st_size, "mtime_ns": info.st_mtime_ns,
               "ctime_ns": info.st_ctime_ns}
        if stat.S_ISLNK(info.st_mode):
            row["symlink"] = os.readlink(current)
        elif stat.S_ISDIR(info.st_mode):
            pending.extend(sorted(current.iterdir(), reverse=True))
        elif stat.S_ISREG(info.st_mode) and hashes:
            row["sha256"] = file_hash(current)
        rows[relative] = row
    return rows


def snapshot_live() -> dict[str, Any]:
    # Реальная запись текущего UID, а не возможный HOME запускающего shell.
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    support = home / "Library/Application Support"
    paths = {
        "package": (support / "Autodesk/ApplicationAddins/GreenAtlasBridge.bundle", True),
        "launcher": (home / "Applications/Green Atlas.app", True),
        "workspace": (support / "Green Atlas/Workspace", False),
        "transfers": (support / "Green Atlas/Transfers", False),
        "settings": (support / "Green Atlas/render-resources.json", False),
    }
    for identifier in ("ru.green-atlas.desktop", "ru.green-atlas.launcher", "ru.green-atlas.autocad-installer"):
        paths[identifier + ".preferences"] = (home / "Library/Preferences" / (identifier + ".plist"), False)
        paths[identifier + ".saved_state"] = (home / "Library/Saved Application State" / (identifier + ".savedState"), False)
    return {"schema": "green-atlas.macos-live-fingerprint/1", "uid": os.getuid(),
            "paths": {key: tree_snapshot(path, hashes=hashes) for key, (path, hashes) in paths.items()},
            "workspace_database_content_read": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    frozen = commands.add_parser("frozen-api")
    for name in ("package", "profile", "output"):
        frozen.add_argument("--" + name, required=True, type=Path)
    for command in ("snapshot-live", "compare-live"):
        sub = commands.add_parser(command)
        sub.add_argument("--root", required=True, type=Path)
        sub.add_argument("--output", required=True, type=Path)
        if command == "compare-live":
            sub.add_argument("--before", required=True, type=Path)
    args = parser.parse_args()
    if args.command == "frozen-api":
        if sys.platform != "darwin":
            parser.error("Настоящая frozen приёмка требует macOS")
        frozen_api(args.package, args.profile, args.output)
    else:
        new_output(args.root, args.output)
        actual = snapshot_live()
        if args.command == "snapshot-live":
            write_report(args.output, actual)
        else:
            if args.before.parent != args.root:
                raise ValueError("Снимок live должен принадлежать корню приёмки")
            expected = read_object(args.before, maximum=64 * 1024**2)
            changed = expected != actual
            write_report(args.output, {"schema": "green-atlas.macos-live-comparison/1", "passed": not changed,
                                       "changed": changed, "snapshot": actual})
            if changed:
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
