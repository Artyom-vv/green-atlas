"""Закрытие сервисов desktop до освобождения профиля, включая частичный запуск."""

import sys
from pathlib import Path

import pytest

from app import composition
from app.desktop import handoff, runtime


@pytest.mark.parametrize("failure", [None, "server", "handoff-close"])
def test_shutdown_releases_services_before_profile_even_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    events: list[str] = []
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<title>Изолированная проверка</title>")

    class Listener:
        def getsockname(self) -> tuple[str, int]:
            return "127.0.0.1", 15235

        def close(self) -> None:
            events.append("listener")

    class Lease:
        def __init__(self, _root: Path) -> None:
            pass

        def close(self) -> None:
            events.append("lease")

    class Handoff:
        def __init__(self, _application: object, _root: Path) -> None:
            pass

        def close(self) -> None:
            events.append("handoff")
            if failure == "handoff-close":
                raise RuntimeError("handoff-close")

    def run(_server: object, **_kwargs: object) -> None:
        events.append("run")
        if failure == "server":
            raise RuntimeError("server")

    monkeypatch.setattr(runtime, "bind_loopback_listener", lambda _port: Listener())
    monkeypatch.setattr(runtime, "configure_local_storage", lambda root: root)
    monkeypatch.setattr(runtime, "WorkspaceLease", Lease)
    monkeypatch.setattr(
        runtime, "create_desktop_web", lambda *_args, **_kwargs: object()
    )
    monkeypatch.setattr(runtime.uvicorn.Server, "run", run)
    monkeypatch.setattr(composition, "get_runtime", lambda: object())
    monkeypatch.setattr(composition, "close_runtime", lambda: events.append("services"))
    monkeypatch.setattr(handoff, "LocalHandoff", Handoff)
    monkeypatch.setenv("GREEN_ATLAS_LAYER_RECOGNITION_PRESETS", "previous")
    monkeypatch.setenv("GREEN_ATLAS_LAYER_MODEL_PROVIDER", "auto")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runtime",
            "--data-dir",
            str(tmp_path / "profile"),
            "--web-dir",
            str(web),
            "--ticket-root",
            str(tmp_path / "transfers"),
        ],
    )
    if failure:
        with pytest.raises(RuntimeError, match=failure):
            runtime.main()
    else:
        assert runtime.main() == 0
    assert events == ["run", "handoff", "services", "lease", "listener"]
    assert not (tmp_path / "profile").exists()


def test_failed_storage_setup_releases_listener_before_any_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    class Listener:
        def close(self) -> None:
            events.append("listener")

    def failed_storage(_path: Path) -> Path:
        raise ValueError("storage unavailable")

    monkeypatch.setattr(runtime, "bind_loopback_listener", lambda _port: Listener())
    monkeypatch.setattr(runtime, "configure_local_storage", failed_storage)
    monkeypatch.setattr(
        sys,
        "argv",
        ["runtime", "--data-dir", str(tmp_path), "--web-dir", str(tmp_path)],
    )
    with pytest.raises(ValueError, match="storage unavailable"):
        runtime.main()
    assert events == ["listener"]
