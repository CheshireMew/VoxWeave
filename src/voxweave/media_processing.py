from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .config import PACKAGE_ROOT, Settings
from .hashing import sha256_file
from .media_checkpoint import _file_record
from .media_errors import MediaPipelineError
from .media_io import _binary, _run, clip_audio
from .runtime import resolve_rvc_python
from .rvc_engine import RvcEngine

Progress = Callable[[float, str, str | None], None]

def analyze_audio(
    settings: Settings,
    audio_path: Path,
    work_dir: Path,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    python = resolve_rvc_python(settings)
    if not python:
        raise MediaPipelineError("RVC Python runtime is required for Silero VAD")
    analysis_audio = work_dir / "analysis-16k.wav"
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-n",
            "-i",
            str(audio_path),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(analysis_audio),
        ],
        cancelled=cancelled,
    )
    command = [
        str(python),
        "-B",
        str(PACKAGE_ROOT / "analysis_worker.py"),
        "--audio",
        str(analysis_audio),
    ]
    if settings.wespeaker_model and Path(settings.wespeaker_model).is_file():
        command.extend(["--speaker-model", settings.wespeaker_model])
    completed = _run(command, cancelled=cancelled)
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    if not payload.get("ok"):
        raise MediaPipelineError(payload.get("error", "media analysis failed"))
    payload["analysis_audio"] = str(analysis_audio)
    return payload


