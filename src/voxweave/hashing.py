from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO


def _stat_identity(file_stat: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


@contextlib.contextmanager
def _stable_reader(path: Path) -> Iterator[BinaryIO]:
    """Open a file without permitting concurrent writers on Windows."""

    if os.name != "nt":
        with path.open("rb") as source:
            yield source
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = (
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    )
    create_file.restype = wintypes.HANDLE
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(path),
        0x80000000,  # GENERIC_READ
        0x00000001,  # FILE_SHARE_READ: writers and replacement remain blocked
        None,
        3,  # OPEN_EXISTING
        0x08000000,  # FILE_FLAG_SEQUENTIAL_SCAN
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value
    if handle == invalid_handle:
        raise ctypes.WinError(
            ctypes.get_last_error(), f"cannot open file for stable hashing: {path}"
        )
    try:
        descriptor = msvcrt.open_osfhandle(int(handle), os.O_RDONLY | os.O_BINARY)
    except Exception:
        close_handle(handle)
        raise
    with os.fdopen(descriptor, "rb", closefd=True) as source:
        yield source


def sha256_file(
    path: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Return a content SHA-256 read from a stable file identity.

    Content hashes are authoritative state in VoxWeave. They are deliberately
    never reused from path or timestamp metadata.
    """

    path = path.expanduser().resolve()
    if cancelled and cancelled():
        raise InterruptedError("file hashing cancelled")
    digest = hashlib.sha256()
    with _stable_reader(path) as source:
        before = _stat_identity(os.fstat(source.fileno()))
        while chunk := source.read(1024 * 1024):
            if cancelled and cancelled():
                raise InterruptedError("file hashing cancelled")
            digest.update(chunk)
        after = _stat_identity(os.fstat(source.fileno()))
        path_after = _stat_identity(path.stat())
        if before != after or after[:4] != path_after[:4]:
            raise OSError(f"file changed while hashing: {path}")
    return digest.hexdigest()
