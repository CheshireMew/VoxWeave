from __future__ import annotations

import json
import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime_contract import runtime_contract

MINIMUM_INITIAL_FREE_BYTES = 12 * 1024**3
DATA_ROOT_LOCATIONS = (
    Path("VoxWeave"),
    Path("Tools/VoxWeave"),
    Path("Apps/VoxWeave"),
    Path("Data/VoxWeave"),
)
RVC_NAMES = {"rvc", "retrieval-based-voice-conversion-webui"}
RVC_LOCATIONS = (
    Path("RVC"),
    Path("Tools/RVC"),
    Path("Code/RVC"),
    Path("Apps/RVC"),
    Path("Retrieval-based-Voice-Conversion-WebUI"),
    Path("Tools/Retrieval-based-Voice-Conversion-WebUI"),
    Path("Code/Retrieval-based-Voice-Conversion-WebUI"),
)
SEARCH_CONTAINERS = ("Tools", "Code", "Work", "Projects", "AI", "Apps")
SEARCH_PRUNE = {
    "$recycle.bin",
    ".git",
    ".venv",
    "appdata",
    "build",
    "cache",
    "dist",
    "node_modules",
    "program files",
    "program files (x86)",
    "site-packages",
    "system volume information",
    "venv",
    "windows",
}


@dataclass(frozen=True, slots=True)
class RuntimeCandidate:
    rvc_root: Path
    rvc_python: Path
    ffmpeg: Path | None
    ffprobe: Path | None


@dataclass(frozen=True, slots=True)
class InitialSetup:
    data_root: Path | None
    reused_existing_data: bool
    runtime: RuntimeCandidate | None = None
    reason: str = ""


def fixed_drive_roots() -> list[Path]:
    """Return fixed Windows volumes without invoking a shell."""

    if sys.platform != "win32":
        return [Path("/")]
    import ctypes  # noqa: PLC0415

    kernel32 = ctypes.windll.kernel32
    mask = int(kernel32.GetLogicalDrives())
    roots: list[Path] = []
    for index in range(26):
        if not mask & (1 << index):
            continue
        root = Path(f"{chr(65 + index)}:/")
        if int(kernel32.GetDriveTypeW(str(root))) == 3:  # DRIVE_FIXED
            roots.append(root)
    return roots


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _pointer_target(path: Path) -> Path | None:
    payload = _read_json(path)
    value = payload.get("data_root") if payload else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return Path(value).expanduser().resolve()
    except OSError:
        return None


def _rvc_python(root: Path) -> Path | None:
    for relative in (
        Path(".venv/Scripts/python.exe"),
        Path("venv/Scripts/python.exe"),
        Path("env/Scripts/python.exe"),
        Path("runtime/python.exe"),
    ):
        candidate = root / relative
        if candidate.is_file():
            return candidate.resolve()
    return None


def _is_rvc_root(root: Path, *, require_assets: bool = True) -> bool:
    required = [
        root / "configs" / "config.py",
        root / "infer" / "vc" / "modules.py",
    ]
    if require_assets:
        required.extend(
            root / "assets" / relative
            for relative in runtime_contract().runtime_assets.required_files
        )
    return all(path.is_file() for path in required) and _rvc_python(root) is not None


def _binary_pair_from_directory(directory: Path) -> tuple[Path, Path] | None:
    ffmpeg = directory / "ffmpeg.exe"
    ffprobe = directory / "ffprobe.exe"
    if ffmpeg.is_file() and ffprobe.is_file():
        return ffmpeg.resolve(), ffprobe.resolve()
    return None


def _system_ffmpeg_pair() -> tuple[Path, Path] | None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        return None
    return Path(ffmpeg).resolve(), Path(ffprobe).resolve()


