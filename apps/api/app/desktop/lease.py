"""OS-held lifetime lock: two desktop engines must not own the same journal."""

import os
from pathlib import Path


class WorkspaceLease:
    def __init__(self, directory: Path):
        self.descriptor = None
        path = directory / ".desktop-runtime.lock"
        descriptor = os.open(
            path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
        )
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            os.close(descriptor)
            raise RuntimeError("Green Atlas уже работает с этим хранилищем") from error
        self.descriptor = descriptor

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
