"""Имитировать Windows Job Object на Mac; выполняется только synthetic Core fixture."""

import subprocess

from test_windows_core_supervision import Child

from app.shared.windows_process import OwnedWindowsProcess


class CompletedOwnedChild(Child, OwnedWindowsProcess):
    def __init__(self, *, code=0, on_stop=lambda: None):
        Child.__init__(self, descendant=True)
        self.code = code
        self.on_stop = on_stop

    @property
    def returncode(self):
        return self.code

    @property
    def closed(self):
        return self.closes > 0

    def terminate_all(self):
        self.on_stop()
        Child.terminate_all(self)


def spawn_completed(command, job, *, on_stop=lambda _job: None):
    completed = subprocess.run(command, cwd=job, capture_output=True, timeout=5)
    return CompletedOwnedChild(code=completed.returncode, on_stop=lambda: on_stop(job))
