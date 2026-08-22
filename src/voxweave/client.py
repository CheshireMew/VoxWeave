from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from .app import SERVICE_ARGUMENT
from .config import Settings
from .discovery import Discovery, read_discovery
from .process_control import start_managed_process
from .protocol import PROTOCOL, PROTOCOL_VERSION


class ServiceUnavailable(RuntimeError):
    pass


_service_start_lock = threading.Lock()
_http_local = threading.local()


def service_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, SERVICE_ARGUMENT]
    return [sys.executable, "-m", "voxweave.service"]


def _handshake(discovery: Discovery) -> bool:
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{discovery.port}/v1/handshake",
            headers={"Authorization": f"Bearer {discovery.token}"},
        )
        with urllib.request.urlopen(request, timeout=1) as response:
            payload = json.load(response)
        return bool(
            payload.get("ok")
            and payload.get("pid") == discovery.pid
            and payload.get("protocol") == PROTOCOL
            and payload.get("version") == PROTOCOL_VERSION
        )
    except (OSError, ValueError, urllib.error.URLError):
        return False


def ensure_service(settings: Settings, timeout: float = 120) -> Discovery:
    discovery, _started = ensure_service_with_state(settings, timeout)
    return discovery


def ensure_service_with_state(settings: Settings, timeout: float = 120) -> tuple[Discovery, bool]:
    """Return the service discovery record and whether this call started it."""

    discovery = read_discovery(settings)
    if discovery and _handshake(discovery):
        return discovery, False
    with _service_start_lock:
        discovery = read_discovery(settings)
        if discovery and _handshake(discovery):
            return discovery, False
        command = service_command()
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "env": os.environ.copy(),
        }
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        startup_log = settings.logs_dir / "service-startup.log"
        with startup_log.open("ab") as log:
            process = start_managed_process(command, stdout=log, stderr=log, **kwargs)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            discovery = read_discovery(settings)
            if discovery and _handshake(discovery):
                return discovery, True
            if process.poll() is not None:
                break
            time.sleep(0.15)
        try:
            detail = startup_log.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            detail = ""
        message = "VoxWeave service did not become ready"
        if process.poll() is not None:
            message += f" (process exited with code {process.returncode})"
        if detail.strip():
            message += f": {detail.strip()}"
        raise ServiceUnavailable(message)


class ManagedServiceClient:
    """GUI transport that only shuts down a service it started itself."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._lock = threading.RLock()
        self._started_service = False
        self._closing = False
        self._discovery: Discovery | None = None

    @property
    def started_service(self) -> bool:
        with self._lock:
            return self._started_service

    def request(
        self,
        settings: Settings,
        method: str,
        route: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        discovery = self.ensure()
        try:
            return _request_json(discovery, method, route, payload)
        except ServiceUnavailable:
            with self._lock:
                self._discovery = None
            discovery = self.ensure()
            return _request_json(discovery, method, route, payload)

    def ensure(self) -> Discovery:
        with self._lock:
            if self._closing:
                raise ServiceUnavailable("service client is closed")
            if self._discovery is not None:
                return self._discovery
        discovery, started = ensure_service_with_state(self.settings)
        stop_after_start = False
        closing = False
        with self._lock:
            closing = self._closing
            if not closing:
                self._discovery = discovery
                if started:
                    self._started_service = True
            elif started:
                stop_after_start = True
        if stop_after_start:
            shutdown_service(self.settings)
        if closing:
            raise ServiceUnavailable("service client is closed")
        return discovery

    def shutdown_if_owned(self) -> dict[str, Any]:
        with self._lock:
            self._closing = True
            owned = self._started_service
            self._started_service = False
            self._discovery = None
        if not owned:
            return {"ok": True, "state": "retained"}
        return shutdown_service(self.settings)


def request_json(
    settings: Settings,
    method: str,
    route: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    discovery = ensure_service(settings)
    return _request_json(discovery, method, route, payload)


def _request_json(
    discovery: Discovery,
    method: str,
    route: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    key = (discovery.port, discovery.token)
    connection = getattr(_http_local, "connection", None)
    if connection is None or getattr(_http_local, "key", None) != key:
        if connection is not None:
            connection.close()
        connection = http.client.HTTPConnection("127.0.0.1", discovery.port, timeout=60)
        _http_local.connection = connection
        _http_local.key = key
    try:
        connection.request(
            method,
            route,
            body=data,
            headers={
                "Authorization": f"Bearer {discovery.token}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        body = response.read()
        if response.status >= 400:
            raise ServiceUnavailable(f"service returned HTTP {response.status}")
        return json.loads(body)
    except (OSError, ValueError, http.client.HTTPException) as exc:
        connection.close()
        _http_local.connection = None
        _http_local.key = None
        raise ServiceUnavailable(str(exc)) from exc


def shutdown_service(settings: Settings) -> dict[str, Any]:
    discovery = read_discovery(settings)
    if not discovery or not _handshake(discovery):
        return {"ok": True, "state": "stopped"}
    return _request_json(discovery, "POST", "/v1/shutdown")
