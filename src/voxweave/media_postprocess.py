from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _record(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "sha256": _sha256_file(path),
        "size_bytes": stat.st_size,
        "identity": [
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
            stat.st_ctime_ns,
        ],
    }


def _match_and_align(converted: np.ndarray, original: np.ndarray) -> np.ndarray:
    if converted.ndim > 1:
        converted = converted.mean(axis=1)
    if original.ndim > 1:
        original = original.mean(axis=1)
    if len(converted) != len(original):
        divisor = math.gcd(max(1, len(converted)), max(1, len(original)))
        converted = resample_poly(
            converted, len(original) // divisor, len(converted) // divisor
        )
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


def align(converted_path: Path, original_path: Path, output_path: Path) -> dict[str, Any]:
    if sf.info(original_path).duration > 120:
        raise ValueError("in-memory alignment is limited to two-minute preview chunks")
    converted, converted_rate = sf.read(converted_path, dtype="float32", always_2d=False)
    original, original_rate = sf.read(original_path, dtype="float32", always_2d=False)
    if converted_rate != original_rate:
        divisor = math.gcd(converted_rate, original_rate)
        converted = resample_poly(
            converted, original_rate // divisor, converted_rate // divisor
        )
    aligned = _match_and_align(converted, original)
    sf.write(output_path, aligned, original_rate, subtype="PCM_24")
    return {
        **_record(output_path),
        "sample_rate": original_rate,
        "samples": len(aligned),
    }


def _quiet_chunk_ranges(
    audio_path: Path,
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
    converted_path: Path, original: np.ndarray, sample_rate: int
) -> np.ndarray:
    converted, converted_rate = sf.read(converted_path, dtype="float32", always_2d=False)
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


def prepare_long(input_path: Path, chunk_dir: Path) -> dict[str, Any]:
    sample_rate, total_frames, ranges = _quiet_chunk_ranges(input_path)
    chunk_dir.mkdir(parents=True, exist_ok=False)
    chunks = []
    for index, (start, end) in enumerate(ranges, start=1):
        source = chunk_dir / f"chunk-{index:03d}-source.wav"
        converted = chunk_dir / f"chunk-{index:03d}-converted.wav"
        sf.write(source, _read_mono_range(input_path, start, end), sample_rate, subtype="PCM_24")
        chunks.append(
            {
                "index": index,
                "start": start,
                "end": end,
                "source": str(source),
                "converted": str(converted),
            }
        )
    return {
        "input": str(input_path),
        "sample_rate": sample_rate,
        "total_frames": total_frames,
        "chunks": chunks,
    }


def finalize_long(manifest: dict[str, Any], output_path: Path) -> list[dict[str, Any]]:
    input_path = Path(manifest["input"])
    sample_rate = int(manifest["sample_rate"])
    total_frames = int(manifest["total_frames"])
    fade = max(1, int(sample_rate * 0.02))
    artifacts = []
    with sf.SoundFile(
        output_path, mode="w", samplerate=sample_rate, channels=1, subtype="PCM_24"
    ) as writer:
        for chunk in manifest["chunks"]:
            start = int(chunk["start"])
            end = int(chunk["end"])
            original = _read_mono_range(input_path, start, end)
            replacement = _converted_replacement(
                Path(chunk["converted"]), original, sample_rate
            )
            if start > 0 or end < total_frames:
                _fade_to_original(replacement, original, fade)
            writer.write(replacement)
            artifacts.append(
                {
                    "chunk": int(chunk["index"]),
                    "start_seconds": round(start / sample_rate, 6),
                    "end_seconds": round(end / sample_rate, 6),
                    "source": _record(Path(chunk["source"])),
                }
            )
    return artifacts


def prepare_selected(
    audio_path: Path,
    output_path: Path,
    work_dir: Path,
    segments: list[dict[str, Any]],
    selected_speakers: set[str],
    overlap_policy: str,
) -> dict[str, Any]:
    info = sf.info(audio_path)
    sample_rate = info.samplerate
    known_speakers = {segment["speaker"] for segment in segments}
    unknown_speakers = selected_speakers - known_speakers
    if unknown_speakers:
        raise ValueError(f"selected speakers are not in analysis: {sorted(unknown_speakers)}")
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
        raise ValueError("no selected speaker intervals remain after applying the overlap policy")
    with sf.SoundFile(audio_path) as reader, sf.SoundFile(
        output_path, mode="w", samplerate=sample_rate, channels=1, subtype="PCM_24"
    ) as writer:
        while True:
            block = reader.read(sample_rate * 10, dtype="float32", always_2d=True)
            if not len(block):
                break
            writer.write(block.mean(axis=1))
    max_chunk = sample_rate * 45
    prepared_segments = []
    for index, segment in enumerate(selected):
        start = max(0, int(segment["start_seconds"] * sample_rate))
        end = min(info.frames, int(segment["end_seconds"] * sample_rate))
        if end - start < sample_rate // 4:
            continue
        chunks = []
        chunk_start = start
        chunk_number = 0
        while chunk_start < end:
            chunk_end = min(end, chunk_start + max_chunk)
            chunk_number += 1
            prefix = f"segment-{index + 1:04d}-chunk-{chunk_number:03d}"
            source = work_dir / f"{prefix}-source.wav"
            converted = work_dir / f"{prefix}-converted.wav"
            sf.write(
                source,
                _read_mono_range(audio_path, chunk_start, chunk_end),
                sample_rate,
                subtype="PCM_24",
            )
            chunks.append(
                {
                    "start": chunk_start,
                    "end": chunk_end,
                    "source": str(source),
                    "converted": str(converted),
                }
            )
            chunk_start = chunk_end
        prepared_segments.append({"segment": segment, "chunks": chunks})
    return {
        "input": str(audio_path),
        "output": str(output_path),
        "sample_rate": sample_rate,
        "segments": prepared_segments,
    }


def finalize_selected(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    audio_path = Path(manifest["input"])
    output_path = Path(manifest["output"])
    sample_rate = int(manifest["sample_rate"])
    fade = int(sample_rate * 0.02)
    completed = []
    with sf.SoundFile(output_path, mode="r+") as writer:
        for prepared in manifest["segments"]:
            for chunk in prepared["chunks"]:
                start = int(chunk["start"])
                end = int(chunk["end"])
                original = _read_mono_range(audio_path, start, end)
                replacement = _converted_replacement(
                    Path(chunk["converted"]), original, sample_rate
                )
                _fade_to_original(replacement, original, fade)
                writer.seek(start)
                writer.write(replacement)
            completed.append({"segment": prepared["segment"]})
    return completed
