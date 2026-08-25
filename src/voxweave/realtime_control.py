from __future__ import annotations

from pathlib import Path
from typing import Any

from .realtime_session_state import RealtimeSessionState
from .realtime_worker_controller import RealtimeWorkerController


class RealtimeControlService:
    def __init__(
        self,
        sessions: RealtimeSessionState,
        worker: RealtimeWorkerController,
        artifacts_dir: Path,
        control_lock: Any,
    ) -> None:
        self.sessions = sessions
        self.worker = worker
        self.recording_directory = (artifacts_dir / "realtime").resolve()
        self.control_lock = control_lock

    def control(self, arguments: dict[str, Any]) -> dict[str, Any]:
        with self.control_lock:
            active = self.sessions.active()
            if not active:
                raise RuntimeError("realtime session is not active")
            session_id = str(active["id"])
            changes = {
                key: bool(arguments[key])
                for key in (
                    "bypass",
                    "muted",
                    "recording",
                    "push_to_talk_enabled",
                    "push_to_talk_pressed",
                )
                if arguments.get(key) is not None
            }
            if changes.get("recording"):
                changes["recording_directory"] = str(self.recording_directory)
            self.worker.control(session_id, changes)
            self.sessions.repository.update_control(session_id, changes)
            result = self.sessions.get(session_id)
            result["worker"] = self.worker.status()
            return result
