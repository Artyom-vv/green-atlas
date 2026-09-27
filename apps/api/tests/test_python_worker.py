import sys

import pytest
from app.shared import python_worker


@pytest.mark.parametrize("module", sorted(python_worker.WORKER_MODULES))
def test_source_and_frozen_worker_use_same_module_and_arguments(monkeypatch, module):
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


def test_dispatch_does_not_start_server_or_accept_unknown_modules(monkeypatch):
    calls = []
    monkeypatch.setattr(
        python_worker.runpy,
        "run_module",
        lambda module, **kwargs: calls.append((module, sys.argv.copy())),
    )
    before = sys.argv
    assert not python_worker.dispatch_worker(["--data-dir", "data"])
    assert python_worker.dispatch_worker(
        ["--worker", "app.cad_delivery.compile_worker", "task.json"]
    )
    assert calls == [
        (
            "app.cad_delivery.compile_worker",
            ["app.cad_delivery.compile_worker", "task.json"],
        )
    ]
    assert sys.argv is before
    for args in (["--worker"], ["--worker", "os"], ["--worker", "unknown"]):
        with pytest.raises(ValueError):
            python_worker.dispatch_worker(args)
    with pytest.raises(ValueError):
        python_worker.worker_command("os")
