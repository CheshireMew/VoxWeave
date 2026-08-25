from __future__ import annotations

import json
import os
import secrets
import socket
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .file_lock import InterprocessFileLock
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


def reserve_loopback_socket(port: int = 0) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", port))
        sock.listen(socket.SOMAXCONN)
        sock.set_inheritable(False)
        return sock
    except Exception:
        sock.close()
        raise


@dataclass(slots=True)
class Discovery:
    pid: int
    port: int
    token: str
    protocol: str
    protocol_version: int
    created_at: float
    owner_token: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "pid": self.pid,
            "port": self.port,
            "token": self.token,
            "protocol": self.protocol,
            "protocol_version": self.protocol_version,
            "created_at": self.created_at,
        }
        if self.owner_token is not None:
            payload["owner_token"] = self.owner_token
        return payload


class ServiceLock(InterprocessFileLock):
    def acquire(self) -> None:
        try:
            super().acquire()
        except RuntimeError as exc:
            raise RuntimeError("another VoxWeave service already owns the lock") from exc


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
    if not 1 <= discovery.port <= 65535 or not discovery.token:
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
        owner_token=os.environ.get("VOXWEAVE_SERVICE_OWNER_TOKEN") or None,
    )
    temp = settings.discovery_path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(discovery.as_dict(), indent=2) + "\n", encoding="utf-8")
    temp.replace(settings.discovery_path)
    return discovery
