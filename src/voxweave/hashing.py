from __future__ import annotations

import contextlib
import hashlib
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
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


@dataclass(frozen=True, slots=True)
class VerifiedFile:
    """A content digest bound to the exact file identity that was hashed."""

    path: Path
    sha256: str
    size_bytes: int
    identity: tuple[int, int, int, int, int]

    def assert_unchanged(self, path: Path | None = None) -> VerifiedFile:
        candidate = (path or self.path).expanduser().resolve()
        if (
            not candidate.is_file()
            or _stat_identity(candidate.stat())[:4] != self.identity[:4]
        ):
            raise OSError(f"verified file identity changed: {candidate}")
        return self if candidate == self.path else replace(self, path=candidate)

    def rebind(self, path: Path) -> VerifiedFile:
        """Bind the same verified file identity after an atomic rename."""

        return self.assert_unchanged(path)

    def record(self) -> dict[str, str | int]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }


class FileVerificationLedger:
    """Task-scoped verified identities; never a cross-task metadata cache."""

    def __init__(self) -> None:
        self._files: dict[Path, VerifiedFile] = {}

    def remember(self, verified: VerifiedFile) -> VerifiedFile:
        self._files[verified.path] = verified
        return verified

    def verify(
        self,
        path: Path,
        *,
        expected_sha256: str | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> VerifiedFile:
        resolved = path.expanduser().resolve()
        existing = self._files.get(resolved)
        if existing is not None:
            existing.assert_unchanged()
            if expected_sha256 and existing.sha256.casefold() != expected_sha256.casefold():
                raise ValueError(f"verified SHA-256 does not match for {resolved}")
            return existing
        return self.remember(
            verify_file(
                resolved,
                expected_sha256=expected_sha256,
                cancelled=cancelled,
            )
        )

    def accept_record(self, record: dict[str, object]) -> VerifiedFile:
        """Accept a digest emitted by a trusted child after checking its file identity."""

        path = Path(str(record["path"])).expanduser().resolve()
        identity_value = record.get("identity")
        if not isinstance(identity_value, list) or len(identity_value) != 5:
            return self.verify(path, expected_sha256=str(record["sha256"]))
        identity = tuple(int(value) for value in identity_value)
        if _stat_identity(path.stat()) != identity:
            raise OSError(f"child-verified file identity changed: {path}")
        verified = VerifiedFile(
            path,
            str(record["sha256"]),
            int(record["size_bytes"]),
            identity,
        )
        return self.remember(verified)

    def rebind(self, verified: VerifiedFile, path: Path) -> VerifiedFile:
        rebound = verified.rebind(path)
        self._files.pop(verified.path, None)
        return self.remember(rebound)


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


def verify_file(
    path: Path,
    *,
    expected_sha256: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> VerifiedFile:
    """Hash a stable file once and bind the digest to its OS file identity.

    The returned identity may only be reused inside the accepting operation.
    It is not a path/timestamp cache and it becomes invalid as soon as the file
    identity or metadata changes.
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
    value = digest.hexdigest()
    if expected_sha256 and value.casefold() != expected_sha256.casefold():
        raise ValueError(f"SHA-256 does not match for {path}")
    return VerifiedFile(path, value, before[2], before)


def bind_verified_digest(path: Path, sha256: str, size_bytes: int) -> VerifiedFile:
    """Bind a digest computed while exclusively writing a newly created file."""

    resolved = path.expanduser().resolve()
    identity = _stat_identity(resolved.stat())
    if identity[2] != size_bytes:
        raise OSError(f"written file size changed before digest binding: {resolved}")
    return VerifiedFile(resolved, sha256, size_bytes, identity)


def sha256_file(
    path: Path,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> str:
    """Return an authoritative SHA-256 from a stable file identity."""

    return verify_file(path, cancelled=cancelled).sha256
