"""Приватный дочерний API-процесс desktop-оболочки; готовность и одноразовый токен
передаются только по закрытому каналу.
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import secrets
import socket
from contextlib import ExitStack
from pathlib import Path

import uvicorn

from app.desktop.lease import WorkspaceLease
from app.desktop.web import create_desktop_web
from app.shared.operation_diagnostics import configure_operation_diagnostics
from app.shared.performance import configure_performance_log


def configure_local_storage(directory: Path) -> Path:
    directory = directory.absolute()
    if any(
        path.is_symlink() or (os.name == "nt" and path.is_junction())
        for path in (directory, *directory.parents)
    ):
        raise ValueError("Desktop storage must not be behind a symbolic link")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError("Desktop storage is not a directory")
    os.environ["GREEN_ATLAS_DB_PATH"] = str(directory / "projects.sqlite3")
    os.environ["GREEN_ATLAS_CAD_INTAKE_PATH"] = str(directory / "cad-intake")
    os.environ["GREEN_ATLAS_CAD_ROOTS_JSON"] = "{}"
    # Не наследуем прежний адрес developer/remote bridge и его учётные данные.
    for key in (
        "GREEN_ATLAS_BRIDGE_ORIGIN",
        "GREEN_ATLAS_BRIDGE_PROXY_KEY",
        "GREEN_ATLAS_DWG_CONVERTER",
    ):
        os.environ.pop(key, None)
    return directory


def parse_listen_port(value: str) -> int:
    """Приватное намерение перезапуска задаёт точный непривилегированный порт без
    запасного выбора.
    """
    if (
        len(value) > 5
        or not value.isascii()
        or not value.isdecimal()
        or not 1024 <= int(value) <= 65535
    ):
        raise argparse.ArgumentTypeError("Expected a numeric port from 1024 to 65535")
    return int(value)


def bind_loopback_listener(port: int | None) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt":
            exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
            if not isinstance(exclusive, int):
                raise RuntimeError("Windows requires exclusive loopback port ownership")
            listener.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        else:
            # SO_REUSEADDR разрешает TIME_WAIT закрытого соединения, а не занятого listener.
            # SO_REUSEPORT позволил бы разделить активный порт и здесь запрещён.
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port if port is not None else 0))
    except BaseException:
        listener.close()
        raise
    return listener


def main() -> int:
    parser = argparse.ArgumentParser(description="Green Atlas private desktop runtime")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--web-dir", type=Path, required=True)
    parser.add_argument("--ticket-root", type=Path)
    parser.add_argument("--cad-worker", type=Path)
    parser.add_argument("--listen-port", type=parse_listen_port)
    args = parser.parse_args()
    # Занимаем порт до открытия хранилища: конфликт перезапуска не меняет
    # рабочую среду и адрес уже открытого документа.
    with ExitStack() as owned:
        listener = bind_loopback_listener(args.listen_port)
        owned.callback(listener.close)
        data_root = configure_local_storage(args.data_dir)
        configure_performance_log(data_root)
        # Desktop использует OpenAI или предложение для точного пакетного входа;
        # локальный сеанс Codex разработчика не становится скрытой зависимостью.
        os.environ.setdefault("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
        os.environ["GREEN_ATLAS_LAYER_RECOGNITION_PRESETS"] = str(
            args.web_dir.parent / "LayerRecognition"
        )
        if args.cad_worker:
            os.environ["GREEN_ATLAS_CAD_RELEASE_WORKER"] = str(
                args.cad_worker.absolute()
            )
        if not (args.web_dir / "index.html").is_file():
            parser.error("Built desktop UI is missing from the application package")
        lease = WorkspaceLease(data_root)
        owned.callback(lease.close)
        configure_operation_diagnostics(data_root)
        # Аварийный обычный выход и завершение сервера используют одного владельца.
        atexit.register(owned.close)
        owned.callback(atexit.unregister, owned.close)
        # До сборки API настраиваем хранилище, принадлежащее этому процессу.
        from app.composition import close_runtime, get_runtime
        from app.desktop.handoff import LocalHandoff
        from app.main import app

        # После handoff закрываются renderer/recognition и БД; lease освобождается последним.
        owned.callback(close_runtime)

        handoff = (
            LocalHandoff(get_runtime(), args.ticket_root) if args.ticket_root else None
        )

        if handoff:
            owned.callback(handoff.close)

        secret = secrets.token_urlsafe(32)
        port = listener.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"

        def request_shutdown() -> None:
            server.should_exit = True

        desktop = create_desktop_web(
            app,
            args.web_dir,
            origin=origin,
            secret=secret,
            handoff=handoff,
            shutdown=request_shutdown,
        )

        class DesktopServer(uvicorn.Server):
            async def startup(self, sockets: list[socket.socket] | None = None) -> None:
                await super().startup(sockets=sockets)
                if self.started:
                    print(
                        json.dumps(
                            {
                                "service": "green-atlas-desktop",
                                "protocol": 1,
                                "origin": origin,
                                "session_token": secret,
                            }
                        ),
                        flush=True,
                    )

        server = DesktopServer(
            uvicorn.Config(
                desktop,
                host="127.0.0.1",
                port=port,
                access_log=False,
                proxy_headers=False,
                server_header=False,
                log_level="warning",
            )
        )
        server.run(sockets=[listener])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
