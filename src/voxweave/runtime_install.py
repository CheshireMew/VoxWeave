from __future__ import annotations

import json
import os
import platform
import shutil
import tarfile
import time
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from .config import PACKAGE_ROOT, Settings
from .hashing import sha256_file
from .process_control import run_logged
from .runtime import RuntimeErrorDetail, inspect_runtime
from .staging import archive_failed_staging

GIBIBYTE = 1024**3


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


def _component_manifest() -> dict[str, Any]:
    manifest = json.loads(
        (PACKAGE_ROOT / "resources" / "runtime_components.json").read_text(encoding="utf-8")
    )
    if platform.system() != "Windows" or platform.machine().casefold() not in {
        "amd64",
        "x86_64",
    }:
        raise RuntimeErrorDetail("the managed installer currently supports Windows x64 only")
    return dict(manifest["windows-x86_64"])


def _require_install_space(settings: Settings) -> None:
    nvidia = platform.system() == "Windows" and bool(shutil.which("nvidia-smi"))
    required = (12 if nvidia else 6) * GIBIBYTE
    free = shutil.disk_usage(settings.root).free
    if free < required:
        raise RuntimeErrorDetail(
            "数据目录可用空间不足 / Not enough free space in the data directory: "
            f"{free / GIBIBYTE:.1f} GiB available, {required / GIBIBYTE:.0f} GiB required"
        )


def _archive_existing(path: Path, failed_root: Path, label: str) -> None:
    if not path.exists():
        return
    failed_root.mkdir(parents=True, exist_ok=True)
    path.replace(failed_root / f"{label}-{uuid.uuid4().hex}")


