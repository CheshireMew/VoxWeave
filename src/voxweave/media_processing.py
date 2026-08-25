from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import PACKAGE_ROOT, Settings
from .hashing import FileVerificationLedger, sha256_file
from .media_errors import MediaPipelineError
from .media_io import _binary, _run, clip_audio
from .runtime import resolve_rvc_python
from .runtime_contract import runtime_contract
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
    separation = runtime_contract().source_separation
    if settings.separation_backend != separation.backend:
        raise MediaPipelineError(f"unsupported separation backend: {settings.separation_backend}")
    python = resolve_rvc_python(settings)
    rvc_root = Path(settings.rvc_root or "")
    model_file = rvc_root / "assets" / separation.model_file
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
    config_file = rvc_root / "assets" / separation.config_file
    component = {
        "backend": separation.backend,
        "model_id": separation.model_id,
        "model_path": str(model_file),
        "model_sha256": sha256_file(model_file),
        "config_path": str(config_file),
        "config_sha256": sha256_file(config_file),
        "source": separation.source,
        "code_license_spdx": separation.code_license_spdx,
        "model_license_spdx": separation.model_license_spdx,
        "distribution_allowed": separation.distribution_allowed,
    }
    return vocals, instrumental, component


def _postprocess(
    settings: Settings | None,
    operation: str,
    request_path: Path,
    cancelled: Callable[[], bool] | None,
) -> Any:
    python = resolve_rvc_python(settings) if settings is not None else Path(sys.executable)
    if not python:
        raise MediaPipelineError("RVC Python runtime is required for media postprocessing")
    completed = _run(
        [
            str(python),
            "-B",
            str(PACKAGE_ROOT / "media_postprocess_worker.py"),
            "--operation",
            operation,
            "--request",
            str(request_path),
        ],
        cancelled=cancelled,
    )
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise MediaPipelineError("media postprocess worker returned invalid output") from exc
    if not payload.get("ok"):
        raise MediaPipelineError(str(payload.get("error") or "media postprocessing failed"))
    return payload["result"]


