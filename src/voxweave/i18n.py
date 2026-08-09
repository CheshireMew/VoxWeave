from __future__ import annotations

import json
from typing import Any

from .config import PACKAGE_ROOT

MESSAGES: dict[str, dict[str, str]] = json.loads(
    (PACKAGE_ROOT / "resources" / "translations.json").read_text(encoding="utf-8")
)


def translate(language: str, key: str, **values: Any) -> str:
    table = MESSAGES.get(language, MESSAGES["en"])
    template = table.get(key) or MESSAGES["en"].get(key) or key
    return template.format(**values)
