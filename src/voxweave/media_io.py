from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import soundfile as sf

from .config import Settings
from .hashing import sha256_file
from .media_errors import MediaPipelineError
from .process_control import run_capture
from .protocol import OperationError


def _binary(settings: Settings, kind: str) -> str:
    configured = settings.ffmpeg if kind == "ffmpeg" else settings.ffprobe
    value = configured or shutil.which(kind)
    if not value or not Path(value).is_file():
        raise MediaPipelineError(f"{kind} is not configured")
    return str(Path(value).resolve())


def _run(command: list[str], *, cancelled: Callable[[], bool] | None = None):
    completed = run_capture(command, cancelled=cancelled)
    if completed.returncode != 0:
        raise MediaPipelineError(completed.stderr.strip() or completed.stdout.strip())
    return completed


def inspect_media(
    settings: Settings,
    input_path: Path,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    input_path = input_path.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    before = input_path.stat()
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
        ],
        cancelled=cancelled,
    )
    payload = json.loads(completed.stdout)
    streams = payload.get("streams", [])
    audio_streams = [stream for stream in streams if stream.get("codec_type") == "audio"]
    video_streams = [stream for stream in streams if stream.get("codec_type") == "video"]
    subtitle_streams = [stream for stream in streams if stream.get("codec_type") == "subtitle"]
    digest = sha256_file(input_path)
    after = input_path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OperationError("input_changed", f"media changed while it was inspected: {input_path}")
    return {
        "path": str(input_path),
        "sha256": digest,
        "size_bytes": after.st_size,
        "media_type": "video" if video_streams else "audio",
        "duration_seconds": float(payload.get("format", {}).get("duration") or 0),
        "format_name": payload.get("format", {}).get("format_name"),
        "audio_streams": audio_streams,
        "video_streams": video_streams,
        "subtitle_streams": subtitle_streams,
    }


def verify_media_snapshot(path: Path, expected_sha256: str) -> None:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise OperationError("input_missing", f"media no longer exists: {path}")
    if sha256_file(path).casefold() != expected_sha256.casefold():
        raise OperationError("input_changed", f"media changed during processing: {path}")


def extract_audio(
    settings: Settings,
    input_path: Path,
    output_path: Path,
    *,
    sample_rate: int = 48000,
    cancelled: Callable[[], bool] | None = None,
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
        ],
        cancelled=cancelled,
    )


def clip_audio(
    settings: Settings,
    input_path: Path,
    output_path: Path,
    start_seconds: float,
    duration_seconds: float,
    cancelled: Callable[[], bool] | None = None,
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
        ],
        cancelled=cancelled,
    )


def mix_stems(
    settings: Settings,
    vocal: Path,
    instrumental: Path,
    output: Path,
    cancelled: Callable[[], bool] | None = None,
) -> None:
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
        ],
        cancelled=cancelled,
    )


def transcode_audio(
    settings: Settings,
    source: Path,
    output: Path,
    overwrite: bool,
    cancelled: Callable[[], bool] | None = None,
) -> None:
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
        ],
        cancelled=cancelled,
    )


def mux_video(
    settings: Settings,
    source: Path,
    converted_audio: Path,
    output: Path,
    overwrite: bool,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    media = inspect_media(settings, source, cancelled)
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
        ],
        cancelled=cancelled,
    )


def measure_audio_quality(
    settings: Settings,
    media_path: Path,
    audio_stream: int = 0,
    cancelled: Callable[[], bool] | None = None,
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
        ],
        cancelled=cancelled,
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
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    reference_quality = measure_audio_quality(settings, loudness_reference, cancelled=cancelled)
    before = measure_audio_quality(settings, source, cancelled=cancelled)
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
        ],
        cancelled=cancelled,
    )
    reference = sf.info(duration_reference)
    _run(
        [
            _binary(settings, "ffmpeg"),
            "-v",
            "error",
            "-n",
            "-i",
            str(filtered),
            "-af",
            (
                f"aresample={reference.samplerate},"
                f"apad=whole_len={reference.frames},atrim=end_sample={reference.frames}"
            ),
            "-c:a",
            "pcm_s24le",
            str(output),
        ],
        cancelled=cancelled,
    )
    after = measure_audio_quality(settings, output, cancelled=cancelled)
    return {
        "reference": reference_quality,
        "before": before,
        "after": after,
        "output_path": str(output),
        "output_sha256": sha256_file(output),
    }


def validate_output(
    settings: Settings,
    output: Path,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    media = inspect_media(settings, output, cancelled)
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
        ],
        cancelled=cancelled,
    )
    media["full_decode"] = "passed"
    media["audio_quality"] = [
        measure_audio_quality(settings, output, stream_index, cancelled)
        for stream_index in range(len(media["audio_streams"]))
    ]
    return media
