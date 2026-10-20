"""Windows Core lifecycle на управляемом процессе без AutoCAD и Win32-вызовов."""

import time
from dataclasses import replace
from pathlib import Path

import pytest

from app.native_query.transport.process_contracts import NativeQueryProcessConfig
from app.native_query.transport.windows_supervision import supervise


class Child:
    pid = 123

    def __init__(
        self, *, chunks: bytes = "журнал".encode(), running: bool = False, descendant: bool = False
    ):
        self.remaining = chunks
        self.running = running
        self.descendant = descendant
        self.code = None if running else 0
        self.stops = self.closes = 0

    def poll(self):
        return self.code

    def read_available(self, maxbytes):
        value, self.remaining = self.remaining[:maxbytes], self.remaining[maxbytes:]
        return value

    def active_processes(self):
        return int(self.running or self.descendant)

    def terminate_all(self):
        self.stops += 1
        self.running = self.descendant = False
        if self.code is None:
            self.code = 1

    def wait(self, timeout):
        assert timeout >= 0
        return self.code

    def close(self):
        self.closes += 1


def config(tmp_path: Path) -> NativeQueryProcessConfig:
    return NativeQueryProcessConfig(
        core_executable=tmp_path / "accoreconsole.exe",
        worker_bundle=tmp_path / "worker",
        job_root=tmp_path,
        worker_binary_sha256="a" * 64,
        plugin_version="0.1.49",
        bootstrap_template=tmp_path / "acadiso.dwt",
        poll_seconds=0.001,
    )


def run(child, tmp_path, *, cfg=None, cancelled=None, started=None, size=0):
    return supervise(
        child,
        tmp_path,
        time.monotonic() if started is None else started,
        config(tmp_path) if cfg is None else cfg,
        cancelled,
        ("native.json", "native.json.tmp"),
        lambda _job, _names: size,
    )


def test_success_drains_final_log_and_releases_handles_without_killing(tmp_path):
    child = Child(chunks=b"abcd" * 20000)
    failure, log, signals = run(child, tmp_path)
    assert failure is None and signals == ()
    assert log == b"abcd" * 20000 == (tmp_path / "core.log").read_bytes()
    assert child.stops == 0 and child.closes == 1


@pytest.mark.parametrize("cause", ["cancelled", "timeout", "output_limit", "log_limit"])
def test_failure_stops_owned_tree_and_preserves_bounded_log(tmp_path, cause):
    child = Child(chunks=b"1234567", running=True)
    cfg = config(tmp_path)
    options = {}
    if cause == "cancelled":
        options["cancelled"] = lambda: True
    elif cause == "timeout":
        options["started"] = time.monotonic() - cfg.timeout_seconds - 1
    elif cause == "output_limit":
        options["size"] = cfg.max_reply_bytes + 1
    else:
        cfg = replace(cfg, max_log_bytes=3)
    failure, log, signals = run(child, tmp_path, cfg=cfg, **options)
    assert failure is not None and failure.code == cause
    assert child.stops == child.closes == 1
    assert signals == ("TerminateJobObject",)
    assert len(log) <= cfg.max_log_bytes
    assert (tmp_path / "core.log").read_bytes() == log


def test_successful_leader_with_live_descendant_is_not_success(tmp_path):
    child = Child(descendant=True)
    failure, _log, signals = run(child, tmp_path)
    assert failure is not None and failure.code == "process_group_alive"
    assert child.code == 0 and child.stops == child.closes == 1
    assert signals == ("TerminateJobObject",)


def test_cancellation_exception_still_stops_child_and_closes_handles(tmp_path):
    child = Child(running=True)

    def cancelled():
        raise RuntimeError("Операция отменена владельцем")

    with pytest.raises(RuntimeError, match="отменена владельцем"):
        run(child, tmp_path, cancelled=cancelled)
    assert child.stops == child.closes == 1


def test_pipe_failure_still_stops_child_and_publishes_bounded_log(tmp_path):
    class BrokenPipe(Child):
        def read_available(self, maxbytes):
            raise OSError("Канал недоступен")

    child = BrokenPipe(running=True)
    with pytest.raises(OSError, match="Канал недоступен"):
        run(child, tmp_path)
    assert child.stops == child.closes == 1
    assert (tmp_path / "core.log").read_bytes() == b""


def test_unconfirmed_tree_shutdown_is_error_even_if_leader_exited(tmp_path):
    class Stuck(Child):
        def terminate_all(self):
            self.stops += 1

    child = Stuck(descendant=True)
    with pytest.raises(OSError, match="не завершил все"):
        run(
            child,
            tmp_path,
            cfg=replace(config(tmp_path), terminate_grace_seconds=0.001),
        )
    assert child.stops == child.closes == 1
