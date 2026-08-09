from __future__ import annotations

import json
import os
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

from .config import Settings
from .protocol import PROTOCOL, PROTOCOL_VERSION


def pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes

        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def reserve_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@dataclass(slots=True)
class Discovery:
    pid: int
    port: int
    token: str
    protocol: str
    protocol_version: int
    created_at: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "pid": self.pid,
            "port": self.port,
            "token": self.token,
            "protocol": self.protocol,
            "protocol_version": self.protocol_version,
            "created_at": self.created_at,
        }


class ServiceLock:
    def __init__(self, path: Path):
        self.path = path
        self.handle: IO[bytes] | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                if self.handle.tell() == 0:
                    self.handle.write(b"0")
                    self.handle.flush()
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("another VoxWeave service already owns the lock") from exc

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


def read_discovery(settings: Settings) -> Discovery | None:
    path = settings.discovery_path
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        discovery = Discovery(**payload)
    except (OSError, ValueError, TypeError):
        return None
    if discovery.protocol != PROTOCOL or discovery.protocol_version != PROTOCOL_VERSION:
        return None
    if not pid_is_alive(discovery.pid):
        return None
    return discovery


def write_discovery(settings: Settings, port: int) -> Discovery:
    settings.ensure_layout()
    discovery = Discovery(
        pid=os.getpid(),
        port=port,
        token=secrets.token_urlsafe(32),
        protocol=PROTOCOL,
        protocol_version=PROTOCOL_VERSION,
        created_at=time.time(),
    )
    temp = settings.discovery_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(discovery.as_dict(), indent=2) + "\n", encoding="utf-8")
    temp.replace(settings.discovery_path)
    return discovery
