from __future__ import annotations

import hashlib
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import __version__
from .hashing import VerifiedFile, bind_verified_digest, sha256_file, verify_file

ProgressCallback = Callable[[float, str, str | None], None]
CancellationProbe = Callable[[], bool]
FileFailureHandler = Callable[[Path], None]


@dataclass(frozen=True, slots=True)
class DownloadSpec:
    url: str
    filename: str
    size_bytes: int
    sha256: str

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> DownloadSpec:
        return cls(
            url=str(value["url"]),
            filename=str(value["filename"]),
            size_bytes=int(value["size_bytes"]),
            sha256=str(value["sha256"]).casefold(),
        )


def file_matches_download(path: Path, spec: DownloadSpec) -> bool:
    return bool(
        path.is_file()
        and path.stat().st_size == spec.size_bytes
        and sha256_file(path).casefold() == spec.sha256
    )


def download_verified(
    spec: DownloadSpec,
    target: Path,
    *,
    cancelled: CancellationProbe,
    progress: ProgressCallback,
    progress_start: float,
    progress_end: float,
    invalid_existing: FileFailureHandler | None = None,
    failed_partial: FileFailureHandler | None = None,
) -> VerifiedFile:
    """Download one immutable artifact and publish it only after size/hash verification."""

    if target.exists():
        try:
            if target.stat().st_size != spec.size_bytes:
                raise ValueError(f"existing size mismatch for {spec.filename}")
            return verify_file(target, expected_sha256=spec.sha256)
        except ValueError:
            pass
        if invalid_existing is None:
            raise FileExistsError(f"download target contains different data: {target}")
        invalid_existing(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.{uuid.uuid4().hex}.part")
    request = urllib.request.Request(
        spec.url,
        headers={"User-Agent": f"VoxWeave/{__version__}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) != spec.size_bytes:
                raise ValueError(f"download size declaration mismatch for {spec.filename}")
            received = 0
            digest = hashlib.sha256()
            while chunk := response.read(1024 * 1024):
                if cancelled():
                    raise InterruptedError("task cancellation requested")
                output.write(chunk)
                received += len(chunk)
                digest.update(chunk)
                fraction = min(1.0, received / max(1, spec.size_bytes))
                progress(
                    progress_start + (progress_end - progress_start) * fraction,
                    "download",
                    f"{spec.filename}: {received}/{spec.size_bytes} bytes",
                )
        if received != spec.size_bytes:
            raise ValueError(f"downloaded size mismatch for {spec.filename}")
        if digest.hexdigest().casefold() != spec.sha256:
            raise ValueError(f"downloaded hash mismatch for {spec.filename}")
        verified = bind_verified_digest(partial, digest.hexdigest(), received)
        partial.replace(target)
        return verified.rebind(target)
    except Exception:
        if partial.exists() and failed_partial is not None:
            failed_partial(partial)
        raise
