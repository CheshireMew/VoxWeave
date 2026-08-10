from __future__ import annotations

import json
import logging

from voxweave.config import Settings
from voxweave.logging_setup import configure_logging


def test_service_log_is_rotating_structured_json(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    path = configure_logging(settings)
    root = logging.getLogger()
    handler = next(
        item for item in root.handlers if getattr(item, "baseFilename", None) == str(path.resolve())
    )
    try:
        logging.getLogger("voxweave.test").info(
            "completed",
            extra={"request_id": "request-1", "operation": "test.operation"},
        )
        handler.flush()
        payload = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        assert payload["level"] == "INFO"
        assert payload["message"] == "completed"
        assert payload["request_id"] == "request-1"
        assert payload["operation"] == "test.operation"
    finally:
        root.removeHandler(handler)
        handler.close()
