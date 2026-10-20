"""Вход автономного runtime: worker выбирается до создания локального API.

Модуль запуска не владеет CAD-операциями или рабочими данными проекта.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    multiprocessing.freeze_support()
    from app.shared.python_worker import dispatch_worker

    if not dispatch_worker(sys.argv[1:]):
        from app.desktop.runtime import main

        raise SystemExit(main())
