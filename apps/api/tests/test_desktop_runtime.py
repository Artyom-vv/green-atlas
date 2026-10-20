"""Изолированный desktop-runtime: запуск дочернего API, доступ и повторное открытие."""

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from queue import Empty, Queue
from threading import Thread

import pytest

from app.desktop.lease import WorkspaceLease
from app.desktop.runtime import (
    bind_loopback_listener,
    configure_local_storage,
    parse_listen_port,
)
from app.desktop.web import SESSION_HEADER


def ready_line(process: subprocess.Popen[str], timeout: float = 30) -> str:
    """Windows pipe не является socket: читаем ready с отдельным ограниченным ожиданием."""
    stream = process.stdout
    assert stream is not None
    records: Queue[str | Exception] = Queue(maxsize=1)

    def read() -> None:
        try:
            records.put(stream.readline())
        except Exception as error:
            records.put(error)

    Thread(target=read, daemon=True).start()
    try:
        value = records.get(timeout=timeout)
    except Empty:
        pytest.fail("Runtime не объявил готовность за отведённое время")
    if isinstance(value, Exception):
        raise value
    return value


def test_workspace_has_one_runtime_owner(tmp_path: Path) -> None:
    first = WorkspaceLease(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="уже работает"):
            WorkspaceLease(tmp_path)
    finally:
        first.close()
    second = WorkspaceLease(tmp_path)
    second.close()


def test_local_storage_does_not_reuse_remote_or_developer_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Окружение процесса восстанавливается после проверки локальной конфигурации.
    for key in (
        "GREEN_ATLAS_DB_PATH",
        "GREEN_ATLAS_CAD_INTAKE_PATH",
        "GREEN_ATLAS_CAD_ROOTS_JSON",
        "GREEN_ATLAS_BRIDGE_ORIGIN",
        "GREEN_ATLAS_BRIDGE_PROXY_KEY",
        "GREEN_ATLAS_DWG_CONVERTER",
    ):
        monkeypatch.setenv(key, "previous-value")
    directory = configure_local_storage(tmp_path.resolve() / "desktop")
    assert os.environ["GREEN_ATLAS_DB_PATH"] == str(directory / "projects.sqlite3")
    assert os.environ["GREEN_ATLAS_CAD_ROOTS_JSON"] == "{}"
    assert "GREEN_ATLAS_BRIDGE_ORIGIN" not in os.environ
    assert "GREEN_ATLAS_BRIDGE_PROXY_KEY" not in os.environ


def test_symlink_storage_is_not_silently_followed(tmp_path: Path) -> None:
    link = tmp_path / "alias"
    target = tmp_path / "target"
    target.mkdir()
    if os.name == "nt":
        from _winapi import CreateJunction

        CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    try:
        with pytest.raises(ValueError, match="symbolic"):
            configure_local_storage(link / "workspace")
    finally:
        if os.name == "nt":
            link.rmdir()


