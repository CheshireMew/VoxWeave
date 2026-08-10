from __future__ import annotations

import os
import platform
import shutil
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import PACKAGE_ROOT, Settings
from .process_control import run_logged
from .runtime import (
    PINNED_RVC_REVISION,
    RVC_REPOSITORY,
    RuntimeErrorDetail,
    inspect_runtime,
)
from .staging import archive_failed_staging


@dataclass(frozen=True, slots=True)
class RuntimeLayout:
    root: Path
    source: Path
    venv: Path
    python: Path
    speaker_root: Path

    @classmethod
    def managed(cls, settings: Settings) -> RuntimeLayout:
        root = settings.root / "runtime" / "rvc"
        venv = root / "venv"
        return cls(
            root=root,
            source=root / "source",
            venv=venv,
            python=venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
            speaker_root=root / "components" / "wespeaker-resnet34-lm",
        )


def _run_install_step(
    command: list[str | Path],
    *,
    cancelled: Callable[[], bool],
    log_path: Path,
    env: dict[str, str] | None = None,
) -> None:
    completed = run_logged(
        command,
        cancelled=cancelled,
        log_path=log_path,
        env=env,
    )
    if completed.returncode != 0:
        tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
        raise RuntimeErrorDetail(
            f"install command failed with exit code {completed.returncode}: {tail.strip()}"
        )


def _attach_supplied_runtime(
    settings: Settings,
    arguments: dict[str, Any],
    cancelled: Callable[[], bool],
) -> dict[str, Any] | None:
    supplied_root = arguments.get("rvc_root")
    if not supplied_root:
        return None
    root = Path(supplied_root).expanduser().resolve()
    if (
        not (root / "configs" / "config.py").is_file()
        or not (root / "infer" / "vc" / "modules.py").is_file()
    ):
        raise RuntimeErrorDetail(f"official RVC inference modules not found under {root}")
    weight_roots = list(settings.weight_roots or [])
    index_roots = list(settings.index_roots or [])
    for roots, candidate in (
        (weight_roots, root / "assets" / "weights"),
        (index_roots, root / "assets" / "indices"),
    ):
        if str(candidate) not in roots:
            roots.append(str(candidate))
    supplied_python = arguments.get("rvc_python")
    candidate = settings.updated(
        rvc_root=str(root),
        rvc_python=str(Path(supplied_python).resolve()) if supplied_python else None,
        weight_roots=weight_roots,
        index_roots=index_roots,
    )
    runtime = inspect_runtime(candidate, cancelled)
    if cancelled():
        raise InterruptedError("task cancellation requested")
    if not runtime["ready"]:
        raise RuntimeErrorDetail(runtime.get("error") or "supplied RVC runtime is not ready")
    settings.update(
        rvc_root=candidate.rvc_root,
        rvc_python=candidate.rvc_python,
        weight_roots=candidate.weight_roots,
        index_roots=candidate.index_roots,
    )
    return runtime


def _reuse_existing_runtime(
    settings: Settings,
    layout: RuntimeLayout,
    cancelled: Callable[[], bool],
) -> dict[str, Any] | None:
    if not layout.root.exists():
        return None
    speaker_model = layout.speaker_root / "voxceleb_resnet34_LM.onnx"
    candidate = settings.updated(
        rvc_root=str(layout.source),
        rvc_python=str(layout.python),
        wespeaker_model=str(speaker_model) if speaker_model.is_file() else settings.wespeaker_model,
    )
    report = inspect_runtime(candidate, cancelled)
    if report["ready"]:
        settings.update(
            rvc_root=candidate.rvc_root,
            rvc_python=candidate.rvc_python,
            wespeaker_model=candidate.wespeaker_model,
        )
        return report
    failed_root = settings.root / "runtime" / "failed"
    failed_root.mkdir(parents=True, exist_ok=True)
    layout.root.replace(failed_root / f"incomplete-{uuid.uuid4().hex}")
    return None


def _checkout_runtime_source(
    staging: Path,
    cancelled: Callable[[], bool],
    log_path: Path,
    progress: Callable[[float, str, str | None], None],
) -> tuple[Path, Path, Path]:
    source = staging / "source"
    venv = staging / "venv"
    progress(0.05, "download", "cloning pinned RVC source")
    _run_install_step(
        ["git", "clone", "--filter=blob:none", RVC_REPOSITORY, source],
        cancelled=cancelled,
        log_path=log_path,
    )
    _run_install_step(
        ["git", "-C", source, "checkout", PINNED_RVC_REVISION],
        cancelled=cancelled,
        log_path=log_path,
    )
    progress(0.25, "environment", "creating Python environment")
    _run_install_step(
        [sys.executable, "-m", "venv", venv],
        cancelled=cancelled,
        log_path=log_path,
    )
    runtime_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return source, venv, runtime_python


