from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import PACKAGE_ROOT, Settings
from .hashing import sha256_file

PINNED_RVC_REVISION = "4338f12c3c28c80b3ac015e2d0df66c41592746d"
PINNED_ASSET_REVISION = "e6d0c1a17da07c33557852f9dfa2bd44cc75737d"
RVC_REPOSITORY = "https://github.com/RVC-Project/Retrieval-based-Voice-Conversion-WebUI.git"


class RuntimeErrorDetail(RuntimeError):
    pass


def _run_json(command: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    output = completed.stdout.strip().splitlines()
    if completed.returncode != 0:
        raise RuntimeErrorDetail(completed.stderr.strip() or completed.stdout.strip())
    if not output:
        raise RuntimeErrorDetail(f"command returned no JSON: {command[0]}")
    return json.loads(output[-1])


def resolve_rvc_python(settings: Settings) -> Path | None:
    if settings.rvc_python:
        path = Path(settings.rvc_python)
        if path.is_file():
            return path
    if settings.rvc_root:
        root = Path(settings.rvc_root)
        candidates = (
            root / ".venv" / "Scripts" / "python.exe",
            root / ".venv" / "bin" / "python",
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
    return None


def resolve_rvc_entry(settings: Settings) -> Path | None:
    if not settings.rvc_root:
        return None
    root = Path(settings.rvc_root)
    required = (root / "configs" / "config.py", root / "infer" / "vc" / "modules.py")
    entry = PACKAGE_ROOT / "rvc_worker.py"
    return entry if entry.is_file() and all(path.is_file() for path in required) else None


def inspect_runtime(settings: Settings) -> dict[str, Any]:
    python = resolve_rvc_python(settings)
    entry = resolve_rvc_entry(settings)
    payload: dict[str, Any] = {
        "platform": platform.platform(),
        "python": sys.version,
        "data_root": str(settings.root),
        "rvc_root": settings.rvc_root,
        "rvc_python": str(python) if python else None,
        "ffmpeg": settings.ffmpeg,
        "ffprobe": settings.ffprobe,
        "hardware_backend": settings.hardware_backend,
        "ready": False,
        "rvc_revision": None,
        "doctor": None,
        "components": {},
    }
    if settings.rvc_root and (Path(settings.rvc_root) / ".git").exists():
        revision = subprocess.run(
            ["git", "-C", settings.rvc_root, "rev-parse", "HEAD"],
            check=False,
            text=True,
            capture_output=True,
        )
        if revision.returncode == 0:
            payload["rvc_revision"] = revision.stdout.strip()
    if python and entry:
        try:
            payload["doctor"] = _run_json(
                [
                    str(python),
                    str(entry),
                    "--rvc-root",
                    settings.rvc_root,
                    "doctor",
                ],
                cwd=Path(settings.rvc_root),
            )
            payload["components"]["python_runtime"] = _run_json(
                [str(python), str(PACKAGE_ROOT / "runtime_worker.py")], cwd=entry.parent
            )
            payload["ready"] = bool(
                payload["doctor"].get("ok")
                and settings.ffmpeg
                and Path(settings.ffmpeg).is_file()
                and settings.ffprobe
                and Path(settings.ffprobe).is_file()
            )
        except (RuntimeErrorDetail, ValueError) as exc:
            payload["error"] = str(exc)
    separation_model = (
        Path(settings.rvc_root)
        / "assets"
        / "pymss_weights"
        / "model_bs_roformer_ep_368_sdr_12.9628.ckpt"
        if settings.rvc_root
        else None
    )
    payload["components"]["source_separation"] = {
        "backend": settings.separation_backend,
        "model_id": settings.separation_model_id,
        "ready": bool(separation_model and separation_model.is_file()),
        "model_path": str(separation_model) if separation_model else None,
        "model_sha256": sha256_file(separation_model)
        if separation_model and separation_model.is_file()
        else None,
        "source": "https://huggingface.co/baicai1145/pymss",
        "code_license_spdx": "MIT",
        "model_license_spdx": "LicenseRef-Unknown",
    }
    speaker_model = Path(settings.wespeaker_model) if settings.wespeaker_model else None
    payload["components"]["speaker_embedding"] = {
        "backend": "wespeaker-onnx",
        "ready": bool(speaker_model and speaker_model.is_file()),
        "model_path": str(speaker_model) if speaker_model else None,
        "model_sha256": sha256_file(speaker_model)
        if speaker_model and speaker_model.is_file()
        else None,
        "code_license_spdx": "Apache-2.0",
        "model_license_spdx": "CC-BY-4.0",
        "source": "https://huggingface.co/Wespeaker/wespeaker-resnet34-LM",
        "revision": "f0c48c298fd835726c27956a5d617bad7115627e",
    }
    payload["pinned_rvc_revision"] = PINNED_RVC_REVISION
    payload["pinned_asset_revision"] = PINNED_ASSET_REVISION
    payload["rvc_revision_matches_pin"] = payload["rvc_revision"] == PINNED_RVC_REVISION
    return payload


def install_runtime(
    settings: Settings,
    arguments: dict[str, Any],
    progress: Callable[[float, str, str | None], None],
) -> dict[str, Any]:
    supplied_root = arguments.get("rvc_root")
    supplied_python = arguments.get("rvc_python")
    if supplied_root:
        root = Path(supplied_root).expanduser().resolve()
        if (
            not (root / "configs" / "config.py").is_file()
            or not (root / "infer" / "vc" / "modules.py").is_file()
        ):
            raise RuntimeErrorDetail(f"official RVC inference modules not found under {root}")
        settings.rvc_root = str(root)
        settings.rvc_python = str(Path(supplied_python).resolve()) if supplied_python else None
        runtime = inspect_runtime(settings)
        if not runtime["ready"]:
            raise RuntimeErrorDetail(runtime.get("error") or "supplied RVC runtime is not ready")
        weight_root = root / "assets" / "weights"
        if str(weight_root) not in settings.model_roots:
            settings.model_roots.append(str(weight_root))
        settings.save()
        progress(1.0, "completed", "external RVC runtime registered")
        return runtime

    runtime_root = settings.root / "runtime" / "rvc"
    source_root = runtime_root / "source"
    venv_root = runtime_root / "venv"
    if source_root.exists() and any(source_root.iterdir()):
        raise RuntimeErrorDetail(f"runtime source directory already exists: {source_root}")
    runtime_root.mkdir(parents=True, exist_ok=True)
    progress(0.05, "download", "cloning pinned RVC source")
    subprocess.run(
        ["git", "clone", "--filter=blob:none", RVC_REPOSITORY, str(source_root)],
        check=True,
    )
    subprocess.run(["git", "-C", str(source_root), "checkout", PINNED_RVC_REVISION], check=True)
    progress(0.25, "environment", "creating Python environment")
    subprocess.run([sys.executable, "-m", "venv", str(venv_root)], check=True)
    runtime_python = venv_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    requirements = source_root / (
        "requirments_cu118_py312.txt"
        if platform.system() == "Windows" and shutil.which("nvidia-smi")
        else "requirments_cpu_py312.txt"
    )
    env = os.environ.copy()
    env["PIP_CACHE_DIR"] = str(settings.root / "pip-cache")
    env["TMP"] = str(settings.root / "temp")
    env["TEMP"] = str(settings.root / "temp")
    Path(env["TEMP"]).mkdir(parents=True, exist_ok=True)
    progress(0.35, "dependencies", f"installing {requirements.name}")
    subprocess.run(
        [str(runtime_python), "-m", "pip", "install", "-r", str(requirements)],
        check=True,
        env=env,
    )
    subprocess.run(
        [
            str(runtime_python),
            "-m",
            "pip",
            "install",
            "huggingface-hub==0.36.2",
        ],
        check=True,
        env=env,
    )
    progress(0.72, "assets", "downloading required official inference assets")
    env["HF_HOME"] = str(settings.cache_dir / "huggingface")
    asset_command = [
        str(runtime_python),
        str(PACKAGE_ROOT / "runtime_assets_worker.py"),
        "--rvc-root",
        str(source_root),
    ]
    if arguments.get("install_separation", False):
        asset_command.append("--with-separation")
    install_speaker_model = arguments.get("install_speaker_model", True)
    speaker_root = settings.components_dir / "wespeaker-resnet34-lm"
    if install_speaker_model:
        asset_command.extend(["--speaker-root", str(speaker_root)])
    subprocess.run(asset_command, check=True, env=env)
    settings.rvc_root = str(source_root)
    settings.rvc_python = str(runtime_python)
    settings.model_roots.append(str(source_root / "assets" / "weights"))
    if install_speaker_model:
        settings.wespeaker_model = str(speaker_root / "voxceleb_resnet34_LM.onnx")
    settings.save()
    progress(0.9, "doctor", "validating installed runtime")
    runtime = inspect_runtime(settings)
    if not runtime["ready"]:
        raise RuntimeErrorDetail(runtime.get("error") or "installed runtime failed doctor")
    progress(1.0, "completed", "runtime installed")
    return runtime
