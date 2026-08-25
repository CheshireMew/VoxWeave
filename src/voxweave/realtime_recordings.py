from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .projects import ProjectService
from .realtime_session_state import RealtimeSessionState


class RealtimeRecordingService:
    """Promotes a closed dry/wet recording into the offline project workflow."""

    def __init__(
        self,
        sessions: RealtimeSessionState,
        projects: ProjectService,
    ) -> None:
        self.sessions = sessions
        self.projects = projects

    def promote(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self.sessions.get(str(arguments["session_id"]))
        if session["state"] != "stopped":
            raise ValueError("only a stopped realtime recording can become a project")
        metrics = session.get("metrics") or {}
        manifest_value = metrics.get("recording_manifest_path")
        if not manifest_value:
            raise LookupError("realtime session has no completed recording manifest")
        manifest_path = Path(str(manifest_value)).expanduser().resolve()
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("session_id") != session["id"]:
            raise ValueError("recording manifest belongs to another realtime session")
        dry = Path(str(payload["dry"]["path"])).expanduser().resolve()
        wet = Path(str(payload["wet"]["path"])).expanduser().resolve()
        for key, path in (("dry", dry), ("wet", wet)):
            if not path.is_file():
                raise FileNotFoundError(path)
            if sha256_file(path) != payload[key]["sha256"]:
                raise ValueError(f"recording {key} file changed after capture")
        settings = dict(session["arguments"])
        output = arguments.get("output") or str(
            dry.with_name(f"{dry.stem}-high-quality.wav")
        )
        project = self.projects.create(
            {
                "name": arguments["project_name"],
                "input": str(dry),
                "output": output,
                "content_mode": "clean",
                "document": {
                    "default_model": session["model_id"],
                    "default_parameters": {
                        key: settings.get(key)
                        for key in (
                            "pitch",
                            "f0",
                            "index_rate",
                            "rms_mix_rate",
                        )
                        if settings.get(key) is not None
                    },
                },
            }
        )
        payload["promoted_project_id"] = project["id"]
        payload["promotion_id"] = str(uuid.uuid4())
        temporary = manifest_path.with_name(
            f".{manifest_path.name}.{payload['promotion_id']}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(manifest_path)
        return {
            "session_id": session["id"],
            "recording_manifest_path": str(manifest_path),
            "dry_path": str(dry),
            "wet_path": str(wet),
            "project": project,
        }