def _configured_runtime(payload: dict[str, Any]) -> RuntimeCandidate | None:
    root_value = payload.get("rvc_root")
    python_value = payload.get("rvc_python")
    if not isinstance(root_value, str) or not isinstance(python_value, str):
        return None
    root = Path(root_value).expanduser()
    python = Path(python_value).expanduser()
    if not _is_rvc_root(root) or not python.is_file():
        return None
    ffmpeg_value = payload.get("ffmpeg")
    ffprobe_value = payload.get("ffprobe")
    ffmpeg = Path(ffmpeg_value).expanduser() if isinstance(ffmpeg_value, str) else None
    ffprobe = Path(ffprobe_value).expanduser() if isinstance(ffprobe_value, str) else None
    return RuntimeCandidate(
        root.resolve(),
        python.resolve(),
        ffmpeg.resolve() if ffmpeg and ffmpeg.is_file() else None,
        ffprobe.resolve() if ffprobe and ffprobe.is_file() else None,
    )


def _data_root_score(root: Path) -> tuple[int, int]:
    settings_path = root / "config" / "settings.json"
    payload = _read_json(settings_path)
    if payload is None:
        return (0, 0)
    score = 100
    if _configured_runtime(payload):
        score += 100
    if (root / "runtime" / "rvc" / "source" / "configs" / "config.py").is_file():
        score += 80
    if (root / "state" / "voxweave.sqlite3").is_file():
        score += 30
    try:
        if any((root / "models").glob("*/model.pth")):
            score += 30
    except OSError:
        pass
    try:
        modified = settings_path.stat().st_mtime_ns
    except OSError:
        modified = 0
    return score, modified


def discover_existing_data_root(
    drive_roots: list[Path],
    *,
    pointer_paths: tuple[Path, ...] = (),
    extra_candidates: tuple[Path, ...] = (),
) -> Path | None:
    candidates: list[Path] = []
    for pointer in pointer_paths:
        target = _pointer_target(pointer)
        if target:
            candidates.append(target)
    candidates.extend(extra_candidates)
    for drive in drive_roots:
        candidates.extend(drive / relative for relative in DATA_ROOT_LOCATIONS)

    ranked: list[tuple[int, int, str, Path]] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        score, modified = _data_root_score(resolved)
        if score:
            ranked.append((score, modified, key, resolved))
    if not ranked:
        return None
    return max(ranked)[-1]


def _shallow_rvc_search(base: Path, *, deadline: float, max_depth: int = 4) -> Path | None:
    pending: list[tuple[Path, int]] = [(base, 0)]
    visited = 0
    while pending and visited < 5000 and time.monotonic() < deadline:
        current, depth = pending.pop(0)
        visited += 1
        if current.name.casefold() in RVC_NAMES and _is_rvc_root(current):
            return current.resolve()
        if depth >= max_depth:
            continue
        try:
            entries = os.scandir(current)
        except OSError:
            continue
        children: list[Path] = []
        with entries:
            for item in entries:
                try:
                    is_directory = item.is_dir(follow_symlinks=False)
                except OSError:
                    continue
                if (
                    is_directory
                    and item.name.casefold() not in SEARCH_PRUNE
                    and not item.name.startswith(".")
                ):
                    children.append(Path(item.path))
        pending.extend((child, depth + 1) for child in children)
    return None


def discover_existing_runtime(
    drive_roots: list[Path],
    *,
    application_root: Path,
    search_seconds: float = 2.5,
) -> RuntimeCandidate | None:
    candidates: list[Path] = []
    for variable in ("VOXWEAVE_RVC_ROOT", "RVC_ROOT"):
        value = os.environ.get(variable)
        if value:
            candidates.append(Path(value).expanduser())
    candidates.extend(
        (
            application_root / "RVC",
            application_root.parent / "RVC",
            application_root / "Retrieval-based-Voice-Conversion-WebUI",
            application_root.parent / "Retrieval-based-Voice-Conversion-WebUI",
        )
    )
    for drive in drive_roots:
        candidates.extend(drive / relative for relative in RVC_LOCATIONS)

    root: Path | None = None
    for candidate in candidates:
        if _is_rvc_root(candidate):
            root = candidate.resolve()
            break
    if root is None:
        deadline = time.monotonic() + search_seconds
        for drive in drive_roots:
            for name in SEARCH_CONTAINERS:
                base = drive / name
                if not base.is_dir():
                    continue
                root = _shallow_rvc_search(base, deadline=deadline)
                if root or time.monotonic() >= deadline:
                    break
            if root or time.monotonic() >= deadline:
                break
    if root is None:
        return None

    pair = _system_ffmpeg_pair()
    if pair is None:
        for directory in (
            root,
            root / "bin",
            root / "ffmpeg" / "bin",
            root.parent / "ffmpeg" / "bin",
        ):
            pair = _binary_pair_from_directory(directory)
            if pair:
                break
    return RuntimeCandidate(
        rvc_root=root,
        rvc_python=_rvc_python(root),  # type: ignore[arg-type]
        ffmpeg=pair[0] if pair else None,
        ffprobe=pair[1] if pair else None,
    )


