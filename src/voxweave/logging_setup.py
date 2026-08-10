from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from .config import Settings


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("request_id", "operation", "task_id", "error_type", "duration_ms"):
            value = getattr(record, name, None)
            if value is not None:
                payload[name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> Path:
    settings.ensure_layout()
    log_path = settings.logs_dir / "voxweave-service.jsonl"
    root = logging.getLogger()
    resolved = str(log_path.resolve())
    for handler in root.handlers:
        if getattr(handler, "baseFilename", None) == resolved:
            return log_path
    handler = RotatingFileHandler(
        log_path,
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(JsonLogFormatter())
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return log_path
