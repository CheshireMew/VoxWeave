from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings
from .discovery import Discovery, read_discovery


class ServiceUnavailable(RuntimeError):
    pass


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
            and payload.get("protocol") == "voxweave-control"
            and payload.get("version") == 1
        )
    except (OSError, ValueError, urllib.error.URLError):
        return False


def ensure_service(settings: Settings, timeout: float = 120) -> Discovery:
    discovery = read_discovery(settings)
    if discovery and _handshake(discovery):
        return discovery
    command = [sys.executable, "-m", "voxweave.service"]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": os.environ.copy(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(command, **kwargs)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        discovery = read_discovery(settings)
        if discovery and _handshake(discovery):
            return discovery
        time.sleep(0.15)
    raise ServiceUnavailable("VoxWeave service did not become ready")


def request_json(
    settings: Settings,
    method: str,
    route: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    discovery = ensure_service(settings)
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
