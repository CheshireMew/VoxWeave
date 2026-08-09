from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from .config import Settings, configure_process_environment, load_settings
from .controller import Controller
from .discovery import ServiceLock, reserve_loopback_port, write_discovery
from .protocol import failure, success


def public_error_code(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "file_not_found"
    if isinstance(error, FileExistsError):
        return "target_exists"
    if isinstance(error, LookupError):
        return "not_found"
    if isinstance(error, ValueError):
        return "invalid_arguments"
    return "operation_failed"


class ExecuteRequest(BaseModel):
    protocol: str = "voxweave-control"
    version: int = 1
    operation: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    request_id: str | None = None
    actor: dict[str, Any] | None = None


def create_app(
    settings: Settings,
    token: str | None = None,
    shutdown_callback: Callable[[], None] | None = None,
) -> FastAPI:
    controller = Controller(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        controller.shutdown()

    app = FastAPI(title="VoxWeave", version="0.1.0", lifespan=lifespan)
    app.state.controller = controller
    app.state.token = token

    def authorize(authorization: str | None) -> None:
        expected = app.state.token
        if expected and authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="invalid local service token")

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "protocol": "voxweave-control", "version": 1, "pid": os.getpid()}

    @app.get("/v1/describe")
    def operation_describe(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(authorization)
        return controller.describe()

    @app.get("/v1/handshake")
    def handshake(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(authorization)
        return {
            "ok": True,
            "pid": os.getpid(),
            "protocol": "voxweave-control",
            "version": 1,
            "product_version": "0.1.0",
        }

    @app.post("/v1/shutdown")
    def shutdown(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(authorization)

        def stop_after_response() -> None:
            time.sleep(0.2)
            if shutdown_callback:
                shutdown_callback()

        threading.Thread(target=stop_after_response, daemon=True).start()
        return {"ok": True, "state": "stopping"}

    @app.post("/v1/execute")
    def execute(
        request: ExecuteRequest, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        authorize(authorization)
        if request.protocol != "voxweave-control" or request.version != 1:
            return failure(request.request_id, "protocol_mismatch", "expected voxweave-control v1")
        try:
            result = controller.execute(request.operation, request.arguments)
        except Exception as exc:  # noqa: BLE001 - public operation boundary
            return failure(request.request_id, public_error_code(exc), str(exc))
        return success(request.request_id, result)

    @app.get("/v1/tasks/{task_id}/events")
    def task_events(
        task_id: str,
        after_id: int = 0,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return {"events": controller.tasks.events(task_id, after_id)}

    @app.websocket("/v1/events")
    async def events(websocket: WebSocket) -> None:
        if app.state.token and websocket.query_params.get("token") != app.state.token:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        task_id = websocket.query_params.get("task_id")
        after_id = 0
        try:
            while True:
                if task_id:
                    rows = controller.tasks.events(task_id, after_id)
                    for row in rows:
                        after_id = max(after_id, int(row["id"]))
                        await websocket.send_json(row)
                await asyncio.sleep(0.25)
        except WebSocketDisconnect:
            return

    return app


def main() -> int:
    settings = load_settings()
    configure_process_environment(settings)
    lock = ServiceLock(settings.lock_path)
    lock.acquire()
    port = int(os.environ.get("VOXWEAVE_PORT", "0")) or reserve_loopback_port()
    discovery = write_discovery(settings, port)
    server: uvicorn.Server | None = None

    def request_shutdown() -> None:
        if server:
            server.should_exit = True

    app = create_app(settings, discovery.token, request_shutdown)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="info")
    server = uvicorn.Server(config)
    try:
        server.run()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