def _install_dependencies(
    settings: Settings,
    source: Path,
    runtime_python: Path,
    cancelled: Callable[[], bool],
    log_path: Path,
    progress: Callable[[float, str, str | None], None],
) -> dict[str, str]:
    requirements = source / (
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
    for command in (
        [runtime_python, "-m", "pip", "install", "-r", requirements],
        [runtime_python, "-m", "pip", "install", "huggingface-hub==0.36.2"],
        [runtime_python, "-m", "pip", "install", "silero-vad==6.2.1"],
    ):
        _run_install_step(
            command,
            cancelled=cancelled,
            log_path=log_path,
            env=env,
        )
    return env


def _install_assets(
    settings: Settings,
    arguments: dict[str, Any],
    staging: Path,
    source: Path,
    runtime_python: Path,
    env: dict[str, str],
    cancelled: Callable[[], bool],
    log_path: Path,
    progress: Callable[[float, str, str | None], None],
) -> tuple[bool, Path]:
    progress(0.72, "assets", "downloading required official inference assets")
    env["HF_HOME"] = str(settings.cache_dir / "huggingface")
    command: list[str | Path] = [
        runtime_python,
        PACKAGE_ROOT / "runtime_assets_worker.py",
        "--rvc-root",
        source,
    ]
    if arguments.get("install_separation", False):
        command.append("--with-separation")
    install_speaker = arguments.get("install_speaker_model", True)
    speaker_root = staging / "components" / "wespeaker-resnet34-lm"
    if install_speaker:
        command.extend(["--speaker-root", speaker_root])
    _run_install_step(
        command,
        cancelled=cancelled,
        log_path=log_path,
        env=env,
    )
    return install_speaker, speaker_root


def _staging_candidate(
    settings: Settings,
    source: Path,
    runtime_python: Path,
    install_speaker: bool,
    speaker_root: Path,
) -> Settings:
    weight_roots = list(settings.weight_roots or [])
    index_roots = list(settings.index_roots or [])
    for roots, candidate in (
        (weight_roots, source / "assets" / "weights"),
        (index_roots, source / "assets" / "indices"),
    ):
        if str(candidate) not in roots:
            roots.append(str(candidate))
    return settings.updated(
        rvc_root=str(source),
        rvc_python=str(runtime_python),
        weight_roots=weight_roots,
        index_roots=index_roots,
        wespeaker_model=(
            str(speaker_root / "voxceleb_resnet34_LM.onnx")
            if install_speaker
            else settings.wespeaker_model
        ),
    )


def _publish_runtime(
    settings: Settings,
    layout: RuntimeLayout,
    staging: Path,
    candidate: Settings,
    install_speaker: bool,
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    staging.replace(layout.root)
    final_weight_roots = [
        str(layout.source / "assets" / "weights")
        if value == str(Path(candidate.rvc_root or "") / "assets" / "weights")
        else value
        for value in candidate.weight_roots or []
    ]
    final_index_roots = [
        str(layout.source / "assets" / "indices")
        if value == str(Path(candidate.rvc_root or "") / "assets" / "indices")
        else value
        for value in candidate.index_roots or []
    ]
    final_candidate = settings.updated(
        rvc_root=str(layout.source),
        rvc_python=str(layout.python),
        weight_roots=final_weight_roots,
        index_roots=final_index_roots,
        wespeaker_model=(
            str(layout.speaker_root / "voxceleb_resnet34_LM.onnx")
            if install_speaker
            else settings.wespeaker_model
        ),
    )
    runtime = inspect_runtime(final_candidate, cancelled)
    if not runtime["ready"]:
        failed_root = settings.root / "runtime" / "failed"
        failed_root.mkdir(parents=True, exist_ok=True)
        layout.root.replace(failed_root / f"post-publish-{uuid.uuid4().hex}")
        raise RuntimeErrorDetail(runtime.get("error") or "published runtime failed doctor")
    settings.update(
        rvc_root=final_candidate.rvc_root,
        rvc_python=final_candidate.rvc_python,
        weight_roots=final_candidate.weight_roots,
        index_roots=final_candidate.index_roots,
        wespeaker_model=final_candidate.wespeaker_model,
    )
    return runtime


def install_runtime(
    settings: Settings,
    arguments: dict[str, Any],
    progress: Callable[[float, str, str | None], None],
    cancelled: Callable[[], bool],
    task_id: str,
) -> dict[str, Any]:
    if cancelled():
        raise InterruptedError("task cancellation requested")
    supplied = _attach_supplied_runtime(settings, arguments, cancelled)
    if supplied is not None:
        return supplied
    layout = RuntimeLayout.managed(settings)
    existing = _reuse_existing_runtime(settings, layout, cancelled)
    if existing is not None:
        return existing
    staging = settings.root / "runtime" / "staging" / task_id
    staging.mkdir(parents=True, exist_ok=False)
    with archive_failed_staging(
        staging,
        settings.root / "runtime" / "failed",
        f"staging-{task_id}",
    ):
        log_path = staging / "install.log"
        source, _venv, runtime_python = _checkout_runtime_source(
            staging,
            cancelled,
            log_path,
            progress,
        )
        env = _install_dependencies(
            settings,
            source,
            runtime_python,
            cancelled,
            log_path,
            progress,
        )
        install_speaker, speaker_root = _install_assets(
            settings,
            arguments,
            staging,
            source,
            runtime_python,
            env,
            cancelled,
            log_path,
            progress,
        )
        progress(0.9, "doctor", "validating installed runtime")
        candidate = _staging_candidate(
            settings,
            source,
            runtime_python,
            install_speaker,
            speaker_root,
        )
        report = inspect_runtime(candidate, cancelled)
        if cancelled():
            raise InterruptedError("task cancellation requested")
        if not report["ready"]:
            raise RuntimeErrorDetail(report.get("error") or "installed runtime failed doctor")
        return _publish_runtime(
            settings,
            layout,
            staging,
            candidate,
            install_speaker,
            cancelled,
        )
