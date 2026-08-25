from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

from .database import Database
from .model_registry import ModelRegistry
from .protocol import public_error_code
from .realtime_recording_manifest import RealtimeRecordingManifestService
from .realtime_repository import RealtimeRepository
from .realtime_worker_controller import WorkerExit

ACTIVE_STATES = {"starting", "running", "stopping"}
TERMINAL_STATES = {"stopped", "failed", "interrupted"}
LIFECYCLE_STATES = ACTIVE_STATES | TERMINAL_STATES


@dataclass(frozen=True, slots=True)
class WorkerEventResult:
    handled: bool
    session_ended: bool = False


class RealtimeSessionState:
    """Owns persisted realtime lifecycle state and model snapshot verification."""

    def __init__(self, database: Database, models: ModelRegistry) -> None:
        self.repository = RealtimeRepository(database)
        self.models = models
        self._lock = threading.RLock()
        self._active_id: str | None = None
        self._active_model: dict[str, Any] | None = None
        self._service_stopping = False
        self._last_heartbeat_at = 0.0
        self._last_overloaded: bool | None = None
        self.repository.recover_interrupted()

    def active(self) -> dict[str, Any] | None:
        return self.repository.active()

    def begin(
        self,
        session_id: str,
        model: dict[str, Any],
        arguments: dict[str, Any],
    ) -> None:
        with self._lock:
            if self._service_stopping:
                raise RuntimeError("realtime manager is shutting down")
            active = self.repository.active()
            if active:
                raise RuntimeError(f"realtime session is already active: {active['id']}")
            self._last_heartbeat_at = 0.0
            self._last_overloaded = None
            self.repository.create(session_id, model, arguments)
            self._active_id = session_id
            self._active_model = model

    def fail_start(self, session_id: str, error: Exception) -> None:
        with self._lock:
            self._transition(
                session_id,
                state="failed",
                stage="failed",
                error_type=public_error_code(error),
                error=str(error),
            )
            self._clear_active()

    def mark_stopping(self) -> str | None:
        with self._lock:
            active = self.repository.active()
            if not active:
                return None
            session_id = str(active["id"])
            current = self.repository.get(session_id)
            if current["state"] != "stopping":
                self._transition(
                    session_id,
                    state="stopping",
                    stage="stopping",
                    detail="realtime stop requested",
                )
            return session_id

    def worker_unavailable(self, session_id: str) -> None:
        with self._lock:
            self._transition(
                session_id,
                state="failed",
                stage="failed",
                error_type="worker_unavailable",
                error="resident realtime worker is unavailable",
            )
            self._clear_active()

    def stop_timeout(self, session_id: str, timeout_seconds: float) -> bool:
        with self._lock:
            current = self.repository.get(session_id)
            if current["state"] != "stopping":
                return False
            self._transition(
                session_id,
                state="failed",
                stage="stop_timeout",
                error_type="stop_timeout",
                error=(f"realtime audio stream did not stop within {timeout_seconds:g} seconds"),
            )
            self._clear_active()
            return True

    def handle_worker_event(self, payload: dict[str, Any]) -> WorkerEventResult:
        with self._lock:
            session_id = str(payload.get("session_id") or "")
            if not session_id or session_id != self._active_id:
                return WorkerEventResult(False)
            event = str(payload.get("event") or "")
            metrics = {
                key: value
                for key, value in payload.items()
                if key not in {"session_id", "model_id", "cache_key"}
            }
            if not payload.get("ok"):
                stage = event if event in {"startup_timeout", "warmup_timeout"} else "failed"
                self._transition(
                    session_id,
                    state="failed",
                    stage=stage,
                    error_type=payload.get("error_type") or "RvcRealtimeError",
                    error=payload.get("error") or "resident realtime worker failed",
                )
                self._clear_active()
                return WorkerEventResult(True, True)
            if event == "warming":
                self._transition(
                    session_id,
                    state="starting",
                    stage="warming",
                    detail="warming resident RVC model",
                    metrics=metrics,
                )
            elif event == "ready":
                self._transition(
                    session_id,
                    state="starting",
                    stage="ready",
                    detail="resident RVC model warmup completed",
                    metrics=metrics,
                )
            elif event == "running":
                self._transition(
                    session_id,
                    state="running",
                    stage="streaming",
                    detail="audio stream is active",
                    metrics=metrics,
                )
            elif event == "metrics":
                self._heartbeat(session_id, metrics)
            elif event == "control":
                self.repository.heartbeat(session_id, metrics)
            elif event == "stopped":
                self._finish_stopped(session_id, metrics)
                self._clear_active()
                return WorkerEventResult(True, True)
            return WorkerEventResult(True)

    def _finish_stopped(self, session_id: str, stopped_metrics: dict[str, Any]) -> None:
        current = self.repository.get(session_id)
        if current["state"] in TERMINAL_STATES:
            return
        try:
            if self._active_model:
                self.models.verify_snapshot(self._active_model)
        except Exception as error:  # noqa: BLE001 - persisted snapshot boundary
            self._transition(
                session_id,
                state="failed",
                stage="failed",
                error_type=public_error_code(error),
                error=str(error),
            )
        else:
            merged_metrics = {**(current.get("metrics") or {}), **stopped_metrics}
            manifest_path = RealtimeRecordingManifestService.for_database(
                self.repository.database
            ).finalize(current, merged_metrics)
            if manifest_path:
                merged_metrics["recording_manifest_path"] = manifest_path
            if self._service_stopping:
                self._transition(
                    session_id,
                    state="interrupted",
                    stage="service_shutdown",
                    error_type="service_shutdown",
                    error="service stopped during realtime session",
                    metrics=merged_metrics,
                )
            elif current["state"] == "stopping":
                self._transition(
                    session_id,
                    state="stopped",
                    stage="stopped",
                    metrics=merged_metrics,
                )
            else:
                self._transition(
                    session_id,
                    state="failed",
                    stage="failed",
                    error_type="stream_stopped",
                    error="realtime audio stream stopped unexpectedly",
                    metrics=merged_metrics,
                )

    def handle_worker_exit(self, worker_exit: WorkerExit) -> bool:
        with self._lock:
            session_id = worker_exit.session_id
            if not session_id or session_id != self._active_id:
                return False
            current = self.repository.get(session_id)
            if current["state"] not in TERMINAL_STATES:
                error = (
                    (worker_exit.last_failure or {}).get("error")
                    or (str(worker_exit.read_error) if worker_exit.read_error else None)
                    or worker_exit.stderr
                    or f"resident realtime worker exited with code {worker_exit.return_code}"
                )
                self._transition(
                    session_id,
                    state="interrupted" if self._service_stopping else "failed",
                    stage="service_shutdown" if self._service_stopping else "failed",
                    error_type=(
                        "service_shutdown"
                        if self._service_stopping
                        else (worker_exit.last_failure or {}).get("error_type")
                        or "RvcRealtimeError"
                    ),
                    error=(
                        "service stopped during realtime session"
                        if self._service_stopping
                        else error
                    ),
                )
            self._clear_active()
            return True

    def shutdown(self) -> None:
        with self._lock:
            self._service_stopping = True
            if not self._active_id:
                return
            self._transition(
                self._active_id,
                state="interrupted",
                stage="service_shutdown",
                error_type="service_shutdown",
                error="service stopped during realtime session",
            )
            self._clear_active()

    def get(self, session_id: str) -> dict[str, Any]:
        return self.repository.get(session_id)

    def status(self) -> dict[str, Any]:
        session_id = self.repository.latest_id()
        if not session_id:
            return {
                "session_id": None,
                "state": "idle",
                "stage": "idle",
                "metrics": {},
            }
        return self.repository.get(session_id)

    def events(self, session_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        return self.repository.events(session_id, after_id)

    def _heartbeat(self, session_id: str, metrics: dict[str, Any]) -> None:
        now = time.monotonic()
        overloaded = bool(metrics.get("overloaded"))
        if self._last_overloaded == overloaded and now - self._last_heartbeat_at < 1.0:
            return
        self.repository.heartbeat(session_id, metrics)
        self._last_heartbeat_at = now
        self._last_overloaded = overloaded

    def _clear_active(self) -> None:
        self._active_id = None
        self._active_model = None

    def _transition(
        self,
        session_id: str,
        *,
        state: str,
        stage: str,
        detail: str | None = None,
        metrics: dict[str, Any] | None = None,
        error_type: str | None = None,
        error: str | None = None,
    ) -> None:
        if state not in LIFECYCLE_STATES:
            raise ValueError(f"invalid realtime lifecycle state: {state}")
        self.repository.transition(
            session_id,
            state=state,
            stage=stage,
            detail=detail,
            metrics=metrics,
            error_type=error_type,
            error=error,
        )
