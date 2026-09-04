"""Small cross-process locks used to coordinate expensive repo operations."""
from __future__ import annotations

import os
from pathlib import Path


class UpdateLock:
    """Non-blocking, whole-repository lock for an ``irag update`` sweep.

    Both the CLI and dashboard use this file lock.  A process-local mutex is
    insufficient because a dashboard button and a Stop hook commonly fire at
    the same time, selecting the same stale pages and paying for them twice.
    """

    def __init__(self, root: Path):
        self.path = root / ".irag" / "update.lock"
        self.fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                if not self.fh.read(1):
                    self.fh.write("0")
                    self.fh.flush()
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.fh.close()
            self.fh = None
            return False
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(str(os.getpid()))
        self.fh.flush()
        return True

    def release(self) -> None:
        if self.fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> bool:
        self.release()
        return False