def _write_postprocess_request(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def align_audio_file(
    converted_path: Path,
    original_path: Path,
    output_path: Path,
    settings: Settings | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    request_path = output_path.with_suffix(".align-request.json")
    _write_postprocess_request(
        request_path,
        {
            "converted": str(converted_path),
            "original": str(original_path),
            "output": str(output_path),
        },
    )
    return dict(_postprocess(settings, "align", request_path, cancelled))


def dereverb_audio_file(
    input_path: Path,
    output_path: Path,
    strength: float,
    settings: Settings | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    request_path = output_path.with_suffix(".dereverb-request.json")
    _write_postprocess_request(
        request_path,
        {
            "input": str(input_path),
            "output": str(output_path),
            "strength": strength,
        },
    )
    return dict(_postprocess(settings, "dereverb", request_path, cancelled))


def convert_long_audio(
    engine: RvcEngine,
    input_path: Path,
    output_path: Path,
    model: dict[str, Any],
    parameters: dict[str, Any],
    work_dir: Path,
    progress: Progress,
    cancelled: Callable[[], bool],
    settings: Settings | None = None,
    ledger: FileVerificationLedger | None = None,
) -> list[dict[str, Any]]:
    settings = settings or getattr(engine, "settings", None)
    prepare_request = work_dir / "long-audio-prepare.json"
    _write_postprocess_request(
        prepare_request,
        {"input": str(input_path), "chunk_dir": str(work_dir / "long-audio-chunks")},
    )
    manifest = dict(_postprocess(settings, "prepare-long", prepare_request, cancelled))
    jobs = [
        (Path(chunk["source"]), Path(chunk["converted"]))
        for chunk in manifest["chunks"]
    ]
    progress(0.3, "converting", f"prepared {len(jobs)} low-energy RVC chunks")
    engine_arguments: dict[str, Any] = {"progress": progress, "cancelled": cancelled}
    if ledger is not None:
        engine_arguments["ledger"] = ledger
    engine_results = engine.convert_batch(jobs, model, parameters, **engine_arguments)
    finalize_request = work_dir / "long-audio-finalize.json"
    _write_postprocess_request(
        finalize_request, {"manifest": manifest, "output": str(output_path)}
    )
    artifacts = list(
        _postprocess(settings, "finalize-long", finalize_request, cancelled)
    )
    for artifact, engine_result in zip(artifacts, engine_results, strict=True):
        artifact["conversion"] = engine_result
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
    settings: Settings | None = None,
    ledger: FileVerificationLedger | None = None,
) -> list[dict[str, Any]]:
    settings = settings or getattr(engine, "settings", None)
    prepare_request = work_dir / "selected-segments-prepare.json"
    _write_postprocess_request(
        prepare_request,
        {
            "input": str(audio_path),
            "output": str(output_path),
            "work_dir": str(work_dir),
            "segments": segments,
            "selected_speakers": sorted(selected_speakers),
            "overlap_policy": overlap_policy,
        },
    )
    try:
        manifest = dict(
            _postprocess(settings, "prepare-selected", prepare_request, cancelled)
        )
    except MediaPipelineError as exc:
        raise MediaPipelineError(str(exc)) from exc
    jobs = [
        (Path(chunk["source"]), Path(chunk["converted"]))
        for prepared in manifest["segments"]
        for chunk in prepared["chunks"]
    ]
    if not jobs:
        raise MediaPipelineError("selected speaker intervals are too short to convert")
    local_parameters = {**parameters, "overwrite": False}
    progress(0.3, "converting", f"prepared {len(jobs)} selected-speaker chunks")
    engine_arguments: dict[str, Any] = {"progress": progress, "cancelled": cancelled}
    if ledger is not None:
        engine_arguments["ledger"] = ledger
    engine_results = engine.convert_batch(
        jobs, model, local_parameters, **engine_arguments
    )
    finalize_request = work_dir / "selected-segments-finalize.json"
    _write_postprocess_request(finalize_request, {"manifest": manifest})
    artifacts = list(
        _postprocess(settings, "finalize-selected", finalize_request, cancelled)
    )
    result_index = 0
    for artifact, prepared in zip(artifacts, manifest["segments"], strict=True):
        count = len(prepared["chunks"])
        conversions = engine_results[result_index : result_index + count]
        result_index += count
        artifact["conversions"] = conversions
        if len(conversions) == 1:
            artifact["conversion"] = conversions[0]
    progress(0.8, "converting", f"converted {len(artifacts)} selected segments")
    return artifacts


def convert_assigned_segments(
    engine: RvcEngine,
    audio_path: Path,
    output_path: Path,
    assignments: list[dict[str, Any]],
    segments: list[dict[str, Any]],
    work_dir: Path,
    progress: Progress,
    cancelled: Callable[[], bool],
    overlap_policy: str,
    settings: Settings | None = None,
    ledger: FileVerificationLedger | None = None,
) -> list[dict[str, Any]]:
    """Convert explicitly assigned analysis segments, grouping jobs by model revision."""

    settings = settings or getattr(engine, "settings", None)
    assignment_by_segment: dict[str, dict[str, Any]] = {}
    for assignment in assignments:
        for segment_id in assignment["segment_ids"]:
            if segment_id in assignment_by_segment:
                raise MediaPipelineError(f"segment is assigned more than once: {segment_id}")
            assignment_by_segment[segment_id] = assignment
    selected_ids = set(assignment_by_segment)
    known = {str(segment.get("id")) for segment in segments}
    missing = selected_ids - known
    if missing:
        raise MediaPipelineError(f"assigned segments are not in analysis: {sorted(missing)}")
    selected_speakers = {
        str(segment["speaker"])
        for segment in segments
        if str(segment.get("id")) in selected_ids
    }
    prepare_request = work_dir / "assigned-segments-prepare.json"
    _write_postprocess_request(
        prepare_request,
        {
            "input": str(audio_path),
            "output": str(output_path),
            "work_dir": str(work_dir),
            "segments": segments,
            "selected_speakers": sorted(selected_speakers),
            "selected_segment_ids": sorted(selected_ids),
            "overlap_policy": overlap_policy,
        },
    )
    manifest = dict(_postprocess(settings, "prepare-selected", prepare_request, cancelled))
    prepared_by_assignment: dict[int, list[dict[str, Any]]] = {}
    for prepared in manifest["segments"]:
        segment_id = str(prepared["segment"].get("id"))
        assignment = assignment_by_segment[segment_id]
        prepared_by_assignment.setdefault(id(assignment), []).append(prepared)

    result_by_chunk: dict[str, dict[str, Any]] = {}
    for assignment_index, assignment in enumerate(assignments):
        prepared_segments = prepared_by_assignment.get(id(assignment), [])
        jobs = [
            (Path(chunk["source"]), Path(chunk["converted"]))
            for prepared in prepared_segments
            for chunk in prepared["chunks"]
        ]
        if not jobs:
            continue
        model = assignment["model"]
        parameters = {
            **model["recommended"],
            **assignment.get("parameters", {}),
            "overwrite": False,
        }
        progress(
            0.3 + 0.45 * assignment_index / max(1, len(assignments)),
            "converting",
            f"converting {len(jobs)} chunks with {model['display_name']}",
        )
        engine_arguments: dict[str, Any] = {
            "progress": progress,
            "cancelled": cancelled,
        }
        if ledger is not None:
            engine_arguments["ledger"] = ledger
        conversions = engine.convert_batch(
            jobs,
            model,
            parameters,
            **engine_arguments,
        )
        for (_source, converted), conversion in zip(jobs, conversions, strict=True):
            result_by_chunk[str(converted)] = conversion

    finalize_request = work_dir / "assigned-segments-finalize.json"
    _write_postprocess_request(finalize_request, {"manifest": manifest})
    artifacts = list(_postprocess(settings, "finalize-selected", finalize_request, cancelled))
    for artifact, prepared in zip(artifacts, manifest["segments"], strict=True):
        segment_id = str(prepared["segment"].get("id"))
        assignment = assignment_by_segment[segment_id]
        model = assignment["model"]
        artifact["model"] = {
            "id": model["id"],
            "display_name": model["display_name"],
            "model_sha256": model["model_sha256"],
            "index_sha256": model.get("index_sha256"),
        }
        artifact["parameters"] = {
            **model["recommended"],
            **assignment.get("parameters", {}),
        }
        artifact["conversions"] = [
            result_by_chunk[str(chunk["converted"])] for chunk in prepared["chunks"]
        ]
    progress(0.8, "converting", f"converted {len(artifacts)} assigned segments")
    return artifacts
