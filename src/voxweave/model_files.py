from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from .config import Settings
from .hashing import sha256_file

CHECKPOINT_PATTERN = re.compile(r"^(?P<family>.+)_e(?P<epoch>\d+)_s\d+$", re.IGNORECASE)


def family_and_epoch(stem: str) -> tuple[str, int | None]:
    match = CHECKPOINT_PATTERN.match(stem)
    if match:
        return match.group("family"), int(match.group("epoch"))
    return stem, None


def index_roots_for_model(settings: Settings, model: Path) -> list[Path]:
    roots = [model.parent]
    if settings.rvc_root:
        for candidate in (
            Path(settings.rvc_root) / "assets" / "indices",
            Path(settings.rvc_root) / "logs",
        ):
            if candidate.is_dir():
                roots.append(candidate.resolve())
    return sorted(set(roots), key=lambda value: str(value).casefold())


def candidate_indices(
    model: Path,
    index_roots: list[Path],
    cancelled: Callable[[], bool] | None = None,
) -> list[Path]:
    stem = model.stem.casefold()
    family, _ = family_and_epoch(stem)
    tokens = {stem, family.casefold()}
    candidates: list[Path] = []
    for root in index_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.index"):
            index_stem = path.stem.casefold()
            if index_stem.startswith("trained_"):
                continue
            if any(token in index_stem or index_stem in token for token in tokens):
                candidates.append(path.resolve())
    ordered = sorted(set(candidates), key=lambda value: str(value).casefold())
    unique_by_hash: dict[str, Path] = {}
    for path in ordered:
        if cancelled and cancelled():
            raise InterruptedError("task cancellation requested")
        digest = sha256_file(path)
        existing = unique_by_hash.get(digest)
        if existing is None or "assets\\indices" in str(path).casefold():
            unique_by_hash[digest] = path
    return sorted(unique_by_hash.values(), key=lambda value: str(value).casefold())


def scan_roots(
    settings: Settings,
    weight_roots: list[str] | None,
    index_roots: list[str] | None,
) -> tuple[list[Path], list[Path]]:
    weights = [
        Path(path).expanduser().resolve()
        for path in (weight_roots or settings.weight_roots)
    ]
    indices = [
        Path(path).expanduser().resolve()
        for path in (index_roots or settings.index_roots)
    ]
    if settings.rvc_root:
        default_weight = Path(settings.rvc_root) / "assets" / "weights"
        if default_weight.is_dir() and default_weight.resolve() not in weights:
            weights.append(default_weight.resolve())
        for default_index in (
            Path(settings.rvc_root) / "assets" / "indices",
            Path(settings.rvc_root) / "logs",
        ):
            if default_index.is_dir() and default_index.resolve() not in indices:
                indices.append(default_index.resolve())
    return weights, indices
