"""Small cross-process locks used to coordinate expensive repo operations."""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Literal, TextIO


class UpdateLock:
    """Non-blocking, whole-repository lock for memory/model operations.

    The CLI, hooks, and dashboard share it across update, sync, scan, lint,
    synthesis, and live structural reads.  A process-local mutex is
    insufficient because those entry points run in independent processes and
    can otherwise expose partially refreshed state or pay for the same page
    twice.
    """

    def __init__(self, root: Path, name: str = "update"):
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,30}", name):
            raise ValueError("lock name must be a short lowercase identifier")
        self.path = root / ".irag" / f"{name}.lock"
        self.fh: TextIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        self.fh = fh
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                if not fh.read(1):
                    fh.write("0")
                    fh.flush()
                fh.seek(0)
                locking = getattr(msvcrt, "locking")
                locking(fh.fileno(), getattr(msvcrt, "LK_NBLCK"), 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            self.fh = None
            return False
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        return True

    def release(self) -> None:
        fh = self.fh
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                fh.seek(0)
                locking = getattr(msvcrt, "locking")
                locking(fh.fileno(), getattr(msvcrt, "LK_UNLCK"), 1)
            else:
                import fcntl
                fcntl.flock(fh, fcntl.LOCK_UN)
        finally:
            fh.close()
            self.fh = None

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc) -> Literal[False]:
        self.release()
        return False