def discover_runtime_for_data_root(
    data_root: Path,
    *,
    application_root: Path,
    drive_roots: list[Path] | None = None,
) -> RuntimeCandidate | None:
    """Validate configured runtime paths, then discover a usable local runtime."""

    payload = _read_json(data_root / "config" / "settings.json") or {}
    configured = _configured_runtime(payload)
    if configured is not None:
        if configured.ffmpeg is not None and configured.ffprobe is not None:
            return configured
        pair = _system_ffmpeg_pair()
        if pair is None:
            for directory in (
                configured.rvc_root,
                configured.rvc_root / "bin",
                configured.rvc_root / "ffmpeg" / "bin",
                configured.rvc_root.parent / "ffmpeg" / "bin",
            ):
                pair = _binary_pair_from_directory(directory)
                if pair:
                    break
        return RuntimeCandidate(
            configured.rvc_root,
            configured.rvc_python,
            pair[0] if pair else configured.ffmpeg,
            pair[1] if pair else configured.ffprobe,
        )
    drives = drive_roots if drive_roots is not None else fixed_drive_roots()
    return discover_existing_runtime(drives, application_root=application_root)


def _system_drive() -> str:
    value = os.environ.get("SystemDrive") or Path.home().drive
    return value.casefold()


def _is_system_drive(drive: Path) -> bool:
    return drive.drive.casefold() == _system_drive()


def choose_automatic_data_root(drive_roots: list[Path]) -> Path | None:
    ranked: list[tuple[int, int, str, Path]] = []
    for drive in drive_roots:
        try:
            free = shutil.disk_usage(drive).free
        except OSError:
            continue
        if free < MINIMUM_INITIAL_FREE_BYTES:
            continue
        non_system = int(not _is_system_drive(drive))
        ranked.append((non_system, free, os.path.normcase(str(drive)), drive))
    if not ranked:
        return None
    drive = max(ranked)[-1]
    for name in ("VoxWeave", "VoxWeaveData"):
        candidate = drive / name
        try:
            if not candidate.exists() or not any(candidate.iterdir()):
                return candidate.resolve()
        except OSError:
            continue
    return None


def plan_initial_setup(
    *,
    application_root: Path,
    drive_roots: list[Path] | None = None,
    pointer_paths: tuple[Path, ...] = (),
    extra_data_candidates: tuple[Path, ...] = (),
) -> InitialSetup:
    drives = drive_roots if drive_roots is not None else fixed_drive_roots()
    existing = discover_existing_data_root(
        drives,
        pointer_paths=pointer_paths,
        extra_candidates=extra_data_candidates,
    )
    if existing:
        runtime = discover_runtime_for_data_root(
            existing,
            application_root=application_root,
            drive_roots=drives,
        )
        return InitialSetup(existing, True, runtime, "existing_data")
    target = choose_automatic_data_root(drives)
    if target is None:
        return InitialSetup(None, False, reason="no_suitable_drive")
    runtime = discover_existing_runtime(drives, application_root=application_root)
    return InitialSetup(target, False, runtime, "new_automatic_data")