def test_private_restart_port_is_explicit_and_never_reuses_a_live_listener() -> None:
    assert parse_listen_port("1024") == 1024
    assert parse_listen_port("65535") == 65535
    for bad in ("0", "80", "65536", "+8123", " 8123", "８１２３", "8123/", "8123\n"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_listen_port(bad)
    with bind_loopback_listener(None) as occupied:
        occupied.listen()
        port = occupied.getsockname()[1]
        with pytest.raises(OSError):
            bind_loopback_listener(port)


def test_real_runtime_selects_port_and_serves_isolated_project_list(
    tmp_path: Path,
) -> None:
    root = tmp_path.resolve()
    web = root / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>Desktop runtime test</title>")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    with (root / "runtime.log").open("w+") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.desktop.runtime",
                "--data-dir",
                str(root / "data"),
                "--web-dir",
                str(web),
            ],
            stdout=subprocess.PIPE,
            stderr=log,
            env=env,
            text=True,
        )
        assert process.stdout is not None
        try:
            line = ready_line(process)
            if not line:
                log.seek(0)
                pytest.fail("Local runtime stopped: " + log.read()[-2000:])
            receipt = json.loads(line)
            assert receipt["service"] == "green-atlas-desktop"
            origin, secret = receipt["origin"], receipt["session_token"]
            assert origin.startswith("http://127.0.0.1:")
            assert len(secret) >= 43
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with pytest.raises(urllib.error.HTTPError) as unauthorized:
                opener.open(origin + "/api/projects", timeout=10)
            assert unauthorized.value.code == 403
            request = urllib.request.Request(
                origin + "/api/projects", headers={SESSION_HEADER: secret}
            )
            with opener.open(request, timeout=10) as response:
                projects = json.load(response)
            assert projects == []
            # Удаление в настоящем дочернем API не останавливает сервис. Повторный
            # запуск ниже читает то же хранилище после подтверждённого удаления.
            create = urllib.request.Request(
                origin + "/api/projects",
                data=json.dumps({"name": "Проект для удаления"}).encode(),
                headers={SESSION_HEADER: secret, "Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(create, timeout=10) as response:
                project = json.load(response)
            delete = urllib.request.Request(
                origin + f"/api/projects/{project['id']}",
                headers={
                    SESSION_HEADER: secret,
                    "If-Match": f'"{project["state_version"]}"',
                },
                method="DELETE",
            )
            with opener.open(delete, timeout=10) as response:
                assert response.status == 204
            with opener.open(request, timeout=10) as response:
                assert json.load(response) == []
            assert (root / "data/projects.sqlite3").is_file()
            log.flush()
            log.seek(0)
            assert secret not in log.read()
            # Windows host завершает CREATE_NO_WINDOW-процесс через native API,
            # затем ждёт освобождения профиля до перезапуска на том же порту.
            shutdown = urllib.request.Request(
                origin + "/_desktop/shutdown",
                data=b"{}",
                headers={SESSION_HEADER: secret, "Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(shutdown, timeout=10) as response:
                assert response.status == 202
                assert json.load(response) == {"state": "stopping"}
            assert process.wait(timeout=10) == 0
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()

    # После потери ответа тот же документ обращается к прежнему origin.
    # Хранилище и порт прежние, токен новый; старая cookie и заголовок отклоняются.
    port = int(origin.rsplit(":", 1)[1])
    with (root / "restart.log").open("w+") as log:
        restarted = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.desktop.runtime",
                "--data-dir",
                str(root / "data"),
                "--web-dir",
                str(web),
                "--listen-port",
                str(port),
            ],
            stdout=subprocess.PIPE,
            stderr=log,
            env=env,
            text=True,
        )
        assert restarted.stdout is not None
        try:
            line = ready_line(restarted)
            if not line:
                log.seek(0)
                pytest.fail("Runtime restart failed: " + log.read()[-2000:])
            rotated = json.loads(line)
            assert rotated["origin"] == origin
            assert rotated["session_token"] != secret
            for headers in (
                {SESSION_HEADER: secret},
                {"Cookie": "green_atlas_local_session=" + secret},
            ):
                with pytest.raises(urllib.error.HTTPError) as stale:
                    opener.open(
                        urllib.request.Request(
                            origin + "/api/projects", headers=headers
                        ),
                        timeout=10,
                    )
                assert stale.value.code == 403
            with opener.open(
                urllib.request.Request(
                    origin + "/api/projects",
                    headers={SESSION_HEADER: rotated["session_token"]},
                ),
                timeout=10,
            ) as response:
                assert json.load(response) == []
        finally:
            restarted.terminate()
            try:
                restarted.wait(timeout=10)
            except subprocess.TimeoutExpired:
                restarted.kill()
                restarted.wait(timeout=5)
            restarted.stdout.close()


def test_occupied_restart_port_fails_before_workspace_is_opened(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    web = root / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>Occupied restart</title>")
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    with bind_loopback_listener(None) as occupied:
        occupied.listen()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.desktop.runtime",
                "--data-dir",
                str(root / "data"),
                "--web-dir",
                str(web),
                "--listen-port",
                str(occupied.getsockname()[1]),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    assert result.returncode != 0
    assert "green-atlas-desktop" not in result.stdout
    assert not (root / "data").exists()
