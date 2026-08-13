from __future__ import annotations

import time
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
    except Exception as original:
        if staging.exists():
            failed_root.mkdir(parents=True, exist_ok=True)
            destination = failed_root / f"{label}-{uuid.uuid4().hex}"
            archive_error: OSError | None = None
            for delay in (0.0, 0.1, 0.25, 0.5, 1.0, 2.0):
                if delay:
                    time.sleep(delay)
                try:
                    staging.replace(destination)
                    archive_error = None
                    break
                except OSError as exc:
                    archive_error = exc
            if archive_error is not None:
                original.add_note(f"failed staging remains at {staging}: {archive_error}")
        raise
