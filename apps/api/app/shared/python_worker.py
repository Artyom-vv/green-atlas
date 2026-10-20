"""Запуск существующих изолированных Python-работников из интерпретатора или собранного
desktop runtime.
"""

from __future__ import annotations

import runpy
import sys
from collections.abc import Sequence

WORKER_MODULES = frozenset(
    {
        "app.cad_import.legacy.inspection",
        "app.dxf_import.native.live_worker",
    }
)


def is_bundled_worker_command(command: Sequence[str]) -> bool:
    """Библиотеки комплекта наследует только разрешённый worker своего executable."""
    return (
        len(command) >= 3
        and command[0] == sys.executable
        and command[1] == "--worker"
        and command[2] in WORKER_MODULES
    )


def worker_command(module: str, *arguments: str) -> list[str]:
    if module not in WORKER_MODULES:
        raise ValueError("Unknown Green Atlas worker")
    flag = "--worker" if getattr(sys, "frozen", False) else "-m"
    return [sys.executable, flag, module, *arguments]


def dispatch_worker(arguments: list[str]) -> bool:
    """Запускает разрешённый обработчик до API/GUI без динамического вызова произвольных
    модулей.
    """
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
