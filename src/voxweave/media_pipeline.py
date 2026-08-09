from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .config import PACKAGE_ROOT, Settings
from .hashing import sha256_file
from .model_registry import ModelRegistry
from .runtime import resolve_rvc_python
from .rvc_engine import RvcEngine


class MediaPipelineError(RuntimeError):
    pass


Progress = Callable[[float, str, str | None], None]


def _file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path.resolve()),
        "sha256": sha256_file(path),
        "size_bytes": path.stat().st_size,
    }


def _verified_checkpoint_file(record: dict[str, Any] | None) -> Path | None:
    if not record or not record.get("path") or not record.get("sha256"):
        return None
    path = Path(record["path"])
    if not path.is_file() or path.stat().st_size != int(record.get("size_bytes", -1)):
        return None
    return path if sha256_file(path) == record["sha256"] else None


def _write_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _load_resume_checkpoint(
    settings: Settings, arguments: dict[str, Any], signature: dict[str, Any]
) -> dict[str, Any] | None:
    previous_task = arguments.get("_resume_from_task_id")
    if not previous_task:
        return None
    path = settings.artifacts_dir / str(previous_task) / "checkpoint.json"
    if not path.is_file():
        return None
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (
        checkpoint.get("protocol") != "voxweave-conversion-checkpoint"
        or checkpoint.get("version") != 1
        or checkpoint.get("signature") != signature
    ):
        return None
    return checkpoint


def _binary(settings: Settings, kind: str) -> str:
    configured = settings.ffmpeg if kind == "ffmpeg" else settings.ffprobe
    value = configured or shutil.which(kind)
    if not value or not Path(value).is_file():
        raise MediaPipelineError(f"{kind} is not configured")
    return str(Path(value).resolve())


def _run(
    command: list[str],
    *,
    capture: bool = True,
    cancelled: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    if cancelled is not None:
        process = subprocess.Popen(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
        while True:
            try:
                stdout, stderr = process.communicate(timeout=0.25)
                completed = subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                break
            except subprocess.TimeoutExpired:
                if cancelled():
                    process.terminate()
                    process.communicate()
                    raise InterruptedError("task cancellation requested") from None
        if completed.returncode != 0:
            raise MediaPipelineError(completed.stderr.strip() or completed.stdout.strip())
        return completed
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=capture,
    )
    if completed.returncode != 0:
        raise MediaPipelineError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def inspect_media(settings: Settings, input_path: Path) -> dict[str, Any]:
    input_path = input_path.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    completed = _run(
        [
            _binary(settings, "ffprobe"),
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            "--",
            str(input_path),
        ]
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    return {
        "path": str(input_path),
        "sha256": sha256_file(input_path),
        "size_bytes": input_path.stat().st_size,
        "media_type": "video" if video_streams else "audio",
        "duration_seconds": float(payload.get("format", {}).get("duration") or 0),
        "format_name": payload.get("format", {}).get("format_name"),
        "audio_streams": audio_streams,
        "video_streams": video_streams,
        "subtitle_streams": subtitle_streams,
    }


def extract_audio(
    settings: Settings, input_path: Path, output_path: Path, *, sample_rate: int = 48000
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-n",
            "-i",
            str(input_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "pcm_s24le",
            str(output_path),
        ]
    )


def clip_audio(
    settings: Settings,
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    if duration_seconds < 1:
        raise ValueError("preview duration must be at least one second")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-n",
            "-ss",
            str(max(0.0, start_seconds)),
            "-t",
            str(duration_seconds),
            "-i",
            str(input_path),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s24le",
            str(output_path),
        ]
    )


def analyze_audio(settings: Settings, audio_path: Path, work_dir: Path) -> dict[str, Any]:
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
        ]
    )
    command = [
        str(python),
        str(PACKAGE_ROOT / "analysis_worker.py"),
        "--audio",
        str(analysis_audio),
    ]
    if settings.wespeaker_model and Path(settings.wespeaker_model).is_file():
        command.extend(["--speaker-model", settings.wespeaker_model])
    completed = _run(command)
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
) -> list[dict[str, Any]]:
    sample_dir = work_dir / "speaker-samples"
    sample_dir.mkdir(parents=True, exist_ok=False)
    speakers = sorted({segment["speaker"] for segment in segments})
    samples = []
    for speaker in speakers:
        candidates = [segment for segment in segments if segment["speaker"] == speaker]
        segment = max(
            candidates,
            key=lambda item: item["end_seconds"] - item["start_seconds"],
        )
        duration = min(10.0, segment["end_seconds"] - segment["start_seconds"])
        output = sample_dir / f"{speaker}.wav"
        clip_audio(settings, audio_path, output, segment["start_seconds"], duration)
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


