from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .realtime_session_state import RealtimeSessionState
from .realtime_worker_controller import RealtimeWorkerController
from .rvc_engine import RvcEngine


class RealtimeRoutingTestService:
    """Runs a closed-loop audio route probe only while realtime is idle."""

    def __init__(
        self,
        sessions: RealtimeSessionState,
        worker: RealtimeWorkerController,
        engine: RvcEngine,
        control_lock: threading.RLock,
        state_lock: threading.RLock,
        stopping: Callable[[], bool],
    ) -> None:
        self.sessions = sessions
        self.worker = worker
        self.engine = engine
        self.control_lock = control_lock
        self.state_lock = state_lock
        self.stopping = stopping

    def run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with self.control_lock:
            with self.state_lock:
                if self.stopping():
                    raise RuntimeError("realtime service is stopping")
                worker_state = self.worker.status()["state"]
                if self.sessions.active() or worker_state in {"starting", "warming"}:
                    raise RuntimeError(
                        "audio routing cannot be tested while realtime is active or preparing"
                    )
            return self.engine.routing_test(
                int(arguments["input_device"]),
                int(arguments["output_device"]),
                float(arguments["duration_seconds"]),
            )
