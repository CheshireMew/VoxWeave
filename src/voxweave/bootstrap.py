from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .config import LOCAL_POINTER, Settings


def optional_path(value: str | None, label: str) -> str | None:
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"{label} does not exist: {path}")
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure a source checkout of VoxWeave")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--rvc-root")
    parser.add_argument("--rvc-python")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    parser.add_argument("--wespeaker-model")
    arguments = parser.parse_args()
    data_root = Path(arguments.data_root).expanduser().resolve()
    if data_root.drive == "" and not data_root.is_absolute():
        raise ValueError("data root must be absolute")
    data_root.mkdir(parents=True, exist_ok=True)
    pointer = {"data_root": str(data_root)}
    LOCAL_POINTER.write_text(
        json.dumps(pointer, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    rvc_root = optional_path(arguments.rvc_root, "RVC root")
    rvc_python = optional_path(arguments.rvc_python, "RVC Python")
    model_roots = [str(Path(rvc_root) / "assets" / "weights")] if rvc_root else []
    settings = Settings(
        data_root=str(data_root),
        rvc_root=rvc_root,
        rvc_python=rvc_python,
        ffmpeg=optional_path(arguments.ffmpeg or shutil.which("ffmpeg"), "FFmpeg"),
        ffprobe=optional_path(arguments.ffprobe or shutil.which("ffprobe"), "FFprobe"),
        wespeaker_model=optional_path(arguments.wespeaker_model, "WeSpeaker model"),
        model_roots=model_roots,
    )
    settings.save()
    print(
        json.dumps(
            {
                "ok": True,
                "data_root": str(data_root),
                "settings": str(settings.config_path),
                "rvc_root": rvc_root,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