def _quiet_chunk_ranges(
    audio: np.ndarray,
    sample_rate: int,
    *,
    target_seconds: float = 45.0,
    search_seconds: float = 6.0,
) -> list[tuple[int, int]]:
    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    total = len(mono)
    target = int(target_seconds * sample_rate)
    search = int(search_seconds * sample_rate)
    minimum = int(20.0 * sample_rate)
    window = max(1, int(0.1 * sample_rate))
    step = max(1, int(0.05 * sample_rate))
    if total <= target + search:
        return [(0, total)]
    energy = np.concatenate(([0.0], np.cumsum(np.square(mono, dtype=np.float64), dtype=np.float64)))
    boundaries = [0]
    while total - boundaries[-1] > target + search:
        expected = boundaries[-1] + target
        lower = max(boundaries[-1] + minimum, expected - search)
        upper = min(total - minimum, expected + search)
        if upper <= lower:
            break
        candidates = np.arange(lower, upper + 1, step, dtype=np.int64)
        starts = np.maximum(0, candidates - window // 2)
        ends = np.minimum(total, starts + window)
        scores = (energy[ends] - energy[starts]) / np.maximum(1, ends - starts)
        boundaries.append(int(candidates[int(np.argmin(scores))]))
    boundaries.append(total)
    return list(zip(boundaries, boundaries[1:], strict=False))


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
    audio, sample_rate = sf.read(input_path, dtype="float32", always_2d=False)
    mono = audio.mean(axis=1) if audio.ndim > 1 else audio
    ranges = _quiet_chunk_ranges(mono, sample_rate)
    chunk_dir = work_dir / "long-audio-chunks"
    chunk_dir.mkdir(parents=True, exist_ok=False)
    jobs = []
    sources = []
    for index, (start, end) in enumerate(ranges, start=1):
        source = chunk_dir / f"chunk-{index:03d}-source.wav"
        converted = chunk_dir / f"chunk-{index:03d}-converted.wav"
        sf.write(source, mono[start:end], sample_rate, subtype="PCM_24")
        jobs.append((source, converted))
        sources.append((start, end, source, converted))
    progress(0.3, "converting", f"prepared {len(jobs)} low-energy RVC chunks")
    engine_results = engine.convert_batch(
        jobs, model, parameters, progress=progress, cancelled=cancelled
    )
    result = mono.copy()
    fade = max(1, int(sample_rate * 0.02))
    artifacts = []
    for index, ((start, end, source, converted), engine_result) in enumerate(
        zip(sources, engine_results, strict=True), start=1
    ):
        converted_audio, converted_rate = sf.read(converted, dtype="float32", always_2d=False)
        if converted_rate != sample_rate:
            divisor = math.gcd(converted_rate, sample_rate)
            converted_audio = resample_poly(
                converted_audio, sample_rate // divisor, converted_rate // divisor
            )
        replacement = _match_and_align(converted_audio, mono[start:end])
        edge = min(fade, len(replacement) // 4)
        if edge and start > 0:
            ramp = np.linspace(0.0, 1.0, edge, dtype=np.float32)
            replacement[:edge] = mono[start : start + edge] * (1 - ramp) + replacement[:edge] * ramp
        if edge and end < len(mono):
            ramp = np.linspace(1.0, 0.0, edge, dtype=np.float32)
            replacement[-edge:] = replacement[-edge:] * ramp + mono[end - edge : end] * (1 - ramp)
        result[start:end] = replacement
        artifacts.append(
            {
                "chunk": index,
                "start_seconds": round(start / sample_rate, 6),
                "end_seconds": round(end / sample_rate, 6),
                "source": _file_record(source),
                "conversion": engine_result,
            }
        )
    sf.write(output_path, result, sample_rate, subtype="PCM_24")
    return artifacts


def align_audio_length(audio_path: Path, reference_path: Path, output_path: Path) -> None:
    audio, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
    reference = sf.info(reference_path)
    if sample_rate != reference.samplerate:
        divisor = math.gcd(sample_rate, reference.samplerate)
        audio = resample_poly(
            audio,
            reference.samplerate // divisor,
            sample_rate // divisor,
            axis=0,
        )
    if len(audio) < reference.frames:
        audio = np.pad(audio, ((0, reference.frames - len(audio)), (0, 0)))
    audio = audio[: reference.frames]
    if audio.shape[1] == 1:
        audio = audio[:, 0]
    sf.write(output_path, audio, reference.samplerate, subtype="PCM_24")


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
    audio, sample_rate = sf.read(audio_path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    result = audio.copy()
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
    artifacts = []
    for index, segment in enumerate(selected):
        start = max(0, int(segment["start_seconds"] * sample_rate))
        end = min(len(audio), int(segment["end_seconds"] * sample_rate))
        if end - start < sample_rate // 4:
            continue
        source = work_dir / f"segment-{index + 1:04d}-source.wav"
        converted_path = work_dir / f"segment-{index + 1:04d}-converted.wav"
        sf.write(source, audio[start:end], sample_rate, subtype="PCM_24")
        local_parameters = dict(parameters)
        local_parameters["overwrite"] = False
        engine_result = engine.convert(
            source, converted_path, model, local_parameters, cancelled=cancelled
        )
        converted, _ = sf.read(converted_path, dtype="float32", always_2d=False)
        replacement = _match_and_align(converted, audio[start:end])
        fade = min(int(sample_rate * 0.02), len(replacement) // 4)
        if fade:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            replacement[:fade] = (
                audio[start : start + fade] * (1 - ramp) + replacement[:fade] * ramp
            )
            replacement[-fade:] = audio[end - fade : end] * ramp + replacement[-fade:] * (1 - ramp)
        result[start:end] = replacement
        artifacts.append({"segment": segment, "conversion": engine_result})
        progress(
            0.3 + 0.5 * ((index + 1) / max(1, len(selected))),
            "converting",
            f"segment {index + 1}/{len(selected)}",
        )
    sf.write(output_path, result, sample_rate, subtype="PCM_24")
    return artifacts


def mix_stems(settings: Settings, vocal: Path, instrumental: Path, output: Path) -> None:
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-n",
            "-i",
            str(vocal),
            "-i",
            str(instrumental),
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:normalize=0:duration=longest[a]",
            "-map",
            "[a]",
            "-c:a",
            "pcm_s24le",
            str(output),
        ]
    )


def transcode_audio(settings: Settings, source: Path, output: Path, overwrite: bool) -> None:
    codec_args: list[str]
    suffix = output.suffix.casefold()
    if suffix == ".wav":
        codec_args = ["-c:a", "pcm_s24le"]
    elif suffix == ".flac":
        codec_args = ["-c:a", "flac"]
    elif suffix == ".mp3":
        codec_args = ["-c:a", "libmp3lame", "-b:a", "320k"]
    elif suffix in {".m4a", ".aac"}:
        codec_args = ["-c:a", "aac", "-b:a", "320k"]
    else:
        raise ValueError(f"unsupported audio output: {output.suffix}")
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-y" if overwrite else "-n",
            "-i",
            str(source),
            *codec_args,
            str(output),
        ]
    )


