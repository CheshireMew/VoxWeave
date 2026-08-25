from __future__ import annotations

import os
import stat as stat_module
from collections.abc import Callable
from pathlib import Path
from typing import Any

TEMP_SUFFIXES = (".tmp", ".part", ".crdownload", ".download")


def _is_hidden(path: Path) -> bool:
    if path.name.startswith("."):
        return True
    attributes = getattr(path.stat(), "st_file_attributes", 0)
    hidden_flag = getattr(stat_module, "FILE_ATTRIBUTE_HIDDEN", 0x2)
    return bool(attributes & hidden_flag) if os.name == "nt" else False


def _allowed_relative(rule: dict[str, Any], relative: Path) -> bool:
    include = list(rule.get("include_globs") or [])
    exclude = list(rule.get("exclude_globs") or [])
    if include and not any(relative.match(pattern) for pattern in include):
        return False
    return not any(relative.match(pattern) for pattern in exclude)


def matches_variant(rule: dict[str, Any], variant: dict[str, Any], path: Path) -> bool:
    relative = path.relative_to(Path(rule["input_root"]))
    extensions = {value.casefold() for value in variant.get("extensions") or []}
    if extensions and path.suffix.casefold() not in extensions:
        return False
    include = list(variant.get("include_globs") or [])
    exclude = list(variant.get("exclude_globs") or [])
    if include and not any(relative.match(pattern) for pattern in include):
        return False
    return not any(relative.match(pattern) for pattern in exclude)


def discover_files(
    rule: dict[str, Any],
    cancelled: Callable[[], bool] | None = None,
    scan_directory: Callable[[Path], Any] = os.scandir,
) -> list[Path]:
    root = Path(rule["input_root"])
    extensions = {value.casefold() for value in rule["extensions"]}
    files: list[Path] = []
    pending = [root]
    while pending:
        if cancelled and cancelled():
            raise InterruptedError("task cancellation requested")
        current = pending.pop()
        try:
            entries = list(scan_directory(current))
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for entry in entries:
            path = Path(entry.path)
            try:
                if entry.name.startswith(".") or _is_hidden(path):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if rule["recursive"]:
                        pending.append(path)
                elif (
                    entry.is_file(follow_symlinks=False)
                    and path.suffix.casefold() in extensions
                    and not path.name.casefold().endswith(TEMP_SUFFIXES)
                    and _allowed_relative(rule, path.relative_to(root))
                ):
                    files.append(path)
            except (FileNotFoundError, PermissionError, OSError):
                continue
    return sorted(files)


def matches_rule(rule: dict[str, Any], path: Path) -> bool:
    root = Path(rule["input_root"])
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    if not rule["recursive"] and len(relative.parts) != 1:
        return False
    if (
        path.suffix.casefold()
        not in {value.casefold() for value in rule["extensions"]}
        or path.name.casefold().endswith(TEMP_SUFFIXES)
        or not _allowed_relative(rule, relative)
    ):
        return False
    current = root
    try:
        for part in relative.parts:
            current /= part
            if _is_hidden(current):
                return False
        return path.is_file()
    except (FileNotFoundError, PermissionError, OSError):
        return False
