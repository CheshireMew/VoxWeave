from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .batch_discovery import matches_variant
from .batch_variants import filesystem_slug
from .capabilities import VIDEO_EXTENSIONS
from .hashing import sha256_file


def output_path(
    rule: dict[str, Any],
    variant_or_source: dict[str, Any] | Path,
    source_or_hash: Path | str,
    source_hash: str | None = None,
) -> Path:
    if source_hash is None:
        source = Path(variant_or_source)
        source_hash = str(source_or_hash)
        variant = list(
            rule.get("variants")
            or [
                {
                    "name": "default",
                    "model_id": rule["model_id"],
                    "preset_name": rule["preset_name"],
                    "output_format": rule.get("output_format", "auto"),
                }
            ]
        )[0]
    else:
        variant = dict(variant_or_source)
        source = Path(source_or_hash)
    relative = source.relative_to(Path(rule["input_root"]))
    model_slug = filesystem_slug(variant["model_id"])
    preset_slug = filesystem_slug(variant["preset_name"])
    variant_slug = filesystem_slug(variant["name"])
    output_format = str(variant.get("output_format") or rule.get("output_format") or "auto")
    suffix = (
        source.suffix
        if output_format == "auto" and source.suffix.casefold() in VIDEO_EXTENSIONS
        else f".{output_format}"
        if output_format != "auto"
        else ".wav"
    )
    source_type = source.suffix.casefold().removeprefix(".")
    naming_template = str(
        rule.get("naming_template") or "{stem}_{source_ext}_{model}_{preset}_{hash}"
    )
    name = filesystem_slug(
        naming_template.format(
            stem=source.stem,
            source_ext=source_type,
            model=model_slug,
            preset=preset_slug,
            variant=variant_slug,
            hash=source_hash[:12],
        )
    )
    if len(rule.get("variants") or []) > 1 and "{variant}" not in naming_template:
        name = f"{name}_{variant_slug}"
    parent = relative.parent if rule.get("preserve_structure", True) else Path()
    return Path(rule["output_root"]) / parent / f"{name}{suffix}"


def resolve_collision(rule: dict[str, Any], output: Path) -> tuple[Path, bool]:
    if not output.exists():
        return output, False
    policy = rule.get("collision_policy", "skip")
    if policy == "overwrite":
        return output, True
    if policy == "version":
        index = 2
        while True:
            candidate = output.with_name(f"{output.stem}-{index}{output.suffix}")
            if not candidate.exists():
                return candidate, False
            index += 1
    return output, False


def plan_outputs(
    rule: dict[str, Any],
    files: list[Path],
    progress: Callable[[float, str, str], None],
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    total_bytes = 0
    collisions = 0
    examples = []
    variants = list(rule.get("variants") or [])
    planned = [
        (source, variant)
        for source in files
        for variant in variants
        if matches_variant(rule, variant, source)
    ]
    total = max(1, len(planned))
    unique_sources: set[str] = set()
    for index, (source, variant) in enumerate(planned, start=1):
        if cancelled():
            raise InterruptedError("batch plan cancelled")
        stat = source.stat()
        if str(source) not in unique_sources:
            total_bytes += stat.st_size
            unique_sources.add(str(source))
        source_hash = sha256_file(source, cancelled=cancelled)
        raw_output = output_path(rule, variant, source, source_hash)
        collision = raw_output.exists()
        collisions += int(collision)
        resolved, overwrite = resolve_collision(rule, raw_output)
        if len(examples) < 50:
            examples.append(
                {
                    "input": str(source),
                    "variant": variant["name"],
                    "output": str(resolved),
                    "size_bytes": stat.st_size,
                    "collision": collision,
                    "action": (
                        "overwrite"
                        if overwrite
                        else "skip"
                        if collision and rule.get("collision_policy") == "skip"
                        else "create"
                    ),
                }
            )
        progress(index / total, "planning", f"planned {index} of {len(planned)} outputs")
    return {
        "batch_id": str(rule["id"]),
        "file_count": len(files),
        "output_count": len(planned),
        "total_bytes": total_bytes,
        "collisions": collisions,
        "examples": examples,
    }