def mux_video(
    settings: Settings, source: Path, converted_audio: Path, output: Path, overwrite: bool
) -> None:
    media = inspect_media(settings, source)
    converted_index = len(media["audio_streams"])
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-y" if overwrite else "-n",
            "-i",
            str(source),
            "-i",
            str(converted_audio),
            "-map",
            "0",
            "-map",
            "1:a:0",
            "-map_metadata",
            "0",
            "-map_chapters",
            "0",
            "-c",
            "copy",
            f"-c:a:{converted_index}",
            "aac",
            f"-b:a:{converted_index}",
            "320k",
            f"-metadata:s:a:{converted_index}",
            "title=VoxWeave converted voice",
            str(output),
        ]
    )


def measure_audio_quality(
    settings: Settings, media_path: Path, audio_stream: int = 0
) -> dict[str, Any]:
    completed = _run(
        [
            _binary(settings, "ffmpeg"),
            "-hide_banner",
            "-nostats",
            "-i",
            str(media_path),
            "-map",
            f"0:a:{audio_stream}",
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ]
    )
    report = completed.stderr
    loudness = re.findall(r"^\s*I:\s*(-?inf|[-+]?\d+(?:\.\d+)?)\s+LUFS", report, re.MULTILINE)
    peaks = re.findall(r"^\s*Peak:\s*(-?inf|[-+]?\d+(?:\.\d+)?)\s+dBFS", report, re.MULTILINE)
    return {
        "audio_stream": audio_stream,
        "integrated_loudness_lufs": (
            float(loudness[-1]) if loudness and loudness[-1] != "-inf" else None
        ),
        "true_peak_dbfs": float(peaks[-1]) if peaks and peaks[-1] != "-inf" else None,
    }


