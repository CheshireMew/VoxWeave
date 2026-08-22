from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import Settings, configure_process_environment, load_settings
from .controller import Controller
from .discovery import ServiceLock, reserve_loopback_socket, write_discovery
from .logging_setup import configure_logging
from .protocol import (
    PROTOCOL,
    PROTOCOL_VERSION,
    failure,
    public_error_code,
    success,
    validate_execute_result,
)

LOGGER = logging.getLogger(__name__)


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: str = PROTOCOL
    version: int = PROTOCOL_VERSION
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

    app = FastAPI(title="VoxWeave", version=__version__, lifespan=lifespan)
    app.state.controller = controller
    app.state.token = token

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        _request: Any, error: RequestValidationError
    ) -> JSONResponse:
        envelope = failure(None, "invalid_request", str(error.errors()))
        return JSONResponse(status_code=422, content=envelope)

    @app.exception_handler(HTTPException)
    async def http_error(_request: Any, error: HTTPException) -> JSONResponse:
        error_type = "unauthorized" if error.status_code == 401 else "http_error"
        envelope = failure(None, error_type, str(error.detail))
        return JSONResponse(status_code=error.status_code, content=envelope)

    def authorize(authorization: str | None) -> None:
        expected = app.state.token
        if expected and authorization != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="invalid local service token")

    @app.get("/v1/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "protocol": PROTOCOL,
            "version": PROTOCOL_VERSION,
            "pid": os.getpid(),
        }

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
            "protocol": PROTOCOL,
            "version": PROTOCOL_VERSION,
            "product_version": __version__,
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
        if request.protocol != PROTOCOL or request.version != PROTOCOL_VERSION:
            return failure(request.request_id, "protocol_mismatch", "expected voxweave-control v1")
        started = time.perf_counter()
        try:
            result = controller.execute(
                request.operation,
                request.arguments,
                request_id=request.request_id,
                actor=request.actor,
            )
            result = validate_execute_result(request.operation, result)
        except Exception as exc:  # noqa: BLE001 - public operation boundary
            error_type = public_error_code(exc)
            LOGGER.warning(
                "operation failed",
                extra={
                    "request_id": request.request_id,
                    "operation": request.operation,
                    "error_type": error_type,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
                exc_info=error_type == "operation_failed",
            )
            return failure(request.request_id, error_type, str(exc))
        LOGGER.info(
            "operation completed",
            extra={
                "request_id": request.request_id,
                "operation": request.operation,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
        return success(request.request_id, result)

    @app.get("/v1/tasks/{task_id}/events")
    def task_events(
        task_id: str,
        after_id: int = 0,
        limit: int = 500,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        return {
            "events": controller.task_events(task_id, after_id, max(1, min(limit, 2000)))
        }

    @app.websocket("/v1/events")
    async def events(websocket: WebSocket) -> None:
        if app.state.token and websocket.query_params.get("token") != app.state.token:
            await websocket.close(code=4401)
            return
        await websocket.accept()
        task_id = websocket.query_params.get("task_id")
        try:
            after_id = max(0, int(websocket.query_params.get("after_id", "0")))
        except ValueError:
            await websocket.close(code=4400)
            return
        try:
            while True:
                rows = await asyncio.to_thread(
                    controller.task_events if task_id else controller.all_task_events,
                    *((task_id, after_id) if task_id else (after_id, 500)),
                )
                for row in rows:
                    after_id = max(after_id, int(row["id"]))
                    await websocket.send_json(row)
                if len(rows) >= 500:
                    continue
                try:
                    message = await asyncio.wait_for(websocket.receive(), timeout=0.25)
                except TimeoutError:
                    continue
                if message["type"] == "websocket.disconnect":
                    return
        except WebSocketDisconnect:
            return

    return app


def main() -> int:
    settings = load_settings()
    configure_process_environment(settings)
    configure_logging(settings)
    LOGGER.info("service starting", extra={"operation": "service.start"})
    lock = ServiceLock(settings.lock_path)
    lock.acquire()
    server_socket = None
    server: uvicorn.Server | None = None
    try:
        requested_port = int(os.environ.get("VOXWEAVE_PORT", "0"))
        server_socket = reserve_loopback_socket(requested_port)
        port = int(server_socket.getsockname()[1])
        discovery = write_discovery(settings, port)

        def request_shutdown() -> None:
            if server:
                server.should_exit = True

        app = create_app(settings, discovery.token, request_shutdown)
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="info",
            log_config=None,
            access_log=False,
        )
        server = uvicorn.Server(config)
        server.run(sockets=[server_socket])
    finally:
        if server_socket:
            server_socket.close()
        LOGGER.info("service stopped", extra={"operation": "service.stop"})
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
