from __future__ import annotations

import queue
import threading
import time
import uuid
from pathlib import Path


class WindowsDirectoryWatcher:
    """ReadDirectoryChangesW adapter with overflow recovery signalling."""

    def __init__(self, root: Path, recursive: bool) -> None:
        self.root = root.resolve()
        self.recursive = recursive
        self.changes: queue.Queue[Path] = queue.Queue(maxsize=8192)
        self.overflow = threading.Event()
        self.stop_event = threading.Event()
        self._handle: int | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"voxweave-directory-watch-{uuid.uuid4().hex[:8]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()

    def drain(self) -> tuple[list[Path], bool]:
        paths: list[Path] = []
        overflow = self.overflow.is_set()
        self.overflow.clear()
        while True:
            try:
                item = self.changes.get_nowait()
            except queue.Empty:
                break
            paths.append(item)
        return paths, overflow

    def _record(self, path: Path) -> None:
        try:
            self.changes.put_nowait(path)
        except queue.Full:
            self.overflow.set()

    def stop(self) -> None:
        self.stop_event.set()
        handle = self._handle
        if handle not in {None, -1}:
            import ctypes
            from ctypes import wintypes

            cancel_io = ctypes.WinDLL("kernel32", use_last_error=True).CancelIoEx
            cancel_io.argtypes = (wintypes.HANDLE, wintypes.LPVOID)
            cancel_io.restype = wintypes.BOOL
            cancel_io(wintypes.HANDLE(handle), None)
        self._thread.join(timeout=2)
        if self._thread.is_alive():
            raise RuntimeError(f"directory watcher did not stop: {self.root}")

    def _run(self) -> None:
        import ctypes
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
        read_changes = kernel32.ReadDirectoryChangesW
        read_changes.argtypes = (
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
            wintypes.LPDWORD,
            wintypes.LPVOID,
            wintypes.LPVOID,
        )
        read_changes.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        handle = create_file(
            str(self.root),
            0x0001,  # FILE_LIST_DIRECTORY
            0x00000001 | 0x00000002 | 0x00000004,
            None,
            3,  # OPEN_EXISTING
            0x02000000,  # FILE_FLAG_BACKUP_SEMANTICS
            None,
        )
        invalid_handle = ctypes.c_void_p(-1).value
        if handle == invalid_handle:
            self.overflow.set()
            return
        self._handle = int(handle)
        buffer = ctypes.create_string_buffer(64 * 1024)
        returned = wintypes.DWORD()
        notify_filter = 0x00000001 | 0x00000002 | 0x00000008 | 0x00000010 | 0x00000040
        try:
            while not self.stop_event.is_set():
                ok = read_changes(
                    handle,
                    buffer,
                    len(buffer),
                    self.recursive,
                    notify_filter,
                    ctypes.byref(returned),
                    None,
                    None,
                )
                if not ok:
                    error = ctypes.get_last_error()
                    if self.stop_event.is_set() or error == 995:  # ERROR_OPERATION_ABORTED
                        return
                    self.overflow.set()
                    time.sleep(0.25)
                    continue
                if returned.value == 0:
                    self.overflow.set()
                    continue
                offset = 0
                data = buffer.raw[: returned.value]
                while offset + 12 <= len(data):
                    next_offset = int.from_bytes(data[offset : offset + 4], "little")
                    name_length = int.from_bytes(data[offset + 8 : offset + 12], "little")
                    name = data[offset + 12 : offset + 12 + name_length].decode(
                        "utf-16-le", errors="replace"
                    )
                    self._record(self.root / name)
                    if next_offset == 0:
                        break
                    offset += next_offset
        finally:
            self._handle = None
            close_handle(handle)