def match_loudness(
    settings: Settings,
    loudness_reference: Path,
    duration_reference: Path,
    source: Path,
    output: Path,
    work_dir: Path,
) -> dict[str, Any]:
    reference_quality = measure_audio_quality(settings, loudness_reference)
    before = measure_audio_quality(settings, source)
    target = reference_quality["integrated_loudness_lufs"]
    if target is None:
        raise MediaPipelineError("reference audio has no measurable integrated loudness")
    target = max(-70.0, min(-5.0, target))
    filtered = work_dir / "loudness-filtered.wav"
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-n",
            "-i",
            str(source),
            "-af",
            f"loudnorm=I={target}:TP=-1.0:LRA=11,aresample=48000",
            "-c:a",
            "pcm_s24le",
            str(filtered),
        ]
    )
    align_audio_length(filtered, duration_reference, output)
    after = measure_audio_quality(settings, output)
    return {
        "reference": reference_quality,
        "before": before,
        "after": after,
        "output_path": str(output),
        "output_sha256": sha256_file(output),
    }


def validate_output(settings: Settings, output: Path) -> dict[str, Any]:
    media = inspect_media(settings, output)
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-i",
            str(output),
            "-f",
            "null",
            "NUL" if os.name == "nt" else "/dev/null",
        ]
    )
    media["full_decode"] = "passed"
    media["audio_quality"] = [
        measure_audio_quality(settings, output, stream_index)
        for stream_index in range(len(media["audio_streams"]))
    ]
    return media


