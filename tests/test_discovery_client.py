from __future__ import annotations

import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from voxweave import client
from voxweave.config import Settings
from voxweave.discovery import Discovery, ServiceLock, reserve_loopback_socket
from voxweave.protocol import PROTOCOL, PROTOCOL_VERSION


def test_reserved_service_socket_keeps_the_selected_port_owned() -> None:
    reserved = reserve_loopback_socket()
    port = reserved.getsockname()[1]
    contender = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(OSError):
            contender.bind(("127.0.0.1", port))
    finally:
        contender.close()
        reserved.close()


def test_windows_lock_sentinel_does_not_grow_on_reacquire(tmp_path) -> None:
    path = tmp_path / "service.lock"
    lock = ServiceLock(path)
    for _ in range(4):
        lock.acquire()
        lock.release()
    assert path.read_bytes() == b"0"


def test_concurrent_clients_launch_only_one_service(tmp_path, monkeypatch) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    discovery = Discovery(
        pid=os.getpid(),
        port=12345,
        token="token",
        protocol=PROTOCOL,
        protocol_version=PROTOCOL_VERSION,
        created_at=1.0,
    )
    launch_count = 0
    launch_lock = threading.Lock()

    class Process:
        returncode = None

        @staticmethod
        def poll():
            return None

    def read(_settings):
        with launch_lock:
            return discovery if launch_count else None

    def launch(*_args, **_kwargs):
        nonlocal launch_count
        with launch_lock:
            launch_count += 1
        return Process()

    monkeypatch.setattr(client, "read_discovery", read)
    monkeypatch.setattr(client, "_handshake", lambda value: value is discovery)
    monkeypatch.setattr(client.subprocess, "Popen", launch)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: client.ensure_service(settings), range(8)))
    assert results == [discovery] * 8
    assert launch_count == 1
