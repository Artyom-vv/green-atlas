"""Monitor and stop an owned converter process together with its descendants."""

import psutil


class ProcessTree:
    def __init__(self, pid: int) -> None:
        self._processes: dict[int, psutil.Process] = {}
        self._root: psutil.Process | None = None
        try:
            self._root = psutil.Process(pid)
            self._processes[pid] = self._root
        except psutil.NoSuchProcess:
            self._root = None

    def resident_bytes(self) -> int:
        if self._root is not None:
            try:
                for child in self._root.children(recursive=True):
                    self._processes[child.pid] = child
            except psutil.NoSuchProcess:
                pass
        total = 0
        for process in tuple(self._processes.values()):
            try:
                total += process.memory_info().rss
            except psutil.NoSuchProcess:
                self._processes.pop(process.pid, None)
        return total

    def stop(self) -> None:
        # Refresh descendants while their parent is still alive, then stop
        # children first. A Windows venv Python launcher is not the parser.
        self.resident_bytes()
        running = list(self._processes.values())
        for process in reversed(running):
            try:
                process.kill()
            except psutil.NoSuchProcess:
                pass
        psutil.wait_procs(running, timeout=5)
