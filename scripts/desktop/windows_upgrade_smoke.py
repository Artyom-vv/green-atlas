"""Real released119 -> current wizard update in an owned ordinary CI account."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "apps/api"))

from scripts.desktop.windows_product_install_smoke import (
    digest,
    read_object,
    verify_hkcu,
    verify_web_http,
)
from scripts.desktop.windows_qualification import ready_record, wait_clean_exit
from scripts.desktop.windows_wizard_smoke import (
    known_folder,
    require,
    token_environment,
)


def project_probe(package, data, environment, *, create):
    """Ask the installed old/new EXE to write/read its own saved project schema."""
    from app.shared.windows_process import launch_owned

    windows = package / "Contents/Windows"
    child = launch_owned(
        [
            str(windows / "Runtime/GreenAtlasRuntime.exe"),
            "--data-dir",
            str(data),
            "--web-dir",
            str(windows / "Web"),
        ],
        cwd=windows,
        environment=environment,
    )
    try:
        ready = ready_record(child)
        opener = build_opener(ProxyHandler({}))

        def api(path, payload=None):
            request = Request(
                str(ready["origin"]) + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={
                    "X-Green-Atlas-Local-Session": str(ready["session_token"]),
                    "Content-Type": "application/json",
                },
            )
            with opener.open(request, timeout=45) as response:
                return json.load(response)

        web = verify_web_http(
            str(ready["origin"]), str(ready["session_token"]), windows / "Web"
        )
        if create:
            project = api(
                "/api/projects", {"name": "Сохранённый проект до обновления119"}
            )
            require(
                project["name"] == "Сохранённый проект до обновления119",
                "Old EXE не создал контрольный проект",
            )
        else:
            projects = api("/api/projects")
            require(len(projects) == 1, "Обновление изменило каталог проектов")
            project = api("/api/projects/" + projects[0]["id"])
        require(
            api("/_desktop/shutdown", {}) == {"state": "stopping"},
            "Нет graceful shutdown",
        )
        shutdown = wait_clean_exit(
            child,
            timeout=20,
            label="API119 до обновления" if create else "API после обновления",
        )
        return project, web, shutdown
    finally:
        child.close()


def main(*, upgrade_control=None, project_probe_fn=project_probe):
    import winreg

    from app.distribution.preflight import manifest_identity
    from app.distribution.windows_install import load_owned_bundle, plain_directory
    from app.distribution.windows_setup_guard import current_user_sid
    from app.distribution.windows_shell import (
        UNINSTALL_KEY,
        native_uninstaller,
        require_windows_user,
    )

    parser = argparse.ArgumentParser()
    for name in ("setup", "setup-receipt", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--expected-sid-hex", required=True)
    args = parser.parse_args()
    require_windows_user()
    require(
        current_user_sid().hex() == args.expected_sid_hex, "Неверный ordinary CI SID"
    )
    verify_hkcu(current_user_sid())
    output = args.output.absolute()
    require(output.is_relative_to(REPO / ".runtime"), "Вывод только внутри CI checkout")
    plain_directory(output.parent)
    output.mkdir()
    report = {
        "schema": "green-atlas.windows-version-upgrade-ci/1",
        "passed": False,
        "ordinary_user_token": True,
        "synthetic_profile_only": True,
        "product_qualified": False,
        "autocad_executed": False,
        "blender_executed": False,
    }
    try:
        inputs = (
            upgrade_control
            if upgrade_control is not None
            else read_object(REPO / "tools/autocad-bridge/windows-product-inputs.json")[
                "upgrade_control"
            ]
        )
        previous = (
            REPO / ".runtime/autocad-bridge/ci-product/previous" / inputs["asset"]
        )
        require(
            digest(previous) == inputs["sha256"],
            "Прежний released Setup не совпадает с SHA",
        )
        build = read_object(args.setup_receipt)
        require(
            digest(args.setup) == build["sha256"],
            "Новый Setup не совпадает с квитанцией",
        )
        environment = token_environment()
        profile_home = Path(environment.get("USERPROFILE", ""))
        require(
            profile_home.is_absolute() and profile_home.name.startswith("ga-ci-"),
            "Windows не загрузила профиль одноразового SID",
        )
        # Shell разворачивает пути HKCU через окружение текущего процесса.
        # Launcher может наследовать профиль администратора, как и в wizard smoke.
        os.environ.update(environment)
        home = known_folder("5E6C858F-0E22-4760-9AFE-EA3317B67173")
        roaming = known_folder("3EB685DB-65F9-4CF6-A03A-E3EF65729F3D")
        local = known_folder("F1B32785-6FBA-4FCF-9D55-7B8E7F157091")
        require(
            home.name.startswith("ga-ci-")
            and roaming.is_relative_to(home)
            and local.is_relative_to(home),
            "Не собственный одноразовый профиль",
        )
        environment.update(
            APPDATA=str(roaming),
            LOCALAPPDATA=str(local),
            USERPROFILE=str(home),
            TEMP=str(output.parent / "Temp"),
            TMP=str(output.parent / "Temp"),
            PATH=str(Path(os.environ["SystemRoot"]) / "System32"),
            GREEN_ATLAS_LAYER_MODEL_PROVIDER="names",
        )
        installed = roaming / "Autodesk/ApplicationPlugins/GreenAtlas.bundle"
        require(not installed.exists(), "Нужен новый профиль")
        data = local / "GreenAtlas/Workspace"
        data.mkdir(parents=True)
        sentinel = data / "upgrade-preserve.txt"
        sentinel.write_bytes(os.urandom(37))
        sentinel_sha = digest(sentinel)

        def install(executable, name):
            report["stage"] = name
            result = subprocess.run(
                [
                    str(executable),
                    "/VERYSILENT",
                    "/SUPPRESSMSGBOXES",
                    "/NORESTART",
                    "/LOG=" + str(output / (name + ".log")),
                ],
                cwd=output,
                env=environment,
                timeout=600,
                check=False,
            )
            if result.returncode != 0:
                import re

                text = (output / (name + ".log")).read_text(
                    encoding="utf-8", errors="replace"
                )
                tail = "\n".join(text.splitlines()[-12:])[-2400:]
                tail = re.sub(r"[A-Za-z0-9_-]{43,}", "[hidden]", tail)
                raise RuntimeError(
                    f"Native Setup failed: {name}, exit={result.returncode}; owned log tail: {tail}"
                )

        install(previous, "install-previous119")
        old = load_owned_bundle(installed)
        require(
            old.layout.version == inputs["version"], "Установлена другая прежняя версия"
        )
        report["stage"] = "previous119_project"
        original, report["previous_web"], report["previous_api_exit"] = (
            project_probe_fn(installed, data, environment, create=True)
        )
        database = data / "projects.sqlite3"
        before_update = digest(database)
        install(args.setup, "update-current")
        current = load_owned_bundle(installed)
        require(
            current.layout.version == build["product_version"]
            and current.layout.version != old.layout.version,
            "Нет перехода между разными версиями",
        )
        require(
            digest(installed / "distribution-manifest.json")
            == build["distribution_manifest_sha256"],
            "Обновлённый bundle не совпадает с build",
        )
        require(
            digest(database) == before_update and digest(sentinel) == sentinel_sha,
            "Setup изменил рабочие данные",
        )
        report["stage"] = "updated_project"
        reopened, report["updated_web"], report["updated_api_exit"] = project_probe_fn(
            installed, data, environment, create=False
        )
        require(
            reopened["id"] == original["id"] and reopened["name"] == original["name"],
            "Потерян сохранённый проект119",
        )
        require(
            reopened["source_file"] == original["source_file"]
            and reopened["plan"] == original["plan"],
            "Изменены данные проекта119",
        )
        before_remove = digest(database)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            uninstaller = native_uninstaller(
                local, Path(winreg.QueryValueEx(key, "NativeUninstaller")[0])
            )
        result = subprocess.run(
            [str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"],
            cwd=output,
            env=environment,
            timeout=600,
            check=False,
        )
        require(
            result.returncode == 0 and not installed.exists(),
            "Удаление обновлённой версии не прошло",
        )
        require(
            digest(database) == before_remove and digest(sentinel) == sentinel_sha,
            "Удаление изменило данные",
        )
        report.update(
            passed=True,
            stage="complete",
            setup_sha256=build["sha256"],
            previous_setup_sha256=inputs["sha256"],
            source_manifest_sha256=build["source_manifest_sha256"],
            previous_version=old.layout.version,
            current_version=current.layout.version,
            package_identity=manifest_identity(current),
            different_version_update_tested=True,
            old_executable_created_project=project_probe_fn is project_probe,
            new_executable_reopened_project=True,
            update_preserved_database_bytes=True,
            uninstall_preserved_database_bytes=True,
            prepared_cad_project_migration_tested=False,
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
    ) as error:
        report["error"] = type(error).__name__ + ": " + str(error)
    (output / "install-ci-receipt.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"passed": report["passed"], "product_qualified": False}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
