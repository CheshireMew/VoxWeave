from __future__ import annotations

import json
import logging
import subprocess
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .process_control import start_managed_process, terminate_process_tree
from .rvc_engine import RvcEngine

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TransportExit:
    return_code: int
    last_failure: dict[str, Any] | None
    read_error: Exception | None
    stderr: str


class RealtimeWorkerTransport:
    """Owns the worker process, standard streams and JSON-line transport."""

    def __init__(
        self,
        engine: RvcEngine,
        on_payload: Callable[[int, dict[str, Any]], None],
        on_exit: Callable[[int, TransportExit], None],
    ) -> None:
        self.engine = engine
        self.on_payload = on_payload
        self.on_exit = on_exit
        self._lock = threading.RLock()
        self._process: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._stderr_tail: deque[str] = deque(maxlen=40)
        self._generation = 0

    def status(self) -> dict[str, int | None]:
        with self._lock:
            process = self._process
            return {
                "pid": process.pid if process and process.poll() is None else None,
                "generation": self._generation if process else None,
            }

    def start(self) -> int:
        with self._lock:
            if self._process and self._process.poll() is None:
                return self._generation
            command, entry = self.engine.realtime_worker_command()
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
                raise RuntimeError("resident realtime worker pipes are unavailable")
            self._generation += 1
            generation = self._generation
            self._process = process
            self._stderr_tail.clear()
            self._stderr_thread = threading.Thread(
                target=self._read_stderr,
                args=(process,),
                name="voxweave-realtime-stderr",
                daemon=True,
            )
            self._reader_thread = threading.Thread(
                target=self._read_worker,
                args=(generation, process),
                name="voxweave-realtime-events",
                daemon=True,
            )
            self._stderr_thread.start()
            self._reader_thread.start()
            return generation

    def is_active(self, generation: int) -> bool:
        with self._lock:
            return bool(
                generation == self._generation
                and self._process
                and self._process.poll() is None
            )

    def send(self, payload: dict[str, Any]) -> None:
        with self._lock:
            process = self._process
            if not process or process.poll() is not None or process.stdin is None:
                raise RuntimeError("resident realtime worker is unavailable")
            process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
            process.stdin.flush()

    def terminate(self, generation: int) -> None:
        with self._lock:
            if generation != self._generation:
                return
            process = self._process
        if process and process.poll() is None:
            terminate_process_tree(process)

    def close(self, *, timeout: float, action: str) -> None:
        with self._lock:
            generation = self._generation
            process = self._process
            if process and process.poll() is None:
                try:
                    self.send({"command": "shutdown"})
                except (BrokenPipeError, OSError, RuntimeError):
                    terminate_process_tree(process)
            reader = self._reader_thread
        if reader:
            reader.join(timeout=timeout)
        if reader and reader.is_alive() and process and process.poll() is None:
            self.terminate(generation)
            reader.join(timeout=5)
        if reader and reader.is_alive():
            raise RuntimeError(f"realtime worker reader did not stop during {action}")

    def _read_stderr(self, process: subprocess.Popen[str]) -> None:
        if process.stderr is None:
            return
        for line in process.stderr:
            value = line.rstrip()
            if value:
                self._stderr_tail.append(value)

    def _read_worker(self, generation: int, process: subprocess.Popen[str]) -> None:
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
                self.on_payload(generation, payload)
        except Exception as error:  # noqa: BLE001 - isolated process reader boundary
            read_error = error
            LOGGER.exception("resident realtime worker reader failed")
            if process.poll() is None:
                terminate_process_tree(process)
        return_code = process.wait()
        stderr_thread = self._stderr_thread
        if stderr_thread:
            stderr_thread.join(timeout=1)
        with self._lock:
            if generation != self._generation or process is not self._process:
                return
            stderr = "\n".join(self._stderr_tail)
            self._process = None
        self.on_exit(
            generation,
            TransportExit(
                return_code=return_code,
                last_failure=last_failure,
                read_error=read_error,
                stderr=stderr,
            ),
        )
