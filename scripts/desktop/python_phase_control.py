"""Диагностический запуск Python-фазы с дампом зависания и аварийным выходом.

Watchdog остаётся активным до завершения интерпретатора, включая ожидание
неосвобождённых потоков. Он не заменяет владение процессами в продукте.
"""

from __future__ import annotations

import argparse
import faulthandler
import json
import math
import os
import runpy
import sys
from pathlib import Path
from typing import TextIO

_DIAGNOSTIC_STREAM: TextIO | None = None
_OUTPUT_STREAM: TextIO | None = None


def main() -> int:
    global _DIAGNOSTIC_STREAM, _OUTPUT_STREAM
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline", type=float, default=120)
    parser.add_argument("--log", type=Path)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if (
        not command
        or not math.isfinite(args.deadline)
        or not 0.1 <= args.deadline <= 300
    ):
        parser.error("Нужны Python-команда и срок от 0.1 до 300 секунд")
    kind = "module" if command[0] == "-m" else "code" if command[0] == "-c" else "file"
    if (kind != "file" and len(command) < 2) or (
        kind == "file" and command[0].startswith("-")
    ):
        parser.error("Поддерживаются -m, -c или Python-файл")
    if args.log is not None:
        # Нативные stdout/stderr сразу принадлежат файлу, а не каналу PowerShell.
        # Это сохраняет и вывод C-библиотек, и аварийный выход при блокировке IO.
        _OUTPUT_STREAM = args.log.open("x", encoding="utf-8", buffering=1)
        sys.stdout.flush()
        sys.stderr.flush()
        os.dup2(_OUTPUT_STREAM.fileno(), sys.stdout.fileno())
        os.dup2(_OUTPUT_STREAM.fileno(), sys.stderr.fileno())
    # pytest меняет fd 2 при перехвате вывода. Собственный дубликат сохраняет
    # дамп вне захвата; глобальная ссылка живёт и во время Python shutdown.
    _DIAGNOSTIC_STREAM = os.fdopen(os.dup(sys.stderr.fileno()), "w", buffering=1)
    faulthandler.enable(file=_DIAGNOSTIC_STREAM)
    faulthandler.dump_traceback_later(args.deadline, file=_DIAGNOSTIC_STREAM, exit=True)
    print(
        "GA_PYTHON_PHASE_BEGIN "
        + json.dumps(
            {
                "kind": kind,
                "deadline_seconds": args.deadline,
                "pid": os.getpid(),
            }
        ),
        flush=True,
    )
    # Для -m/-c Python обычно включает текущий каталог в поиск модулей.
    sys.path[0] = (
        str(Path.cwd()) if kind != "file" else str(Path(command[0]).resolve().parent)
    )
    if kind == "module":
        sys.argv = command[1:]
        runpy.run_module(command[1], run_name="__main__", alter_sys=True)
    elif kind == "code":
        sys.argv = ["-c", *command[2:]]
        exec(  # noqa: S102 - доверенная команда -c из CI, как у самого Python.
            compile(command[1], "<phase-control>", "exec"),
            {
                "__name__": "__main__",
                "__package__": None,
                "__spec__": None,
            },
        )
    else:
        sys.argv = command
        runpy.run_path(command[0], run_name="__main__")
    # Не снимаем таймер здесь: зависание может начаться в Python shutdown.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