def _replace_directory(source: Path, destination: Path) -> None:
    """Publish a directory after transient Windows scanners release their handles."""

    last_error: OSError | None = None
    for delay in (0.0, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0):
        if delay:
            time.sleep(delay)
        try:
            source.replace(destination)
            return
        except OSError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def _download_verified(
    settings: Settings,
    component: dict[str, Any],
    cancelled: Callable[[], bool],
    progress: Callable[[float, str, str | None], None],
    progress_start: float,
    progress_end: float,
) -> Path:
    target = settings.downloads_dir / "components" / str(component["filename"])
    expected_size = int(component["size_bytes"])
    expected_hash = str(component["sha256"]).casefold()
    if target.is_file():
        if (
            target.stat().st_size == expected_size
            and sha256_file(target).casefold() == expected_hash
        ):
            return target
        _archive_existing(
            target,
            settings.downloads_dir / "failed",
            f"invalid-{target.name}",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.part-{uuid.uuid4().hex}")
    request = urllib.request.Request(str(component["url"]), headers={"User-Agent": "VoxWeave/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("xb") as output:
            received = 0
            while chunk := response.read(1024 * 1024):
                if cancelled():
                    raise InterruptedError("task cancellation requested")
                output.write(chunk)
                received += len(chunk)
                fraction = min(1.0, received / max(1, expected_size))
                progress(
                    progress_start + (progress_end - progress_start) * fraction,
                    "download",
                    f"{component['filename']}: {received}/{expected_size} bytes",
                )
        if partial.stat().st_size != expected_size:
            raise ValueError(f"downloaded size mismatch for {component['filename']}")
        if sha256_file(partial).casefold() != expected_hash:
            raise ValueError(f"downloaded hash mismatch for {component['filename']}")
        partial.replace(target)
        return target
    except Exception:
        _archive_existing(
            partial,
            settings.downloads_dir / "failed",
            f"partial-{target.name}",
        )
        raise


def _ensure_managed_python(
    settings: Settings,
    component: dict[str, Any],
    cancelled: Callable[[], bool],
    progress: Callable[[float, str, str | None], None],
) -> Path:
    root = settings.components_dir / f"python-{component['version']}"
    python = root / "python.exe"
    if python.is_file():
        return python
    if root.exists():
        _archive_existing(root, settings.components_dir / "failed", "python-incomplete")
    archive = _download_verified(settings, component, cancelled, progress, 0.03, 0.08)
    progress(0.09, "environment", "extracting private Python 3.12 runtime")
    staging = settings.components_dir / "staging" / f"python-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            if not members or any(
                member.name != "python" and not member.name.startswith("python/")
                for member in members
            ):
                raise RuntimeErrorDetail("Python archive has an unexpected layout")
            bundle.extractall(staging, filter="data")
        extracted = staging / "python"
        if not (extracted / "python.exe").is_file():
            raise RuntimeErrorDetail("Python archive does not contain python.exe")
        extracted.replace(root)
        staging.rmdir()
    except Exception:
        _archive_existing(
            staging,
            settings.components_dir / "failed",
            "python-staging",
        )
        raise
    if not python.is_file():
        raise RuntimeErrorDetail("managed Python extraction completed without python.exe")
    return python


def _ensure_managed_ffmpeg(
    settings: Settings,
    component: dict[str, Any],
    cancelled: Callable[[], bool],
    progress: Callable[[float, str, str | None], None],
) -> tuple[Path, Path]:
    root = settings.components_dir / f"ffmpeg-{component['version']}"
    ffmpeg = root / "ffmpeg.exe"
    ffprobe = root / "ffprobe.exe"
    if ffmpeg.is_file() and ffprobe.is_file():
        return ffmpeg, ffprobe
    if root.exists():
        _archive_existing(root, settings.components_dir / "failed", "ffmpeg-incomplete")
    archive = _download_verified(settings, component, cancelled, progress, 0.10, 0.18)
    staging = settings.components_dir / "staging" / f"ffmpeg-{uuid.uuid4().hex}"
    staging.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = {
                Path(info.filename).name.casefold(): info
                for info in bundle.infolist()
                if not info.is_dir()
                and Path(info.filename).name.casefold() in {"ffmpeg.exe", "ffprobe.exe"}
                and "/bin/" in info.filename.replace("\\", "/")
            }
            if set(members) != {"ffmpeg.exe", "ffprobe.exe"}:
                raise RuntimeErrorDetail(
                    "FFmpeg archive does not contain ffmpeg.exe and ffprobe.exe"
                )
            for name, info in members.items():
                with bundle.open(info) as source, (staging / name).open("xb") as output:
                    shutil.copyfileobj(source, output)
        staging.replace(root)
    except Exception:
        _archive_existing(staging, settings.components_dir / "failed", "ffmpeg-staging")
        raise
    return ffmpeg, ffprobe


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
    if report["ready"] or bool((report.get("doctor") or {}).get("ok")):
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
    settings: Settings,
    component: dict[str, Any],
    staging: Path,
    bootstrap_python: Path,
    cancelled: Callable[[], bool],
    log_path: Path,
    progress: Callable[[float, str, str | None], None],
) -> tuple[Path, Path, Path]:
    source = staging / "source"
    venv = staging / "venv"
    progress(0.19, "download", "downloading pinned RVC source")
    archive = _download_verified(settings, component, cancelled, progress, 0.19, 0.23)
    source.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        for info in bundle.infolist():
            parts = PurePosixPath(info.filename).parts
            if len(parts) < 2:
                continue
            relative = Path(*parts[1:])
            destination = source / relative
            if info.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(info) as input_stream, destination.open("xb") as output:
                shutil.copyfileobj(input_stream, output)
    (source / ".voxweave-rvc-revision").write_text(
        str(component["revision"]) + "\n", encoding="utf-8"
    )
    progress(0.25, "environment", "creating Python environment")
    _run_install_step(
        [bootstrap_python, "-m", "venv", venv],
        cancelled=cancelled,
        log_path=log_path,
    )
    runtime_python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return source, venv, runtime_python


def _install_dependencies(
    settings: Settings,
    runtime_python: Path,
    install_separation: bool,
    cancelled: Callable[[], bool],
    log_path: Path,
    progress: Callable[[float, str, str | None], None],
) -> dict[str, str]:
    use_nvidia = platform.system() == "Windows" and bool(shutil.which("nvidia-smi"))
    requirements = PACKAGE_ROOT / "resources" / "runtime_requirements_windows.txt"
    env = os.environ.copy()
    env["PIP_CACHE_DIR"] = str(settings.root / "pip-cache")
    env["TMP"] = str(settings.root / "temp")
    env["TEMP"] = str(settings.root / "temp")
    Path(env["TEMP"]).mkdir(parents=True, exist_ok=True)
    progress(0.35, "dependencies", "installing VoxWeave inference dependencies")
    commands: list[list[str | Path]] = []
    if use_nvidia:
        commands.append(
            [
                runtime_python,
                "-m",
                "pip",
                "install",
                "torch==2.7.1+cu118",
                "torchaudio==2.7.1+cu118",
                "--index-url",
                "https://mirrors.nju.edu.cn/pytorch/whl/cu118",
                "--extra-index-url",
                "https://mirrors.pku.edu.cn/pypi/simple",
            ]
        )
    else:
        commands.append(
            [
                runtime_python,
                "-m",
                "pip",
                "install",
                "torch==2.4.1+cpu",
                "torchaudio==2.4.1+cpu",
                "torchvision==0.19.1+cpu",
                "torch-directml==0.2.5.dev240914",
                "--index-url",
                "https://mirrors.nju.edu.cn/pytorch/whl/cpu",
                "--extra-index-url",
                "https://mirrors.pku.edu.cn/pypi/simple",
            ]
        )
    commands.extend(([runtime_python, "-m", "pip", "install", "-r", requirements],))
    if install_separation:
        if not use_nvidia:
            raise RuntimeErrorDetail(
                "optional PyMSS separation currently requires an NVIDIA runtime"
            )
        commands.append(
            [
                runtime_python,
                "-m",
                "pip",
                "install",
                "pymss==2.0.14",
                "pymss-core==0.1.4",
            ]
        )
    for command in commands:
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
        "-B",
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
    ffmpeg: Path,
    ffprobe: Path,
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
        ffmpeg=str(ffmpeg),
        ffprobe=str(ffprobe),
    )


