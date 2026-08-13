from __future__ import annotations

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
    discovery = read_discovery(settings)
    if discovery and _handshake(discovery):
        return discovery
    with _service_start_lock:
        discovery = read_discovery(settings)
        if discovery and _handshake(discovery):
            return discovery
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
                return discovery
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
    request = urllib.request.Request(
        f"http://127.0.0.1:{discovery.port}{route}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {discovery.token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.URLError as exc:
        raise ServiceUnavailable(str(exc)) from exc


def shutdown_service(settings: Settings) -> dict[str, Any]:
    discovery = read_discovery(settings)
    if not discovery or not _handshake(discovery):
        return {"ok": True, "state": "stopped"}
    return _request_json(discovery, "POST", "/v1/shutdown")