def create_speaker_samples(
    settings: Settings,
    audio_path: Path,
    segments: list[dict[str, Any]],
    work_dir: Path,
    cancelled: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    sample_dir = work_dir / "speaker-samples"
    sample_dir.mkdir(parents=True, exist_ok=False)
    speakers = sorted({segment["speaker"] for segment in segments})
    samples = []
    for speaker in speakers:
        if cancelled and cancelled():
            raise InterruptedError("task cancellation requested")
        candidates = [segment for segment in segments if segment["speaker"] == speaker]
        segment = max(
            candidates,
            key=lambda item: item["end_seconds"] - item["start_seconds"],
        )
        duration = min(10.0, segment["end_seconds"] - segment["start_seconds"])
        output = sample_dir / f"{speaker}.wav"
        clip_audio(
            settings, audio_path, output, segment["start_seconds"], duration, cancelled
        )
        samples.append(
            {
                "id": speaker,
                "sample_audio": str(output),
                "start_seconds": segment["start_seconds"],
                "duration_seconds": duration,
                "sha256": sha256_file(output),
            }
        )
    return samples


def separate_audio(
    settings: Settings,
    audio_path: Path,
    work_dir: Path,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[Path, Path, dict[str, Any]]:
    if settings.separation_backend != "rvc-pymss":
        raise MediaPipelineError(f"unsupported separation backend: {settings.separation_backend}")
    python = resolve_rvc_python(settings)
    rvc_root = Path(settings.rvc_root or "")
    model_file = rvc_root / "assets" / "pymss_weights" / "model_bs_roformer_ep_368_sdr_12.9628.ckpt"
    if not python or not rvc_root.is_dir() or not model_file.is_file():
        raise MediaPipelineError(
            "mixed and singing modes require the configured RVC PyMSS runtime "
            "and licensed separation model"
        )
    work_dir.mkdir(parents=True, exist_ok=False)
    command = [
        str(python),
        "-B",
        str(PACKAGE_ROOT / "separation_worker.py"),
        "--rvc-root",
        str(rvc_root),
        "--input",
        str(audio_path),
        "--output",
        str(work_dir),
        "--model",
        settings.separation_model_id,
    ]
    completed = _run(command, cancelled=cancelled)
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    if not payload.get("ok"):
        raise MediaPipelineError(payload.get("error", "source separation failed"))
    files = list(work_dir.rglob("*.wav"))
    vocals = next((path for path in files if re.search(r"vocal", path.name, re.I)), None)
    instrumental = next(
        (path for path in files if re.search(r"instrument|no[_ -]?vocal", path.name, re.I)),
        None,
    )
    if not vocals or not instrumental:
        raise MediaPipelineError("separator did not produce vocal and instrumental stems")
    config_file = (
        rvc_root / "assets" / "pymss_weights" / "model_bs_roformer_ep_368_sdr_12.9628.yaml"
    )
    component = {
        "backend": "rvc-pymss",
        "model_id": settings.separation_model_id,
        "model_path": str(model_file),
        "model_sha256": sha256_file(model_file),
        "config_path": str(config_file),
        "config_sha256": sha256_file(config_file),
        "source": "https://huggingface.co/baicai1145/pymss",
        "code_license_spdx": "MIT",
        "model_license_spdx": "LicenseRef-Unknown",
        "distribution_allowed": False,
    }
    return vocals, instrumental, component


def _match_and_align(converted: np.ndarray, original: np.ndarray) -> np.ndarray:
    if converted.ndim > 1:
        converted = converted.mean(axis=1)
    if original.ndim > 1:
        original = original.mean(axis=1)
    if len(converted) != len(original):
        divisor = math.gcd(max(1, len(converted)), max(1, len(original)))
        converted = resample_poly(converted, len(original) // divisor, len(converted) // divisor)
        if len(converted) < len(original):
            converted = np.pad(converted, (0, len(original) - len(converted)))
        converted = converted[: len(original)]
    original_rms = float(np.sqrt(np.mean(np.square(original), dtype=np.float64)))
    converted_rms = float(np.sqrt(np.mean(np.square(converted), dtype=np.float64)))
    if original_rms > 1e-6 and converted_rms > 1e-6:
        converted = converted * max(0.25, min(4.0, original_rms / converted_rms))
    peak = float(np.max(np.abs(converted))) if converted.size else 0.0
    if peak > 0.98:
        converted = converted * (0.98 / peak)
    return converted.astype(np.float32)


def align_audio_file(
    converted_path: Path, original_path: Path, output_path: Path
) -> dict[str, Any]:
    if sf.info(original_path).duration > 120:
        raise MediaPipelineError("in-memory alignment is limited to two-minute preview chunks")
    converted, converted_rate = sf.read(converted_path, dtype="float32", always_2d=False)
    original, original_rate = sf.read(original_path, dtype="float32", always_2d=False)
    if converted_rate != original_rate:
        divisor = math.gcd(converted_rate, original_rate)
        converted = resample_poly(converted, original_rate // divisor, converted_rate // divisor)
    aligned = _match_and_align(converted, original)
    sf.write(output_path, aligned, original_rate, subtype="PCM_24")
    return {
        "path": str(output_path),
        "sha256": sha256_file(output_path),
        "sample_rate": original_rate,
        "samples": len(aligned),
    }


def _quiet_chunk_ranges_file(
    audio_path: Path,
    cancelled: Callable[[], bool],
    *,
    target_seconds: float = 45.0,
    search_seconds: float = 6.0,
) -> tuple[int, int, list[tuple[int, int]]]:
    info = sf.info(audio_path)
    sample_rate = info.samplerate
    total = info.frames
    frame_size = max(1, int(sample_rate * 0.05))
    energies: list[float] = []
    with sf.SoundFile(audio_path) as reader:
        while True:
            if cancelled():
                raise InterruptedError("task cancellation requested")
            block = reader.read(frame_size, dtype="float32", always_2d=True)
            if not len(block):
                break
            mono = block.mean(axis=1)
            energies.append(float(np.mean(np.square(mono), dtype=np.float64)))
    target = int(target_seconds * sample_rate)
    search = int(search_seconds * sample_rate)
    minimum = int(20.0 * sample_rate)
    if total <= target + search:
        return sample_rate, total, [(0, total)]
    boundaries = [0]
    while total - boundaries[-1] > target + search:
        expected = boundaries[-1] + target
        lower = max(boundaries[-1] + minimum, expected - search)
        upper = min(total - minimum, expected + search)
        if upper <= lower:
            break
        first_bin = max(0, lower // frame_size)
        last_bin = min(len(energies) - 1, upper // frame_size)
        if last_bin < first_bin:
            break
        local = energies[first_bin : last_bin + 1]
        boundary = min(total, (first_bin + int(np.argmin(local))) * frame_size)
        if boundary <= boundaries[-1]:
            break
        boundaries.append(boundary)
    boundaries.append(total)
    return sample_rate, total, list(zip(boundaries, boundaries[1:], strict=False))


def _read_mono_range(path: Path, start: int, end: int) -> np.ndarray:
    with sf.SoundFile(path) as reader:
        reader.seek(start)
        audio = reader.read(end - start, dtype="float32", always_2d=True)
    return audio.mean(axis=1)


def _converted_replacement(
    converted_path: Path,
    original: np.ndarray,
    sample_rate: int,
) -> np.ndarray:
    converted, converted_rate = sf.read(
        converted_path, dtype="float32", always_2d=False
    )
    if converted_rate != sample_rate:
        divisor = math.gcd(converted_rate, sample_rate)
        converted = resample_poly(
            converted, sample_rate // divisor, converted_rate // divisor
        )
    return _match_and_align(converted, original)


def _fade_to_original(replacement: np.ndarray, original: np.ndarray, fade: int) -> None:
    edge = min(fade, len(replacement) // 4)
    if not edge:
        return
    ramp = np.linspace(0.0, 1.0, edge, dtype=np.float32)
    replacement[:edge] = original[:edge] * (1 - ramp) + replacement[:edge] * ramp
    replacement[-edge:] = replacement[-edge:] * (1 - ramp) + original[-edge:] * ramp


def convert_long_audio(
    engine: RvcEngine,
    input_path: Path,
    output_path: Path,
    model: dict[str, Any],
    parameters: dict[str, Any],
    work_dir: Path,
    progress: Progress,
    cancelled: Callable[[], bool],
) -> list[dict[str, Any]]:
    sample_rate, total_frames, ranges = _quiet_chunk_ranges_file(input_path, cancelled)
    chunk_dir = work_dir / "long-audio-chunks"
    chunk_dir.mkdir(parents=True, exist_ok=False)
    jobs = []
    sources = []
    for index, (start, end) in enumerate(ranges, start=1):
        if cancelled():
            raise InterruptedError("task cancellation requested")
        source = chunk_dir / f"chunk-{index:03d}-source.wav"
        converted = chunk_dir / f"chunk-{index:03d}-converted.wav"
        sf.write(
            source,
            _read_mono_range(input_path, start, end),
            sample_rate,
            subtype="PCM_24",
        )
        jobs.append((source, converted))
        sources.append((start, end, source, converted))
    progress(0.3, "converting", f"prepared {len(jobs)} low-energy RVC chunks")
    engine_results = engine.convert_batch(
        jobs, model, parameters, progress=progress, cancelled=cancelled
    )
    fade = max(1, int(sample_rate * 0.02))
    artifacts = []
    with sf.SoundFile(
        output_path,
        mode="w",
        samplerate=sample_rate,
        channels=1,
        subtype="PCM_24",
    ) as writer:
        for index, ((start, end, source, converted), engine_result) in enumerate(
            zip(sources, engine_results, strict=True), start=1
        ):
            if cancelled():
                raise InterruptedError("task cancellation requested")
            original = _read_mono_range(input_path, start, end)
            replacement = _converted_replacement(converted, original, sample_rate)
            if start > 0 or end < total_frames:
                _fade_to_original(replacement, original, fade)
            writer.write(replacement)
            artifacts.append(
                {
                    "chunk": index,
                    "start_seconds": round(start / sample_rate, 6),
                    "end_seconds": round(end / sample_rate, 6),
                    "source": _file_record(source),
                    "conversion": engine_result,
                }
            )
    return artifacts

def convert_selected_segments(
    engine: RvcEngine,
    audio_path: Path,
    output_path: Path,
    model: dict[str, Any],
    parameters: dict[str, Any],
    segments: list[dict[str, Any]],
    selected_speakers: set[str],
    work_dir: Path,
    progress: Progress,
    cancelled: Callable[[], bool],
    overlap_policy: str,
) -> list[dict[str, Any]]:
    info = sf.info(audio_path)
    sample_rate = info.samplerate
    known_speakers = {segment["speaker"] for segment in segments}
    unknown_speakers = selected_speakers - known_speakers
    if unknown_speakers:
        raise MediaPipelineError(
            f"selected speakers are not in analysis: {sorted(unknown_speakers)}"
        )
    if overlap_policy not in {"skip", "convert"}:
        raise ValueError("overlap_policy must be skip or convert")
    selected = [
        segment
        for segment in segments
        if segment["speaker"] in selected_speakers
        and (
            overlap_policy == "convert"
            or segment.get("overlap") not in {True, "unknown", "unresolved"}
        )
    ]
    if not selected:
        raise MediaPipelineError(
            "no selected speaker intervals remain after applying the overlap policy"
        )
    with sf.SoundFile(audio_path) as reader, sf.SoundFile(
        output_path,
        mode="w",
        samplerate=sample_rate,
        channels=1,
        subtype="PCM_24",
    ) as writer:
        while True:
            if cancelled():
                raise InterruptedError("task cancellation requested")
            block = reader.read(sample_rate * 10, dtype="float32", always_2d=True)
            if not len(block):
                break
            writer.write(block.mean(axis=1))
    artifacts = []
    max_chunk = sample_rate * 45
    fade = int(sample_rate * 0.02)
    with sf.SoundFile(output_path, mode="r+") as writer:
        for index, segment in enumerate(selected):
            if cancelled():
                raise InterruptedError("task cancellation requested")
            start = max(0, int(segment["start_seconds"] * sample_rate))
            end = min(info.frames, int(segment["end_seconds"] * sample_rate))
            if end - start < sample_rate // 4:
                continue
            conversions = []
            chunk_start = start
            chunk_number = 0
            while chunk_start < end:
                if cancelled():
                    raise InterruptedError("task cancellation requested")
                chunk_end = min(end, chunk_start + max_chunk)
                chunk_number += 1
                prefix = f"segment-{index + 1:04d}-chunk-{chunk_number:03d}"
                source = work_dir / f"{prefix}-source.wav"
                converted_path = work_dir / f"{prefix}-converted.wav"
                original = _read_mono_range(audio_path, chunk_start, chunk_end)
                sf.write(source, original, sample_rate, subtype="PCM_24")
                local_parameters = dict(parameters)
                local_parameters["overwrite"] = False
                engine_result = engine.convert(
                    source,
                    converted_path,
                    model,
                    local_parameters,
                    cancelled=cancelled,
                )
                replacement = _converted_replacement(
                    converted_path, original, sample_rate
                )
                _fade_to_original(replacement, original, fade)
                writer.seek(chunk_start)
                writer.write(replacement)
                conversions.append(engine_result)
                chunk_start = chunk_end
            artifact = {"segment": segment, "conversions": conversions}
            if len(conversions) == 1:
                artifact["conversion"] = conversions[0]
            artifacts.append(artifact)
            progress(
                0.3 + 0.5 * ((index + 1) / max(1, len(selected))),
                "converting",
                f"segment {index + 1}/{len(selected)}",
            )
    return artifacts
