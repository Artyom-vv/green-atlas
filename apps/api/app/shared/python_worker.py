"""Launch existing isolated workers from Python or the bundled desktop runtime."""

from __future__ import annotations

import runpy
import sys

WORKER_MODULES = frozenset(
    {
        "app.cad_delivery.compile_worker",
        "app.cad_intake.worker",
        "app.cad_intake.prepare_worker",
        "app.cad_intake.preview_worker",
        "app.cad_import.inspection",
        "app.cad_import.aoi_worker",
        "app.dxf_import.live_worker",
    }
)


def worker_command(module: str, *arguments: str) -> list[str]:
    if module not in WORKER_MODULES:
        raise ValueError("Unknown Green Atlas worker")
    flag = "--worker" if getattr(sys, "frozen", False) else "-m"
    return [sys.executable, flag, module, *arguments]


def dispatch_worker(arguments: list[str]) -> bool:
    """Called before API/GUI startup; never dynamically execute arbitrary modules."""
    if not arguments or arguments[0] != "--worker":
        return False
    if len(arguments) < 2 or arguments[1] not in WORKER_MODULES:
        raise ValueError("Unknown Green Atlas worker")
    previous = sys.argv
    try:
        sys.argv = [arguments[1], *arguments[2:]]
        runpy.run_module(arguments[1], run_name="__main__", alter_sys=True)
    finally:
        sys.argv = previous
    return True
