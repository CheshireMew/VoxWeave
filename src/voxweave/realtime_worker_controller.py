from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .realtime_worker_transport import RealtimeWorkerTransport, TransportExit
from .rvc_engine import RvcEngine

WORKER_START_TIMEOUT_SECONDS = 20.0
MODEL_WARM_TIMEOUT_SECONDS = 120.0
REALTIME_IDLE_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class WorkerExit:
    session_id: str | None
    return_code: int
    last_failure: dict[str, Any] | None
    read_error: Exception | None
    stderr: str


class RealtimeWorkerController:
    """Owns resident model state, operation correlation and worker deadlines."""

    def __init__(
        self,
        engine: RvcEngine,
        on_event: Callable[[dict[str, Any]], None],
        on_exit: Callable[[WorkerExit], None],
        on_idle_release: Callable[[], None],
    ) -> None:
        self.on_event = on_event
        self.on_exit = on_exit
        self.on_idle_release = on_idle_release
        self._lock = threading.RLock()
        self._transport = RealtimeWorkerTransport(
            engine,
            self._handle_payload,
            self._handle_transport_exit,
        )
        self._stop_watchdog: threading.Thread | None = None
        self._worker_deadline_timer: threading.Timer | None = None
        self._warm_deadline_timer: threading.Timer | None = None
        self._idle_release_timer: threading.Timer | None = None
        self._generation: int | None = None
        self._state = "not_started"
        self._model_id: str | None = None
        self._model_ready = False
        self._prepared_key: str | None = None
        self._prepare_id: str | None = None
        self._preparing_key: str | None = None
        self._active_session_id: str | None = None
        self._stopping = False

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self._state,
                "pid": self._transport.status()["pid"],
                "model_id": self._model_id,
                "model_ready": self._model_ready,
            }

    def is_ready(self, cache_key: str) -> bool:
        with self._lock:
            return self._model_ready and self._prepared_key == cache_key

    def is_preparing(self, cache_key: str) -> bool:
        with self._lock:
            return bool(self._prepare_id and self._preparing_key == cache_key)

    def prepare(
        self,
        command: dict[str, Any],
        *,
        prepare_id: str,
        model_id: str,
        cache_key: str,
    ) -> None:
        with self._lock:
            self._cancel_timer_locked("_idle_release_timer")
            generation = self._ensure_started_locked()
            self._prepare_id = prepare_id
            self._preparing_key = cache_key
            self._state = "warming"
            self._model_id = model_id
            self._model_ready = False
            self._prepared_key = None
            try:
                self._transport.send(
                    {**command, "command": "prepare", "prepare_id": prepare_id}
                )
                self._arm_warm_deadline_locked(generation, prepare_id=prepare_id)
            except Exception:
                self._fail_operation_locked(generation)
                raise

    def start_session(
        self,
        command: dict[str, Any],
        *,
        session_id: str,
        model_id: str,
        cache_key: str,
    ) -> None:
        with self._lock:
            self._cancel_timer_locked("_idle_release_timer")
            generation = self._ensure_started_locked()
            self._active_session_id = session_id
            if not (self._model_ready and self._prepared_key == cache_key):
                self._state = "warming"
                self._model_id = model_id
                self._model_ready = False
                self._prepared_key = None
            try:
                self._transport.send({**command, "session_id": session_id})
                if not self._model_ready:
                    self._arm_warm_deadline_locked(generation, session_id=session_id)
            except Exception:
                self._fail_operation_locked(generation)
                raise

    def request_stop(
        self,
        session_id: str,
        *,
        timeout_seconds: float,
        on_timeout: Callable[[str], None],
    ) -> None:
        with self._lock:
            generation = self._generation
            if generation is None or not self._transport.is_active(generation):
                raise RuntimeError("resident realtime worker is unavailable")
            try:
                self._transport.send({"command": "stop", "session_id": session_id})
            except (BrokenPipeError, OSError):
                self._transport.terminate(generation)
                raise
            if self._stop_watchdog and self._stop_watchdog.is_alive():
                return

            def enforce_deadline() -> None:
                deadline = time.monotonic() + timeout_seconds
                while time.monotonic() < deadline:
                    with self._lock:
                        active = self._active_session_id == session_id
                    if not self._transport.is_active(generation) or not active:
                        return
                    time.sleep(0.1)
                on_timeout(session_id)
                self._transport.terminate(generation)

            self._stop_watchdog = threading.Thread(
                target=enforce_deadline,
                name="voxweave-realtime-stop-watchdog",
                daemon=True,
            )
            self._stop_watchdog.start()

    def control(self, session_id: str, changes: dict[str, Any]) -> None:
        with self._lock:
            generation = self._generation
            if (
                generation is None
                or not self._transport.is_active(generation)
                or self._active_session_id != session_id
            ):
                raise RuntimeError("active realtime worker is unavailable")
            self._transport.send(
                {"command": "control", "session_id": session_id, **changes}
            )

    def release(self) -> None:
        with self._lock:
            self._cancel_all_timers_locked()
            self._prepare_id = None
            self._preparing_key = None
            self._prepared_key = None
            self._model_ready = False
            self._model_id = None
        self._transport.close(timeout=5, action="release")
        with self._lock:
            self._generation = None
            self._state = "not_started"

    def shutdown(self) -> None:
        with self._lock:
            self._stopping = True
            self._cancel_all_timers_locked()
        self._transport.close(timeout=15, action="shutdown")

    def _ensure_started_locked(self) -> int:
        generation = self._generation
        if generation is not None and self._transport.is_active(generation):
            return generation
        generation = self._transport.start()
        self._generation = generation
        self._state = "starting"
        self._model_id = None
        self._model_ready = False
        self._prepared_key = None
        self._prepare_id = None
        self._preparing_key = None
        self._arm_worker_deadline_locked(generation)
        return generation

    def _fail_operation_locked(self, generation: int) -> None:
        self._state = "failed"
        self._prepare_id = None
        self._preparing_key = None
        self._active_session_id = None
        self._cancel_timer_locked("_warm_deadline_timer")
        self._transport.terminate(generation)

    def _cancel_timer_locked(self, name: str) -> None:
        timer = getattr(self, name)
        setattr(self, name, None)
        if timer:
            timer.cancel()

    def _cancel_all_timers_locked(self) -> None:
        for name in (
            "_worker_deadline_timer",
            "_warm_deadline_timer",
            "_idle_release_timer",
        ):
            self._cancel_timer_locked(name)

    def _arm_worker_deadline_locked(self, generation: int) -> None:
        self._cancel_timer_locked("_worker_deadline_timer")

        def expired() -> None:
            with self._lock:
                if generation != self._generation or self._state != "starting":
                    return
                event = {
                    "ok": False,
                    "event": "startup_timeout",
                    "session_id": self._active_session_id,
                    "prepare_id": self._prepare_id,
                    "error_type": "startup_timeout",
                    "error": (
                        "resident realtime worker did not start within "
                        f"{WORKER_START_TIMEOUT_SECONDS:g} seconds"
                    ),
                }
                self._fail_operation_locked(generation)
            self.on_event(event)

        timer = threading.Timer(WORKER_START_TIMEOUT_SECONDS, expired)
        timer.daemon = True
        self._worker_deadline_timer = timer
        timer.start()

    def _arm_warm_deadline_locked(
        self,
        generation: int,
        *,
        prepare_id: str | None = None,
        session_id: str | None = None,
    ) -> None:
        self._cancel_timer_locked("_warm_deadline_timer")

        def expired() -> None:
            with self._lock:
                preparing = prepare_id is not None and self._prepare_id == prepare_id
                starting = session_id is not None and self._active_session_id == session_id
                if (
                    generation != self._generation
                    or self._model_ready
                    or not (preparing or starting)
                ):
                    return
                event = {
                    "ok": False,
                    "event": "warmup_timeout",
                    "session_id": session_id,
                    "prepare_id": prepare_id,
                    "error_type": "warmup_timeout",
                    "error": (
                        "realtime model did not warm within "
                        f"{MODEL_WARM_TIMEOUT_SECONDS:g} seconds"
                    ),
                }
                self._fail_operation_locked(generation)
            self.on_event(event)

        timer = threading.Timer(MODEL_WARM_TIMEOUT_SECONDS, expired)
        timer.daemon = True
        self._warm_deadline_timer = timer
        timer.start()

    def _schedule_idle_release_locked(self) -> None:
        self._cancel_timer_locked("_idle_release_timer")
        if self._stopping or self._active_session_id or self._generation is None:
            return

        def release_idle() -> None:
            with self._lock:
                if self._active_session_id or self._stopping:
                    return
            self.on_idle_release()

        timer = threading.Timer(REALTIME_IDLE_SECONDS, release_idle)
        timer.daemon = True
        self._idle_release_timer = timer
        timer.start()

    def _handle_payload(self, generation: int, payload: dict[str, Any]) -> None:
        forward = False
        with self._lock:
            if generation != self._generation:
                return
            event = payload.get("event")
            if event == "worker_started":
                self._cancel_timer_locked("_worker_deadline_timer")
                if self._state == "starting":
                    self._state = "idle"
                return
            if event == "worker_stopped":
                return
            prepare_id = str(payload.get("prepare_id") or "")
            if prepare_id:
                if prepare_id != self._prepare_id:
                    return
                self._model_id = str(payload.get("model_id") or "") or None
                if not payload.get("ok"):
                    self._operation_failed_payload_locked(payload)
                elif event == "warming":
                    self._state = "warming"
                    self._model_ready = False
                    self._prepared_key = None
                elif event == "ready":
                    self._cancel_timer_locked("_warm_deadline_timer")
                    self._state = "ready"
                    self._model_ready = True
                    self._prepared_key = str(payload.get("cache_key") or "") or None
                    self._prepare_id = None
                    self._preparing_key = None
                    self._schedule_idle_release_locked()
                forward = True
            else:
                session_id = str(payload.get("session_id") or "")
                if not session_id or session_id != self._active_session_id:
                    return
                if not payload.get("ok"):
                    self._operation_failed_payload_locked(payload)
                    self._active_session_id = None
                    if self._model_ready:
                        self._schedule_idle_release_locked()
                elif event == "warming":
                    self._state = "warming"
                    self._model_id = str(payload.get("model_id") or "") or None
                    self._model_ready = False
                    self._prepared_key = None
                elif event == "ready":
                    self._cancel_timer_locked("_warm_deadline_timer")
                    self._state = "ready"
                    self._model_id = str(payload.get("model_id") or "") or None
                    self._model_ready = True
                    self._prepared_key = str(payload.get("cache_key") or "") or None
                elif event == "stopped":
                    self._active_session_id = None
                    self._schedule_idle_release_locked()
                forward = True
        if forward:
            self.on_event(payload)

    def _operation_failed_payload_locked(self, payload: dict[str, Any]) -> None:
        self._cancel_timer_locked("_warm_deadline_timer")
        self._model_ready = bool(payload.get("model_ready"))
        self._state = "ready" if self._model_ready else "failed"
        self._prepared_key = (
            str(payload["cache_key"])
            if self._model_ready and payload.get("cache_key")
            else None
        )
        self._prepare_id = None
        self._preparing_key = None

    def _handle_transport_exit(
        self,
        generation: int,
        transport_exit: TransportExit,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            session_id = self._active_session_id
            self._generation = None
            self._cancel_all_timers_locked()
            self._state = "not_started"
            self._model_id = None
            self._model_ready = False
            self._prepared_key = None
            self._prepare_id = None
            self._preparing_key = None
            self._active_session_id = None
        self.on_exit(
            WorkerExit(
                session_id=session_id,
                return_code=transport_exit.return_code,
                last_failure=transport_exit.last_failure,
                read_error=transport_exit.read_error,
                stderr=transport_exit.stderr,
            )
        )
