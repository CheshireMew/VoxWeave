from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .database import Database
from .hashing import sha256_file


class RealtimeRecordingManifestService:
    """Finalizes durable metadata for a completed realtime recording pair."""

    def __init__(self, recording_directory: Path) -> None:
        self.recording_directory = recording_directory.resolve()

    @classmethod
    def for_database(cls, database: Database) -> RealtimeRecordingManifestService:
        parent = database.path.parent
        data_root = parent.parent if parent.name == "state" else parent
        return cls(data_root / "artifacts" / "realtime")

    def finalize(self, session: dict[str, Any], metrics: dict[str, Any]) -> str | None:
        dry_value = metrics.get("recording_dry_path")
        wet_value = metrics.get("recording_wet_path")
        if not dry_value or not wet_value:
            return None
        dry = Path(str(dry_value)).expanduser().resolve()
        wet = Path(str(wet_value)).expanduser().resolve()
        if not dry.is_file() or not wet.is_file():
            return None
        self.recording_directory.mkdir(parents=True, exist_ok=True)
        path = self.recording_directory / f"{session['id']}-recording.json"
        payload = {
            "protocol": "voxweave-realtime-recording",
            "version": 1,
            "session_id": session["id"],
            "model": {
                "id": session["model_id"],
                "model_sha256": session["model_sha256"],
                "index_sha256": session.get("index_sha256"),
            },
            "arguments": session["arguments"],
            "dry": self._file_details(dry),
            "wet": self._file_details(wet),
            "performance": {
                key: metrics.get(key)
                for key in (
                    "callbacks",
                    "inference_callbacks",
                    "skipped_callbacks",
                    "suppressed_callbacks",
                    "xruns",
                    "input_overruns",
                    "output_underruns",
                    "infer_ms",
                    "estimated_latency_ms",
                    "recording_dropped_blocks",
                    "recording_error",
                )
            },
            "promoted_project_id": None,
        }
        temporary = path.with_name(f".{path.name}.{session['id']}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
        return str(path)

    @staticmethod
    def _file_details(path: Path) -> dict[str, Any]:
        return {
            "path": str(path),
            "sha256": sha256_file(path),
            "size_bytes": path.stat().st_size,
        }
