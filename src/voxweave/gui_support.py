from __future__ import annotations

from PySide6.QtCore import QUrl


def local_path(value: str) -> str:
    url = QUrl(value)
    return url.toLocalFile() if url.isLocalFile() else value
