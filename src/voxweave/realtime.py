from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from typing import Any

from .database import Database
from .model_registry import ModelRegistry
from .process_control import terminate_process_tree
from .protocol import public_error_code
from .realtime_repository import RealtimeRepository
from .rvc_engine import RvcEngine

LOGGER = logging.getLogger(__name__)

ACTIVE_STATES = {"starting", "running", "stopping"}
TERMINAL_STATES = {"stopped", "failed", "interrupted"}
LIFECYCLE_STATES = ACTIVE_STATES | TERMINAL_STATES
STOP_TIMEOUT_SECONDS = 10.0


class RealtimeSessionManager:
    def __init__(
        self,
        database: Database,
        models: ModelRegistry,
        engine: RvcEngine,
        pause_offline_dispatch: Callable[[str], bool] | None = None,
        resume_offline_dispatch: Callable[[str], None] | None = None,
    ) -> None:
        self.database = database
        self.repository = RealtimeRepository(database)
        self.models = models
        self.engine = engine
        self.pause_offline_dispatch = pause_offline_dispatch or (lambda _reason: True)
        self.resume_offline_dispatch = resume_offline_dispatch or (lambda _reason: None)
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._stop_watchdog: threading.Thread | None = None
        self._active_id: str | None = None
        self._active_model: dict[str, Any] | None = None
        self._worker_state = "not_started"
        self._worker_model_id: str | None = None
        self._model_ready = False
        self._prepared_key: str | None = None
        self._service_stopping = False
        self._offline_dispatch_paused = False
        self._recover_interrupted_sessions()

    def _recover_interrupted_sessions(self) -> None:
        self.repository.recover_interrupted()

    @staticmethod
    def _normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
        block_seconds = float(arguments.get("block_seconds", 0.5))
        if block_seconds not in {0.25, 0.5, 1.0}:
            raise ValueError("block_seconds must be 0.25, 0.5, or 1.0")
        values = {
            "input_device": int(arguments["input_device"]),
            "output_device": int(arguments["output_device"]),
            "pitch": int(arguments.get("pitch", 0)),
            "f0": str(arguments.get("f0", "rmvpe")),
            "index_rate": float(arguments.get("index_rate", 0.72)),
            "rms_mix_rate": float(arguments.get("rms_mix_rate", 0.25)),
            "protect": 0.33,
            "block_seconds": block_seconds,
            "crossfade_seconds": 0.05,
            "extra_seconds": 2.5,
            "vad_threshold": float(arguments.get("vad_threshold", 0.35)),
            "test_mode": bool(arguments.get("test_mode", False)),
        }
        if not 0.1 <= values["vad_threshold"] <= 0.9:
            raise ValueError("vad_threshold must be between 0.1 and 0.9")
        RvcEngine._parameters(values)
        return values

    def devices(self) -> dict[str, Any]:
        payload = self.engine.audio_devices()
        return {
            "hostapis": payload["hostapis"],
            "devices": payload["devices"],
            "default_input_device": payload["default_input_device"],
            "default_output_device": payload["default_output_device"],
        }

    def start(self, arguments: dict[str, Any]) -> dict[str, Any]:
        model = self.models.resolve_for_execution(arguments["model"])
        normalized = self._normalize_arguments(arguments)
        device_payload = self.devices()
        devices = {int(device["id"]): device for device in device_payload["devices"]}
        input_device = devices.get(normalized["input_device"])
        output_device = devices.get(normalized["output_device"])
        if not input_device or int(input_device["input_channels"]) < 1:
            raise ValueError(f"device is not an audio input: {normalized['input_device']}")
        if not output_device or int(output_device["output_channels"]) < 1:
            raise ValueError(f"device is not an audio output: {normalized['output_device']}")
        if input_device["hostapi_id"] != output_device["hostapi_id"]:
            raise ValueError("input and output devices must use the same Windows audio host API")
        normalized.update(
            input_device_name=input_device["name"],
            output_device_name=output_device["name"],
            input_device_sample_rate=int(input_device["default_sample_rate"]),
            output_device_sample_rate=int(output_device["default_sample_rate"]),
            hostapi=input_device["hostapi"],
        )
        normalized["model"] = model["id"]
        worker_command = self.engine.realtime_start_payload(model, normalized)
        session_id = str(uuid.uuid4())
        try:
            with self._lock:
                active = self.repository.active()
                if active:
                    raise RuntimeError(f"realtime session is already active: {active['id']}")
                if not self.pause_offline_dispatch("realtime"):
                    raise RuntimeError("cannot start realtime while a background task is running")
                self._offline_dispatch_paused = True
                self.repository.create(session_id, model, normalized)
                self._active_id = session_id
                self._active_model = model
                try:
                    self._ensure_worker_locked()
                    if not (
                        self._model_ready and self._prepared_key == worker_command["cache_key"]
                    ):
                        self._worker_state = "warming"
                        self._worker_model_id = model["id"]
                        self._model_ready = False
                        self._prepared_key = None
                    self._send_command_locked({**worker_command, "session_id": session_id})
                except Exception as exc:
                    self._update(
                        session_id,
                        state="failed",
                        stage="failed",
                        error_type=public_error_code(exc),
                        error=str(exc),
                    )
                    self._active_id = None
                    self._active_model = None
                    raise
        except Exception:
            self._release_offline_dispatch()
            raise
        return self.get(session_id)

    def _update(
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
        with self._lock:
            self.repository.transition(
                session_id,
                state=state,
                stage=stage,
                detail=detail,
                metrics=metrics,
                error_type=error_type,
                error=error,
            )

    def _ensure_worker_locked(self) -> None:
        if self._process and self._process.poll() is None:
            return
        command, entry = self.engine.realtime_worker_command()
        process = subprocess.Popen(
            command,
            cwd=entry.parent,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
            creationflags=self.engine.realtime_creation_flags(),
            start_new_session=os.name != "nt",
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            terminate_process_tree(process)
            raise RuntimeError("resident realtime worker pipes are unavailable")
        self._process = process
        self._worker_state = "starting"
        self._worker_model_id = None
        self._model_ready = False
        self._prepared_key = None
        self._stderr_tail.clear()
        self._stderr_thread = threading.Thread(
            target=self._read_stderr,
            args=(process,),
            name="voxweave-realtime-stderr",
            daemon=True,
        )
        self._reader_thread = threading.Thread(
            target=self._read_worker,
            args=(process,),
            name="voxweave-realtime-events",
            daemon=True,
        )
        self._stderr_thread.start()
        self._reader_thread.start()

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            value = line.rstrip()
            if value:
                self._stderr_tail.append(value)

    def _read_worker(self, process: subprocess.Popen[str]) -> None:
        last_failure: dict[str, Any] | None = None
        read_error: Exception | None = None
        try:
            if process.stdout is None:
                raise RuntimeError("resident realtime worker stdout is unavailable")
            for line in process.stdout:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if not payload.get("ok"):
                    last_failure = payload
                self._handle_worker_payload(process, payload)
        except Exception as exc:  # noqa: BLE001 - isolated process reader boundary
            read_error = exc
            LOGGER.exception("resident realtime worker reader failed")
            if process.poll() is None:
                terminate_process_tree(process)
        return_code = process.wait()
        stderr_thread = self._stderr_thread
        if stderr_thread:
            stderr_thread.join(timeout=1)
        self._handle_worker_exit(process, return_code, last_failure, read_error)

    @staticmethod
    def _event_metrics(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            key: value
            for key, value in payload.items()
            if key not in {"session_id", "model_id", "cache_key"}
        }

    def _handle_worker_payload(
        self, process: subprocess.Popen[str], payload: dict[str, Any]
    ) -> None:
        release_dispatch = False
        with self._lock:
            if process is not self._process:
                return
            event = payload.get("event")
            if event == "worker_started":
                if self._worker_state == "starting":
                    self._worker_state = "idle"
                return
            if event == "worker_stopped":
                return

            session_id = str(payload.get("session_id") or "")
            if not session_id or session_id != self._active_id:
                return
            metrics = self._event_metrics(payload)
            if not payload.get("ok"):
                self._model_ready = bool(payload.get("model_ready"))
                self._worker_state = "ready" if self._model_ready else "failed"
                self._prepared_key = (
                    str(payload["cache_key"])
                    if self._model_ready and payload.get("cache_key")
                    else None
                )
                self._update(
                    session_id,
                    state="failed",
                    stage="failed",
                    error_type=payload.get("error_type") or "RvcRealtimeError",
                    error=payload.get("error") or "resident realtime worker failed",
                )
                self._active_id = None
                self._active_model = None
                release_dispatch = True
            elif event == "warming":
                self._worker_state = "warming"
                self._worker_model_id = str(payload.get("model_id") or "") or None
                self._model_ready = False
                self._prepared_key = None
                self._update(
                    session_id,
                    state="starting",
                    stage="warming",
                    detail="warming resident RVC model",
                    metrics=metrics,
                )
            elif event == "ready":
                self._worker_state = "ready"
                self._worker_model_id = str(payload.get("model_id") or "") or None
                self._model_ready = True
                self._prepared_key = str(payload.get("cache_key") or "") or None
                self._update(
                    session_id,
                    state="starting",
                    stage="ready",
                    detail="resident RVC model warmup completed",
                    metrics=metrics,
                )
            elif event == "running":
                self._update(
                    session_id,
                    state="running",
                    stage="streaming",
                    detail="audio stream is active",
                    metrics=metrics,
                )
            elif event == "metrics":
                self._heartbeat(session_id, metrics)
            elif event == "stopped":
                current = self.repository.get(session_id)
                if current["state"] not in TERMINAL_STATES:
                    try:
                        if self._active_model:
                            self.models.verify_snapshot(self._active_model)
                    except Exception as exc:  # noqa: BLE001 - persisted snapshot boundary
                        self._update(
                            session_id,
                            state="failed",
                            stage="failed",
                            error_type=public_error_code(exc),
                            error=str(exc),
                        )
                    else:
                        if self._service_stopping:
                            self._update(
                                session_id,
                                state="interrupted",
                                stage="service_shutdown",
                                error_type="service_shutdown",
                                error="service stopped during realtime session",
                            )
                        elif current["state"] == "stopping":
                            self._update(session_id, state="stopped", stage="stopped")
                        else:
                            self._update(
                                session_id,
                                state="failed",
                                stage="failed",
                                error_type="stream_stopped",
                                error="realtime audio stream stopped unexpectedly",
                            )
                self._active_id = None
                self._active_model = None
                release_dispatch = True
        if release_dispatch:
            self._release_offline_dispatch()

    def _handle_worker_exit(
        self,
        process: subprocess.Popen[str],
        return_code: int,
        last_failure: dict[str, Any] | None,
        read_error: Exception | None,
    ) -> None:
        release_dispatch = False
        with self._lock:
            if process is not self._process:
                return
            self._process = None
            self._worker_state = "not_started"
            self._worker_model_id = None
            self._model_ready = False
            self._prepared_key = None
            session_id = self._active_id
            if session_id:
                current = self.repository.get(session_id)
                if current["state"] not in TERMINAL_STATES:
                    error = (
                        (last_failure or {}).get("error")
                        or (str(read_error) if read_error else None)
                        or "\n".join(self._stderr_tail)
                        or f"resident realtime worker exited with code {return_code}"
                    )
                    self._update(
                        session_id,
                        state="interrupted" if self._service_stopping else "failed",
                        stage="service_shutdown" if self._service_stopping else "failed",
                        error_type=(
                            "service_shutdown"
                            if self._service_stopping
                            else (last_failure or {}).get("error_type") or "RvcRealtimeError"
                        ),
                        error=(
                            "service stopped during realtime session"
                            if self._service_stopping
                            else error
                        ),
                    )
                self._active_id = None
                self._active_model = None
                release_dispatch = True
        if release_dispatch:
            self._release_offline_dispatch()

    def _send_command_locked(self, payload: dict[str, Any]) -> None:
        process = self._process
        if not process or process.poll() is not None or process.stdin is None:
            raise RuntimeError("resident realtime worker is unavailable")
        process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _release_offline_dispatch(self) -> None:
        with self._lock:
            if not self._offline_dispatch_paused:
                return
            self._offline_dispatch_paused = False
        self.resume_offline_dispatch("realtime")

    def _heartbeat(self, session_id: str, metrics: dict[str, Any]) -> None:
        with self._lock:
            self.repository.heartbeat(session_id, metrics)

    def _start_stop_watchdog(self, session_id: str, process: subprocess.Popen[str]) -> None:
        with self._lock:
            if self._stop_watchdog and self._stop_watchdog.is_alive():
                return

            def enforce_deadline() -> None:
                deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
                while time.monotonic() < deadline:
                    current = self.repository.get(session_id)
                    if process.poll() is not None or current["state"] != "stopping":
                        return
                    time.sleep(0.1)
                self._update(
                    session_id,
                    state="failed",
                    stage="stop_timeout",
                    error_type="stop_timeout",
                    error=(
                        "realtime audio stream did not stop within "
                        f"{STOP_TIMEOUT_SECONDS:g} seconds"
                    ),
                )
                terminate_process_tree(process)

            self._stop_watchdog = threading.Thread(
                target=enforce_deadline,
                name="voxweave-realtime-stop-watchdog",
                daemon=True,
            )
            self._stop_watchdog.start()

    def get(self, session_id: str) -> dict[str, Any]:
        with self._lock:
            result = self.repository.get(session_id)
            result["worker"] = self._worker_status_locked()
            return result

    def _worker_status_locked(self) -> dict[str, Any]:
        process = self._process
        return {
            "state": self._worker_state,
            "pid": process.pid if process and process.poll() is None else None,
            "model_id": self._worker_model_id,
            "model_ready": self._model_ready,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            session_id = self.repository.latest_id()
            if not session_id:
                return {
                    "session_id": None,
                    "state": "idle",
                    "stage": "idle",
                    "metrics": {},
                    "worker": self._worker_status_locked(),
                }
            result = self.repository.get(session_id)
            result["worker"] = self._worker_status_locked()
            return result

    def stop(self) -> dict[str, Any]:
        with self._lock:
            active = self.repository.active()
            if not active:
                return self.status()
            session_id = active["id"]
            current = self.get(session_id)
            if current["state"] != "stopping":
                self._update(
                    session_id,
                    state="stopping",
                    stage="stopping",
                    detail="realtime stop requested",
                )
            if self._process:
                try:
                    self._send_command_locked({"command": "stop", "session_id": session_id})
                except (BrokenPipeError, OSError):
                    terminate_process_tree(self._process)
                self._start_stop_watchdog(session_id, self._process)
            else:
                self._update(
                    session_id,
                    state="failed",
                    stage="failed",
                    error_type="worker_unavailable",
                    error="resident realtime worker is unavailable",
                )
                self._active_id = None
                self._active_model = None
                self._release_offline_dispatch()
            return self.get(session_id)

    def events(self, session_id: str, after_id: int = 0) -> list[dict[str, Any]]:
        return self.repository.events(session_id, after_id)

    def shutdown(self) -> None:
        with self._lock:
            self._service_stopping = True
            active_id = self._active_id
            process = self._process
        if active_id:
            self._update(
                active_id,
                state="interrupted",
                stage="service_shutdown",
                error_type="service_shutdown",
                error="service stopped during realtime session",
            )
        if process:
            with self._lock:
                try:
                    self._send_command_locked({"command": "shutdown"})
                except (BrokenPipeError, OSError, RuntimeError):
                    pass
        self._release_offline_dispatch()
        reader = self._reader_thread
        if reader:
            reader.join(timeout=15)
        if reader and reader.is_alive() and process and process.poll() is None:
            terminate_process_tree(process)
            reader.join(timeout=5)