class MediaPipeline:
    def __init__(self, settings: Settings, registry: ModelRegistry):
        self.settings = settings
        self.registry = registry
        self.engine = RvcEngine(settings)

    def inspect(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return inspect_media(self.settings, Path(arguments["input"]))

    def analyze(
        self, arguments: dict[str, Any], progress: Progress, _cancelled: Callable[[], bool]
    ) -> dict[str, Any]:
        task_id = arguments["_task_id"]
        work_dir = self.settings.artifacts_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=False)
        source = Path(arguments["input"]).expanduser().resolve()
        content_mode = arguments.get("content_mode", "clean")
        progress(0.1, "analyzing", "extracting audio")
        audio = work_dir / "source.wav"
        extract_audio(self.settings, source, audio)
        if content_mode in {"mixed", "singing"}:
            progress(0.25, "analyzing", "separating vocals")
            vocal, instrumental, separation = separate_audio(
                self.settings, audio, work_dir / "stems", _cancelled
            )
        else:
            vocal, instrumental, separation = audio, None, None
        progress(0.55, "analyzing", "detecting speech and speakers")
        analysis = (
            analyze_audio(self.settings, vocal, work_dir)
            if content_mode != "singing"
            else {
                "speaker_count": 1,
                "segments": [],
                "note": "speaker clustering is disabled for singing",
            }
        )
        speaker_samples = (
            create_speaker_samples(self.settings, vocal, analysis["segments"], work_dir)
            if analysis.get("segments")
            else []
        )
        result = {
            "input": inspect_media(self.settings, source),
            "content_mode": content_mode,
            "vocal_audio": str(vocal),
            "instrumental_audio": str(instrumental) if instrumental else None,
            "separation": separation,
            "speaker_samples": speaker_samples,
            **analysis,
        }
        manifest = work_dir / "analysis.json"
        manifest.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        result["manifest_path"] = str(manifest)
        progress(0.95, "validating", "analysis complete")
        return result

    def preview(
        self, arguments: dict[str, Any], progress: Progress, _cancelled: Callable[[], bool]
    ) -> dict[str, Any]:
        variants = arguments.get("variants") or [{}]
        if not 1 <= len(variants) <= 4:
            raise ValueError("preview requires one to four variants")
        duration = float(arguments.get("duration_seconds", 15))
        if not 10 <= duration <= 20:
            raise ValueError("preview duration must be between 10 and 20 seconds")
        model = self.registry.resolve(arguments["model"])
        task_id = arguments["_task_id"]
        work_dir = self.settings.artifacts_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=False)
        output_directory = (
            Path(arguments.get("output_directory") or work_dir).expanduser().resolve()
        )
        output_directory.mkdir(parents=True, exist_ok=True)
        source = work_dir / "preview-source.wav"
        clip_audio(
            self.settings,
            Path(arguments["input"]),
            source,
            float(arguments.get("start_seconds", 0)),
            duration,
        )
        content_mode = arguments.get("content_mode", "clean")
        separation = None
        instrumental = None
        if content_mode in {"mixed", "singing"}:
            progress(0.1, "analyzing", "separating preview vocals and instrumental")
            vocal, instrumental, separation = separate_audio(
                self.settings, source, work_dir / "stems", _cancelled
            )
        else:
            vocal = source
        outputs = []
        for index, variant in enumerate(variants):
            parameters = {**model["recommended"], **variant, "overwrite": False}
            pitch = int(parameters.get("pitch", 0))
            output = output_directory / f"preview-{index + 1:02d}-{model['family']}-p{pitch:+d}.wav"
            variant_dir = work_dir / f"variant-{index + 1:02d}"
            variant_dir.mkdir(parents=True, exist_ok=False)
            converted_raw = variant_dir / "converted-raw.wav"
            engine_result = self.engine.convert(
                vocal, converted_raw, model, parameters, cancelled=_cancelled
            )
            converted = variant_dir / "converted.wav"
            aligned = align_audio_file(converted_raw, vocal, converted)
            converted_mix = converted
            if instrumental:
                converted_mix = variant_dir / "converted-mix.wav"
                mix_stems(self.settings, converted, instrumental, converted_mix)
            loudness = match_loudness(
                self.settings, source, source, converted_mix, output, variant_dir
            )
            outputs.append(
                {
                    **engine_result,
                    "aligned_output": aligned,
                    "separation": separation,
                    "loudness_match": loudness,
                    "output_path": str(output),
                    "media": validate_output(self.settings, output),
                }
            )
            progress(
                0.15 + 0.75 * ((index + 1) / len(variants)),
                "converting",
                f"variant {index + 1}/{len(variants)}",
            )
        return {
            "model": model,
            "source": str(source),
            "content_mode": content_mode,
            "separation": separation,
            "outputs": outputs,
        }

    def convert(
        self, arguments: dict[str, Any], progress: Progress, _cancelled: Callable[[], bool]
    ) -> dict[str, Any]:
        source = Path(arguments["input"]).expanduser().resolve()
        output = Path(arguments["output"]).expanduser().resolve()
        overwrite = bool(arguments.get("overwrite", False))
        if output.exists() and not overwrite:
            raise FileExistsError(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        model = self.registry.resolve(arguments["model"])
        if model["status"] not in {"ready", "runtime_missing"}:
            raise MediaPipelineError(f"model is not ready: {model['status']}")
        parameters = {**model["recommended"], **arguments, "overwrite": False}
        task_id = arguments["_task_id"]
        work_dir = self.settings.artifacts_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=False)
        source_media = inspect_media(self.settings, source)
        content_mode = arguments.get("content_mode", "clean")
        selected_speakers = set(arguments.get("selected_speakers") or [])
        analysis_manifest = arguments.get("analysis_manifest")
        analysis_hash = (
            sha256_file(Path(analysis_manifest))
            if analysis_manifest and Path(analysis_manifest).is_file()
            else None
        )
        signature = {
            "input_sha256": source_media["sha256"],
            "model_sha256": model["model_sha256"],
            "index_sha256": model.get("index_sha256"),
            "parameters": {
                key: parameters.get(key)
                for key in ("pitch", "f0", "index_rate", "rms_mix_rate", "protect")
            },
            "content_mode": content_mode,
            "selected_speakers": sorted(selected_speakers),
            "analysis_sha256": analysis_hash,
            "overlap_policy": arguments.get("overlap_policy", "convert"),
        }
        previous_checkpoint = _load_resume_checkpoint(self.settings, arguments, signature)
        checkpoint = {
            "protocol": "voxweave-conversion-checkpoint",
            "version": 1,
            "task_id": task_id,
            "resumed_from_task_id": arguments.get("_resume_from_task_id"),
            "signature": signature,
            "stages": {},
        }
        checkpoint_path = work_dir / "checkpoint.json"
        progress(0.05, "analyzing", "preparing source audio")
        previous_source = _verified_checkpoint_file(
            (previous_checkpoint or {}).get("stages", {}).get("source_audio")
        )
        if previous_source:
            source_audio = previous_source
            progress(0.08, "analyzing", "resumed verified source extraction")
        else:
            source_audio = work_dir / "source.wav"
            extract_audio(self.settings, source, source_audio)
        checkpoint["stages"]["source_audio"] = _file_record(source_audio)
        _write_checkpoint(checkpoint_path, checkpoint)
        instrumental = None
        separation = None
        if content_mode in {"mixed", "singing"}:
            previous_separation = (
                (previous_checkpoint or {}).get("stages", {}).get("separation", {})
            )
            previous_vocal = _verified_checkpoint_file(previous_separation.get("vocal"))
            previous_instrumental = _verified_checkpoint_file(
                previous_separation.get("instrumental")
            )
            if previous_vocal and previous_instrumental:
                vocal, instrumental = previous_vocal, previous_instrumental
                separation = previous_separation.get("metadata")
                progress(0.18, "analyzing", "resumed verified source separation")
            else:
                progress(0.15, "analyzing", "separating vocals and instrumental")
                vocal, instrumental, separation = separate_audio(
                    self.settings, source_audio, work_dir / "stems", _cancelled
                )
            checkpoint["stages"]["separation"] = {
                "vocal": _file_record(vocal),
                "instrumental": _file_record(instrumental),
                "metadata": separation,
            }
            _write_checkpoint(checkpoint_path, checkpoint)
        else:
            vocal = source_audio
        segment_results = []
        previous_conversion = (previous_checkpoint or {}).get("stages", {}).get("conversion", {})
        previous_converted_vocal = _verified_checkpoint_file(
            previous_conversion.get("converted_vocal")
        )
        if previous_converted_vocal:
            converted_vocal = previous_converted_vocal
            segment_results = previous_conversion.get("segments", [])
            progress(0.8, "converting", "resumed verified RVC conversion")
        elif selected_speakers:
            converted_vocal = work_dir / "converted-vocal.wav"
            if analysis_manifest:
                analysis = json.loads(Path(analysis_manifest).read_text(encoding="utf-8"))
                if analysis.get("input", {}).get("sha256") != source_media["sha256"]:
                    raise MediaPipelineError("analysis manifest does not match conversion input")
                if analysis.get("content_mode") != content_mode:
                    raise MediaPipelineError(
                        "analysis manifest content mode does not match conversion"
                    )
            else:
                analysis = analyze_audio(self.settings, vocal, work_dir)
            segment_results = convert_selected_segments(
                self.engine,
                vocal,
                converted_vocal,
                model,
                parameters,
                analysis["segments"],
                selected_speakers,
                work_dir,
                progress,
                _cancelled,
                arguments.get("overlap_policy", "convert"),
            )
        else:
            converted_vocal = work_dir / "converted-vocal.wav"
            vocal_info = sf.info(vocal)
            if vocal_info.duration > 90:
                segment_results = convert_long_audio(
                    self.engine,
                    vocal,
                    converted_vocal,
                    model,
                    parameters,
                    work_dir,
                    progress,
                    _cancelled,
                )
            else:
                converted_raw = work_dir / "converted-vocal-raw.wav"
                engine_result = self.engine.convert(
                    vocal,
                    converted_raw,
                    model,
                    parameters,
                    progress,
                    cancelled=_cancelled,
                )
                engine_result["aligned_output"] = align_audio_file(
                    converted_raw, vocal, converted_vocal
                )
                segment_results = [{"segment": "full", "conversion": engine_result}]
        checkpoint["stages"]["conversion"] = {
            "converted_vocal": _file_record(converted_vocal),
            "segments": segment_results,
        }
        _write_checkpoint(checkpoint_path, checkpoint)
        converted_mix = converted_vocal
        if instrumental:
            progress(0.82, "muxing", "mixing converted vocal with instrumental")
            converted_mix = work_dir / "converted-mix.wav"
            mix_stems(self.settings, converted_vocal, instrumental, converted_mix)
        previous_loudness = (previous_checkpoint or {}).get("stages", {}).get("loudness", {})
        previous_loudness_file = _verified_checkpoint_file(previous_loudness.get("output"))
        if previous_loudness_file:
            converted_mix = previous_loudness_file
            loudness_match = previous_loudness.get("metadata")
            progress(0.87, "muxing", "resumed verified loudness match")
        elif selected_speakers:
            reference_quality = measure_audio_quality(self.settings, source)
            converted_quality = measure_audio_quality(self.settings, converted_mix)
            loudness_match = {
                "mode": "selected-segments-preserved",
                "reference": reference_quality,
                "before": converted_quality,
                "after": converted_quality,
                "output_path": str(converted_mix),
                "output_sha256": sha256_file(converted_mix),
            }
            progress(0.87, "muxing", "preserving unselected speaker intervals")
        else:
            progress(0.85, "muxing", "matching source loudness")
            loudness_matched = work_dir / "loudness-matched.wav"
            loudness_match = match_loudness(
                self.settings,
                source,
                source_audio,
                converted_mix,
                loudness_matched,
                work_dir,
            )
            converted_mix = loudness_matched
        checkpoint["stages"]["loudness"] = {
            "output": _file_record(converted_mix),
            "metadata": loudness_match,
        }
        _write_checkpoint(checkpoint_path, checkpoint)
        progress(0.88, "muxing", "writing final media")
        if source_media["media_type"] == "video":
            mux_video(self.settings, source, converted_mix, output, overwrite)
        else:
            transcode_audio(self.settings, converted_mix, output, overwrite)
        progress(0.95, "validating", "fully decoding final output")
        output_media = validate_output(self.settings, output)
        provenance = {
            "protocol": "voxweave-conversion-result",
            "version": 1,
            "input": source_media,
            "output": output_media,
            "model": {
                "id": model["id"],
                "display_name": model["display_name"],
                "model_sha256": model["model_sha256"],
                "index_sha256": model.get("index_sha256"),
            },
            "parameters": {
                key: parameters.get(key)
                for key in ("pitch", "f0", "index_rate", "rms_mix_rate", "protect", "content_mode")
            },
            "selected_speakers": sorted(selected_speakers),
            "separation": separation,
            "loudness_match": loudness_match,
            "segments": segment_results,
        }
        manifest = work_dir / "conversion-result.json"
        manifest.write_text(
            json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return {**provenance, "manifest_path": str(manifest)}
