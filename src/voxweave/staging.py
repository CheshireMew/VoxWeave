from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def archive_failed_staging(
    staging: Path,
    failed_root: Path,
    label: str,
) -> Iterator[None]:
    """Move a failed staging tree aside without obscuring the original exception."""

    try:
        yield
    except Exception:
        if staging.exists():
            failed_root.mkdir(parents=True, exist_ok=True)
            staging.replace(failed_root / f"{label}-{uuid.uuid4().hex}")
        raise