def _publish_runtime(
    settings: Settings,
    layout: RuntimeLayout,
    staging: Path,
    candidate: Settings,
    install_speaker: bool,
    cancelled: Callable[[], bool],
) -> dict[str, Any]:
    _replace_directory(staging, layout.root)
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
        ffmpeg=candidate.ffmpeg,
        ffprobe=candidate.ffprobe,
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
        ffmpeg=final_candidate.ffmpeg,
        ffprobe=final_candidate.ffprobe,
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
    current = inspect_runtime(settings, cancelled)
    if current["ready"]:
        return current
    supplied = _attach_supplied_runtime(settings, arguments, cancelled)
    if supplied is not None:
        return supplied
    layout = RuntimeLayout.managed(settings)
    existing = _reuse_existing_runtime(settings, layout, cancelled)
    if existing is not None and existing["ready"]:
        return existing
    manifest = _component_manifest()
    ffmpeg: Path | None = None
    ffprobe: Path | None = None
    reusable_report = existing or current
    if bool((reusable_report.get("doctor") or {}).get("ok")):
        ffmpeg, ffprobe = _ensure_managed_ffmpeg(
            settings,
            manifest["ffmpeg"],
            cancelled,
            progress,
        )
        existing_candidate = settings.updated(
            ffmpeg=str(ffmpeg),
            ffprobe=str(ffprobe),
        )
        existing_report = inspect_runtime(existing_candidate, cancelled)
        if existing_report["ready"]:
            settings.update(ffmpeg=str(ffmpeg), ffprobe=str(ffprobe))
            return existing_report
    _require_install_space(settings)
    bootstrap_python = _ensure_managed_python(
        settings,
        manifest["python"],
        cancelled,
        progress,
    )
    if ffmpeg is None or ffprobe is None:
        ffmpeg, ffprobe = _ensure_managed_ffmpeg(
            settings,
            manifest["ffmpeg"],
            cancelled,
            progress,
        )
    staging = settings.root / "runtime" / "staging" / task_id
    staging.mkdir(parents=True, exist_ok=False)
    with archive_failed_staging(
        staging,
        settings.root / "runtime" / "failed",
        f"staging-{task_id}",
    ):
        log_path = staging / "install.log"
        source, _venv, runtime_python = _checkout_runtime_source(
            settings,
            manifest["rvc_source"],
            staging,
            bootstrap_python,
            cancelled,
            log_path,
            progress,
        )
        env = _install_dependencies(
            settings,
            runtime_python,
            bool(arguments.get("install_separation", False)),
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
            ffmpeg,
            ffprobe,
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
