from __future__ import annotations

import base64
import json

from .protocol import OperationError


def encode_cursor(created_at: str, item_id: str) -> str:
    raw = json.dumps([created_at, item_id], separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str, *, resource: str) -> tuple[str, str]:
    try:
        padding = "=" * (-len(cursor) % 4)
        created_at, item_id = json.loads(
            base64.urlsafe_b64decode(cursor + padding).decode("utf-8")
        )
        if not isinstance(created_at, str) or not isinstance(item_id, str):
            raise ValueError
        return created_at, item_id
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        raise OperationError("invalid_cursor", f"{resource} cursor is invalid") from exc
