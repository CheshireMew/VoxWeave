from __future__ import annotations

import os
import socket
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from voxweave import client
from voxweave.config import Settings
from voxweave.discovery import Discovery, ServiceLock, reserve_loopback_socket
from voxweave.protocol import OPERATION_SPECS, PROTOCOL, PROTOCOL_VERSION


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
    monkeypatch.setattr(client, "start_managed_process", launch)
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _index: client.ensure_service(settings), range(8)))
    assert results == [discovery] * 8
    assert launch_count == 1


def test_service_ownership_requires_the_discovered_pid_to_match_spawned_pid(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    discovery = Discovery(
        pid=222,
        port=12345,
        token="token",
        protocol=PROTOCOL,
        protocol_version=PROTOCOL_VERSION,
        created_at=1.0,
    )
    reads = iter([None, None, discovery])

    class Process:
        pid = 111
        returncode = None

        @staticmethod
        def poll():
            return None

    monkeypatch.setattr(client, "read_discovery", lambda _settings: next(reads))
    monkeypatch.setattr(client, "_handshake", lambda _discovery: True)
    monkeypatch.setattr(client, "start_managed_process", lambda *_args, **_kwargs: Process())

    result, started = client.ensure_service_with_state(settings, timeout=1)

    assert result is discovery
    assert started is False


def test_frozen_service_uses_the_desktop_executable(monkeypatch) -> None:
    monkeypatch.setattr(client.sys, "frozen", True, raising=False)
    monkeypatch.setattr(client.sys, "executable", r"C:\Apps\VoxWeave\VoxWeave.exe")
    assert client.service_command() == [
        r"C:\Apps\VoxWeave\VoxWeave.exe",
        "--voxweave-service",
    ]


def test_managed_gui_client_only_stops_a_service_it_started(tmp_path, monkeypatch) -> None:
    settings = Settings(data_root=str(tmp_path))
    discovery = Discovery(
        pid=os.getpid(),
        port=12345,
        token="token",
        protocol=PROTOCOL,
        protocol_version=PROTOCOL_VERSION,
        created_at=1.0,
    )
    stopped = []
    monkeypatch.setattr(client, "ensure_service_with_state", lambda _settings: (discovery, True))
    monkeypatch.setattr(
        client,
        "_describe",
        lambda _discovery: {
            "protocol": PROTOCOL,
            "version": PROTOCOL_VERSION,
            "product_version": client.__version__,
            "operations": {name: {} for name in OPERATION_SPECS},
        },
    )
    monkeypatch.setattr(
        client, "shutdown_service", lambda _settings: stopped.append(True) or {"ok": True}
    )

    managed = client.ManagedServiceClient(settings)
    assert managed.ensure() is discovery
    assert managed.started_service is True
    managed.shutdown_if_owned()
    assert stopped == [True]

    monkeypatch.setattr(client, "ensure_service_with_state", lambda _settings: (discovery, False))
    external = client.ManagedServiceClient(settings)
    assert external.ensure() is discovery
    external.shutdown_if_owned()
    assert stopped == [True]


def test_managed_gui_client_cannot_restart_after_shutdown(tmp_path, monkeypatch) -> None:
    settings = Settings(data_root=str(tmp_path))
    starts: list[bool] = []
    managed = client.ManagedServiceClient(settings)
    managed.shutdown_if_owned()
    monkeypatch.setattr(
        client,
        "ensure_service_with_state",
        lambda _settings: starts.append(True),
    )

    with pytest.raises(client.ServiceUnavailable, match="closed"):
        managed.ensure()

    assert starts == []


def test_client_retries_only_safe_or_idempotent_requests(tmp_path, monkeypatch) -> None:
    settings = Settings(data_root=str(tmp_path))
    discovery = Discovery(
        pid=os.getpid(),
        port=12345,
        token="token",
        protocol=PROTOCOL,
        protocol_version=PROTOCOL_VERSION,
        created_at=1.0,
    )
    managed = client.ManagedServiceClient(settings)
    monkeypatch.setattr(managed, "ensure", lambda: discovery)
    attempts: list[tuple[str, str]] = []

    def flaky(_discovery, method, route, _payload=None):
        attempts.append((method, route))
        if len(attempts) == 1:
            raise client.ServiceUnavailable("connection reset")
        return {"ok": True}

    monkeypatch.setattr(client, "_request_json", flaky)
    assert managed.request(settings, "GET", "/v1/describe") == {"ok": True}
    assert len(attempts) == 2

    attempts.clear()
    with pytest.raises(client.ServiceUnavailable):
        managed.request(settings, "POST", "/v1/shutdown")
    assert len(attempts) == 1

    attempts.clear()
    result = managed.request(
        settings,
        "POST",
        "/v1/execute",
        {"request_id": "stable-request", "operation": "settings.update"},
    )
    assert result == {"ok": True}
    assert len(attempts) == 2
