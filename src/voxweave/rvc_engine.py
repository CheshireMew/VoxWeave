from __future__ import annotations

import hashlib
import json
import logging
import queue
import subprocess
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .hashing import FileVerificationLedger, sha256_file
from .model_registry import ModelRegistry
from .parameter_contracts import normalize_realtime_start, normalize_rvc_parameters
from .process_control import run_capture, start_managed_process, terminate_process_tree
from .runtime import resolve_rvc_entry, resolve_rvc_python


class RvcEngineError(RuntimeError):
    pass


LOGGER = logging.getLogger(__name__)
OFFLINE_IDLE_SECONDS = 60.0
OFFLINE_STARTUP_TIMEOUT_SECONDS = 20.0


class _ResidentOfflineClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.lock = threading.RLock()
        self.process: subprocess.Popen[str] | None = None
        self.responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self.stderr_tail: deque[str] = deque(maxlen=60)
        self.model_key: str | None = None
        self.last_used = 0.0
        self.idle_timer: threading.Timer | None = None

    def _command(self) -> tuple[list[str], Path]:
        python = resolve_rvc_python(self.settings)
        entry = resolve_rvc_entry(self.settings)
        if not python or not entry:
            raise RvcEngineError("RVC runtime is not configured")
        return (
            [
                str(python),
                "-B",
                str(entry),
                "--rvc-root",
                str(Path(self.settings.rvc_root).resolve()),
                "offline",
            ],
            entry,
        )

    @staticmethod
    def _read_stdout(
        process: subprocess.Popen[str], responses: queue.Queue[dict[str, Any]]
    ) -> None:
        if process.stdout is None:
            return
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                responses.put(payload)
        responses.put(
            {
                "ok": False,
                "event": "worker_exited",
                "error": f"resident offline worker exited with code {process.poll()}",
            }
        )

    @staticmethod
    def _read_stderr(process: subprocess.Popen[str], stderr_tail: deque[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            value = line.rstrip()
            if value:
                stderr_tail.append(value)

    def _wait(
        self,
        request_id: str | None,
        cancelled: Callable[[], bool] | None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout if timeout is not None else None
        while True:
            if cancelled and cancelled():
                self._terminate_locked(graceful=False)
                raise InterruptedError("task cancellation requested")
            if deadline is not None and time.monotonic() >= deadline:
                self._terminate_locked(graceful=False)
                raise RvcEngineError(
                    f"resident offline worker did not start within {timeout:g} seconds"
                )
            process = self.process
            if not process or process.poll() is not None:
                detail = "\n".join(self.stderr_tail) or "resident offline worker exited"
                self._terminate_locked()
                raise RvcEngineError(detail)
            try:
                payload = self.responses.get(timeout=0.1)
            except queue.Empty:
                continue
            if request_id is None and payload.get("event") == "worker_started":
                return payload
            if request_id is not None and payload.get("request_id") == request_id:
                return payload
            if not payload.get("ok") and payload.get("event") == "worker_exited":
                detail = payload.get("error") or "\n".join(self.stderr_tail)
                self._terminate_locked()
                raise RvcEngineError(str(detail))

    def _start_locked(self) -> None:
        command, entry = self._command()
        responses: queue.Queue[dict[str, Any]] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=60)
        self.responses = responses
        self.stderr_tail = stderr_tail
        process = start_managed_process(
            command,
            cwd=entry.parent,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        if process.stdin is None or process.stdout is None or process.stderr is None:
            terminate_process_tree(process)
            raise RvcEngineError("resident offline worker pipes are unavailable")
        self.process = process
        threading.Thread(
            target=self._read_stdout,
            args=(process, responses),
            name="voxweave-offline-results",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._read_stderr,
            args=(process, stderr_tail),
            name="voxweave-offline-stderr",
            daemon=True,
        ).start()
        self._wait(None, None, timeout=OFFLINE_STARTUP_TIMEOUT_SECONDS)

    def _terminate_locked(self, *, graceful: bool = True) -> None:
        timer = self.idle_timer
        self.idle_timer = None
        if timer:
            timer.cancel()
        process = self.process
        self.process = None
        self.model_key = None
        if not process:
            return
        if graceful and process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write('{"command":"shutdown"}\n')
                process.stdin.flush()
                process.wait(timeout=2)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                pass
        if process.poll() is None:
            terminate_process_tree(process)

    def _release_if_idle(self) -> None:
        with self.lock:
            if time.monotonic() - self.last_used >= OFFLINE_IDLE_SECONDS:
                self._terminate_locked()

    def request(
        self,
        model_key: str,
        payload: dict[str, Any],
        cancelled: Callable[[], bool] | None,
    ) -> dict[str, Any]:
        with self.lock:
            if self.model_key != model_key:
                self._terminate_locked()
            if not self.process or self.process.poll() is not None:
                self._start_locked()
                self.model_key = model_key
            request_id = str(uuid.uuid4())
            process = self.process
            if not process or process.stdin is None:
                raise RvcEngineError("resident offline worker is unavailable")
            try:
                process.stdin.write(
                    json.dumps({**payload, "request_id": request_id}, ensure_ascii=False) + "\n"
                )
                process.stdin.flush()
            except (BrokenPipeError, OSError) as error:
                self._terminate_locked()
                raise RvcEngineError(str(error)) from error
            response = self._wait(request_id, cancelled)
            self.last_used = time.monotonic()
            if self.idle_timer:
                self.idle_timer.cancel()
            self.idle_timer = threading.Timer(OFFLINE_IDLE_SECONDS, self._release_if_idle)
            self.idle_timer.daemon = True
            self.idle_timer.start()
            if not response.get("ok"):
                raise RvcEngineError(str(response.get("error") or "RVC conversion failed"))
            return response

    def release(self) -> None:
        with self.lock:
            self._terminate_locked()


class RvcEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._offline = _ResidentOfflineClient(settings)

    def _runtime(self) -> tuple[Path, Path]:
        python = resolve_rvc_python(self.settings)
        entry = resolve_rvc_entry(self.settings)
        if not python or not entry:
            raise RvcEngineError("RVC runtime is not configured")
        return python, entry

    def audio_devices(self) -> dict[str, Any]:
        python, entry = self._runtime()
        command = [
            str(python),
            "-B",
            str(entry),
            "--rvc-root",
            str(Path(self.settings.rvc_root).resolve()),
            "devices",
        ]
        return self._run_worker(command, entry, None)

    def audio_test(self, mode: str, device: int, duration_seconds: float) -> dict[str, Any]:
        python, entry = self._runtime()
        command = [
            str(python),
            "-B",
            str(entry),
            "--rvc-root",
            str(Path(self.settings.rvc_root).resolve()),
            "audio-test",
            "--mode",
            mode,
            "--device",
            str(device),
            "--duration-seconds",
            str(duration_seconds),
        ]
        return self._run_worker(command, entry, None)

    def realtime_worker_command(self) -> tuple[list[str], Path]:
        python, entry = self._runtime()
        command = [
            str(python),
            "-B",
            str(entry),
            "--rvc-root",
            str(Path(self.settings.rvc_root).resolve()),
            "realtime",
        ]
        return command, entry

    def realtime_payload(
        self,
        model: dict[str, Any],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        values = normalize_realtime_start(parameters)
        payload = {
            "command": "start",
            "model_id": model["id"],
            "model": model["model_path"],
            "index": model.get("index_path"),
            "pitch": values["pitch"],
            "f0": values["f0"],
            "index_rate": values["index_rate"],
            "rms_mix_rate": values["rms_mix_rate"],
            "input_device": values["input_device"],
            "output_device": values["output_device"],
            "block_seconds": values["block_seconds"],
            "crossfade_seconds": values["crossfade_seconds"],
            "extra_seconds": values["extra_seconds"],
            "vad_threshold": values["vad_threshold"],
            "input_gate_db": values["input_gate_db"],
        }
        converter_identity = {
            "model_sha256": model["model_sha256"],
            "index_sha256": model.get("index_sha256"),
            "pitch": values["pitch"],
            "index_rate": values["index_rate"],
        }
        payload["converter_key"] = hashlib.sha256(
            json.dumps(converter_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_identity = {
            **payload,
            "input_device_name": parameters.get("input_device_name"),
            "output_device_name": parameters.get("output_device_name"),
            "input_device_sample_rate": parameters.get("input_device_sample_rate"),
            "output_device_sample_rate": parameters.get("output_device_sample_rate"),
            "hostapi": parameters.get("hostapi"),
        }
        payload["cache_key"] = hashlib.sha256(
            json.dumps(cache_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        payload["test_mode"] = values["test_mode"]
        return payload

    @staticmethod
    def _run_worker(
        command: list[str], entry: Path, cancelled: Callable[[], bool] | None
    ) -> dict[str, Any]:
        completed = run_capture(command, cwd=entry.parent, cancelled=cancelled)
        stdout = completed.stdout
        stderr = completed.stderr
        lines = [line for line in stdout.splitlines() if line.strip()]
        payload: dict[str, Any] | None = None
        for line in reversed(lines):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if completed.returncode != 0 or not payload or not payload.get("ok"):
            detail = (payload or {}).get("error") or stderr.strip() or stdout.strip()
            raise RvcEngineError(detail)
        return payload

    def _command(
        self,
        python: Path,
        entry: Path,
        subcommand: str,
        model: dict[str, Any],
        values: dict[str, Any],
    ) -> list[str]:
        command = [
            str(python),
            "-B",
            str(entry),
            "--rvc-root",
            str(Path(self.settings.rvc_root).resolve()),
            subcommand,
            "--model",
            model["model_path"],
            "--pitch",
            str(values["pitch"]),
            "--f0",
            values["f0"],
            "--index-rate",
            str(values["index_rate"]),
            "--rms-mix-rate",
            str(values["rms_mix_rate"]),
            "--protect",
            str(values["protect"]),
        ]
        if model.get("index_path"):
            command.extend(["--index", model["index_path"]])
        return command

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        model: dict[str, Any],
        parameters: dict[str, Any],
        progress: Callable[[float, str, str | None], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        ledger: FileVerificationLedger | None = None,
    ) -> dict[str, Any]:
        input_path = input_path.expanduser().resolve()
        output_path = output_path.expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(input_path)
        if output_path.exists() and not parameters.get("overwrite", False):
            raise FileExistsError(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        values = normalize_rvc_parameters(parameters)
        if progress:
            progress(0.35, "converting", f"loading {model['display_name']}")
        ModelRegistry.verify_snapshot(model)
        payload = self._offline.request(
            f"{model['model_path']}:{model['model_sha256']}",
            {
                "command": "convert",
                "model": model["model_path"],
                "index": model.get("index_path"),
                **values,
                "overwrite": bool(parameters.get("overwrite")),
                "items": [{"input": str(input_path), "output": str(output_path)}],
            },
            cancelled,
        )
        ModelRegistry.verify_snapshot(model)
        if not output_path.is_file():
            raise RvcEngineError("RVC reported success but output file is missing")
        if progress:
            progress(0.8, "validating", "hashing RVC output")
        return {
            "engine": "rvc",
            "model_id": model["id"],
            "model_sha256": model["model_sha256"],
            "index_sha256": model.get("index_sha256"),
            "parameters": {
                **values,
            },
            "output_path": str(output_path),
            "output_sha256": (
                ledger.verify(output_path, cancelled=cancelled).sha256
                if ledger is not None
                else sha256_file(output_path)
            ),
            "upstream": {**payload, **payload["results"][0]},
        }

    def convert_batch(
        self,
        jobs: list[tuple[Path, Path]],
        model: dict[str, Any],
        parameters: dict[str, Any],
        progress: Callable[[float, str, str | None], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
        ledger: FileVerificationLedger | None = None,
    ) -> list[dict[str, Any]]:
        if not jobs:
            raise ValueError("RVC batch requires at least one job")
        ModelRegistry.verify_snapshot(model)
        values = normalize_rvc_parameters(parameters)
        items = []
        for input_path, output_path in jobs:
            input_path = input_path.expanduser().resolve()
            output_path = output_path.expanduser().resolve()
            if not input_path.is_file():
                raise FileNotFoundError(input_path)
            if output_path.exists():
                raise FileExistsError(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            items.append({"input": str(input_path), "output": str(output_path)})
        if progress:
            progress(0.35, "converting", f"loading {model['display_name']} for {len(jobs)} chunks")
        payload = self._offline.request(
            f"{model['model_path']}:{model['model_sha256']}",
            {
                "command": "convert-batch",
                "model": model["model_path"],
                "index": model.get("index_path"),
                **values,
                "overwrite": False,
                "items": items,
            },
            cancelled,
        )
        ModelRegistry.verify_snapshot(model)
        upstream_results = payload.get("results") or []
        if len(upstream_results) != len(jobs):
            raise RvcEngineError("RVC batch returned an unexpected result count")
        results = []
        for (input_path, output_path), upstream in zip(jobs, upstream_results, strict=True):
            if not output_path.is_file():
                raise RvcEngineError(f"RVC batch output is missing: {output_path}")
            results.append(
                {
                    "engine": "rvc",
                    "model_id": model["id"],
                    "model_sha256": model["model_sha256"],
                    "index_sha256": model.get("index_sha256"),
                    "parameters": values,
                    "input_path": str(input_path),
                    "output_path": str(output_path),
                    "output_sha256": (
                        ledger.verify(output_path, cancelled=cancelled).sha256
                        if ledger is not None
                        else sha256_file(output_path)
                    ),
                    "upstream": upstream,
                }
            )
        if progress:
            progress(0.78, "converting", f"converted {len(jobs)} verified chunks")
        return results

    def release_offline(self) -> None:
        self._offline.release()

    def shutdown(self) -> None:
        self._offline.release()
