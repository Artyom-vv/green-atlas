import sys

import pytest

from app.shared import python_worker


@pytest.mark.parametrize("module", sorted(python_worker.WORKER_MODULES))
def test_only_own_allowed_frozen_command_inherits_bundle(monkeypatch, module):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    command = python_worker.worker_command(module, "path with spaces.json")
    assert python_worker.is_bundled_worker_command(command)
    assert not python_worker.is_bundled_worker_command(["external.exe", *command[1:]])
    assert not python_worker.is_bundled_worker_command(
        [sys.executable, "--worker", "os"]
    )
    monkeypatch.setattr(sys, "frozen", False)
    assert not python_worker.is_bundled_worker_command(
        python_worker.worker_command(module)
    )
    assert not python_worker.is_bundled_worker_command([])


@pytest.mark.parametrize("module", sorted(python_worker.WORKER_MODULES))
def test_source_and_frozen_worker_use_same_module_and_arguments(
    monkeypatch: pytest.MonkeyPatch, module: str
) -> None:
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    assert python_worker.worker_command(module, "some path.json") == [
        sys.executable,
        "-m",
        module,
        "some path.json",
    ]
    monkeypatch.setattr(sys, "frozen", True)
    assert python_worker.worker_command(module, "some path.json") == [
        sys.executable,
        "--worker",
        module,
        "some path.json",
    ]


def test_dispatch_does_not_start_server_or_accept_unknown_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        python_worker.runpy,
        "run_module",
        lambda module, **kwargs: calls.append((module, sys.argv.copy())),
    )
    before = sys.argv
    assert not python_worker.dispatch_worker(["--data-dir", "data"])
    assert python_worker.dispatch_worker(
        ["--worker", "app.dxf_import.native.live_worker", "task.json"]
    )
    assert calls == [
        (
            "app.dxf_import.native.live_worker",
            ["app.dxf_import.native.live_worker", "task.json"],
        )
    ]
    assert sys.argv is before
    for args in (["--worker"], ["--worker", "os"], ["--worker", "unknown"]):
        with pytest.raises(ValueError):
            python_worker.dispatch_worker(args)
    with pytest.raises(ValueError):
        python_worker.worker_command("os")


@pytest.mark.parametrize(
    "module",
    [
        "app.cad_delivery.compile_worker",
        "app.cad_intake.inspection.worker",
        "app.cad_intake.prepare_worker",
        "app.cad_intake.preview_worker",
        "app.cad_import.aoi_worker",
    ],
)
def test_retired_import_workers_cannot_be_dispatched(module: str) -> None:
    with pytest.raises(ValueError, match="Unknown Green Atlas worker"):
        python_worker.worker_command(module)
    with pytest.raises(ValueError, match="Unknown Green Atlas worker"):
        python_worker.dispatch_worker(["--worker", module, "task.json"])
