from __future__ import annotations

import threading
import uuid
from collections.abc import Callable
from typing import Any

from .database import Database
from .model_registry import ModelRegistry
from .realtime_request import RealtimeRequestBuilder
from .realtime_session_state import RealtimeSessionState
from .realtime_worker_controller import RealtimeWorkerController, WorkerExit
from .rvc_engine import RvcEngine

STOP_TIMEOUT_SECONDS = 10.0


class RealtimeSessionManager:
    """Coordinates resource arbitration around focused realtime services."""

    def __init__(
        self,
        database: Database,
        models: ModelRegistry,
        engine: RvcEngine,
        pause_offline_dispatch: Callable[[str], bool] | None = None,
        resume_offline_dispatch: Callable[[str], None] | None = None,
        release_offline_resources: Callable[[], None] | None = None,
    ) -> None:
        self.requests = RealtimeRequestBuilder(models, engine)
        self.sessions = RealtimeSessionState(database, models)
        self.pause_offline_dispatch = pause_offline_dispatch or (lambda _reason: True)
        self.resume_offline_dispatch = resume_offline_dispatch or (lambda _reason: None)
        self.release_offline_resources = release_offline_resources or (lambda: None)
        self._lock = threading.RLock()
        self._control_lock = threading.RLock()
        self._service_stopping = False
        self._offline_dispatch_paused = False
        self.worker = RealtimeWorkerController(
            engine,
            self._handle_worker_event,
            self._handle_worker_exit,
            self.release,
        )

    def devices(self) -> dict[str, Any]:
        return self.requests.devices()

    def audio_test(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            with self._lock:
                if self._service_stopping:
                    raise RuntimeError("realtime service is stopping")
                worker_state = self.worker.status()["state"]
                if self.sessions.active() or worker_state in {"starting", "warming"}:
                    raise RuntimeError(
                        "audio devices cannot be tested while realtime is active or preparing"
                    )
            return self.requests.audio_test(arguments)

    def prepare(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            model, _normalized, worker_command = self.requests.worker_command(arguments)
            with self._lock:
                if self._service_stopping:
                    raise RuntimeError("realtime manager is shutting down")
                if self.sessions.active():
                    return self.status()
                cache_key = str(worker_command["cache_key"])
                if self.worker.is_ready(cache_key) or self.worker.is_preparing(cache_key):
                    return self.status()
                self._pause_offline_dispatch()
                try:
                    self.release_offline_resources()
                    self.worker.prepare(
                        worker_command,
                        prepare_id=str(uuid.uuid4()),
                        model_id=model["id"],
                        cache_key=cache_key,
                    )
                except Exception:
                    self._release_offline_dispatch()
                    raise
                return self.status()

    def start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with self._control_lock:
            arguments = dict(arguments)
            if arguments.get("recording") and not arguments.get("recording_directory"):
                directory = self.requests.engine.settings.artifacts_dir / "realtime"
                directory.mkdir(parents=True, exist_ok=True)
                arguments["recording_directory"] = str(directory.resolve())
            model, normalized, worker_command = self.requests.worker_command(arguments)
            session_id = str(uuid.uuid4())
            try:
                with self._lock:
                    if self._service_stopping:
                        raise RuntimeError("realtime manager is shutting down")
                    if self.sessions.active():
                        active = self.sessions.active()
                        raise RuntimeError(f"realtime session is already active: {active['id']}")
                    self._pause_offline_dispatch()
                    self.release_offline_resources()
                    self.sessions.begin(session_id, model, normalized)
                    try:
                        self.worker.start_session(
                            worker_command,
                            session_id=session_id,
                            model_id=model["id"],
                            cache_key=str(worker_command["cache_key"]),
                        )
                    except Exception as error:
                        self.sessions.fail_start(session_id, error)
                        raise
            except Exception:
                self._release_offline_dispatch()
                raise
            return self.get(session_id)

    def _pause_offline_dispatch(self) -> None:
        if self._offline_dispatch_paused:
            return
        if not self.pause_offline_dispatch("realtime"):
            raise RuntimeError("cannot use realtime while a background task is running")
        self._offline_dispatch_paused = True

    def _handle_worker_event(self, payload: dict[str, Any]) -> None:
        release_dispatch = False
        with self._lock:
            if payload.get("prepare_id"):
                release_dispatch = not payload.get("ok") and not self.worker.status()["model_ready"]
            else:
                result = self.sessions.handle_worker_event(payload)
                release_dispatch = (
                    result.session_ended
                    and not payload.get("ok")
                    and not self.worker.status()["model_ready"]
                )
        if release_dispatch:
            self._release_offline_dispatch()

    def _handle_worker_exit(self, worker_exit: WorkerExit) -> None:
        with self._lock:
            self.sessions.handle_worker_exit(worker_exit)
            release_dispatch = self._offline_dispatch_paused
        if release_dispatch:
            self._release_offline_dispatch()

    def _stop_timeout(self, session_id: str) -> None:
        if self.sessions.stop_timeout(session_id, STOP_TIMEOUT_SECONDS):
            self._release_offline_dispatch()

    def get(self, session_id: str) -> dict[str, Any]:
        result = self.sessions.get(session_id)
        result["worker"] = self.worker.status()
        return result

    def status(self) -> dict[str, Any]:
        result = self.sessions.status()
        result["worker"] = self.worker.status()
        return result

    def stop(self) -> dict[str, Any]:
        with self._control_lock:
            release_dispatch = False
            session_id = self.sessions.mark_stopping()
            if not session_id:
                return self.status()
            try:
                self.worker.request_stop(
                    session_id,
                    timeout_seconds=STOP_TIMEOUT_SECONDS,
                    on_timeout=self._stop_timeout,
                )
            except (BrokenPipeError, OSError, RuntimeError):
                self.sessions.worker_unavailable(session_id)
                release_dispatch = True
            result = self.get(session_id)
            if release_dispatch:
                self._release_offline_dispatch()
            return result

    def release(self) -> dict[str, Any]:
        with self._control_lock:
            if self.sessions.active():
                return self.status()
            self.worker.release()
            self._release_offline_dispatch()
            return self.status()

    def _release_offline_dispatch(self) -> None:
        with self._lock:
            if not self._offline_dispatch_paused:
                return
            self._offline_dispatch_paused = False
        self.resume_offline_dispatch("realtime")

    def events(self, session_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        return self.sessions.events(session_id, after_id)

    def shutdown(self) -> None:
        with self._control_lock:
            with self._lock:
                self._service_stopping = True
                self.sessions.shutdown()
            self._release_offline_dispatch()
            self.worker.shutdown()
