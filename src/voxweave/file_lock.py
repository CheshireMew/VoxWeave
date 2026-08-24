from __future__ import annotations

import os
from pathlib import Path
from typing import IO


class InterprocessFileLock:
    """One-byte advisory lock shared by every VoxWeave process."""

    def __init__(self, path: Path):
        self.path = path
        self.handle: IO[bytes] | None = None

    def acquire(self) -> None:
        if self.handle is not None:
            raise RuntimeError(f"lock is already held: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise RuntimeError(f"lock is already held: {self.path}") from exc
        self.handle = handle

    def release(self) -> None:
        handle = self.handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self.handle = None

    def __enter__(self) -> InterprocessFileLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()
