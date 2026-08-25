from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    from .runtime_contract import runtime_contract
except ImportError:  # Direct worker script execution.
    from runtime_contract import runtime_contract


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def configure(rvc_root: Path, model_root: Path | None = None) -> None:
    os.chdir(rvc_root)
    worker_directory = Path(__file__).resolve().parent
    sys.path[:] = [
        value for value in sys.path if not value or Path(value).resolve() != worker_directory
    ]
    sys.path.insert(0, str(rvc_root))
    assets = rvc_root / "assets"
    contract = runtime_contract()
    os.environ["weight_root"] = str(model_root or assets / "weights")
    separation_directory = Path(contract.source_separation.model_file).parent
    rmvpe_directory = Path(contract.runtime_assets.rmvpe_file).parent
    os.environ.setdefault("weight_pymss_root", str(assets / separation_directory))
    os.environ.setdefault("index_root", str(rvc_root / "logs"))
    os.environ.setdefault("outside_index_root", str(assets / "indices"))
    os.environ.setdefault("rmvpe_root", str(assets / rmvpe_directory))
    os.environ.setdefault("RVC_CUDA_GRAPH", "0")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")


def doctor(arguments: argparse.Namespace) -> int:
    root = Path(arguments.rvc_root).resolve()
    configure(root)
    import torch  # noqa: PLC0415

    asset_contract = runtime_contract().runtime_assets
    required = [
        root / "configs" / "config.py",
        root / "infer" / "vc" / "modules.py",
        *(root / "assets" / relative for relative in asset_contract.required_files),
    ]
    payload = {
        "ok": all(path.is_file() for path in required),
        "command": "doctor",
        "rvc_root": str(root),
        "required": [{"path": str(path), "exists": path.is_file()} for path in required],
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
    }
    emit(payload)
    return 0 if payload["ok"] else 1


def devices(arguments: argparse.Namespace) -> int:
    from rvc_realtime_worker import list_audio_devices  # noqa: PLC0415

    root = Path(arguments.rvc_root).resolve()
    configure(root)
    emit(list_audio_devices())
    return 0


def _dbfs(value: float) -> float:
    import math  # noqa: PLC0415

    return max(-120.0, 20.0 * math.log10(max(value, 1e-6)))


def _input_statistics(samples: Any, sample_rate: int) -> dict[str, Any]:
    import numpy as np  # noqa: PLC0415

    mono = np.asarray(samples, dtype="float32").reshape(-1)
    window = max(1, int(sample_rate * 0.04))
    usable = len(mono) - len(mono) % window
    frames = mono[:usable].reshape(-1, window) if usable else np.empty((0, window))
    frame_rms = (
        np.sqrt(np.mean(np.square(frames), axis=1, dtype=np.float64))
        if len(frames)
        else np.array([0.0])
    )
    noise_floor_db = _dbfs(float(np.percentile(frame_rms, 20)))
    signal_db = _dbfs(float(np.percentile(frame_rms, 85)))
    pitches: list[float] = []
    minimum_lag = max(1, sample_rate // 500)
    maximum_lag = max(minimum_lag + 1, sample_rate // 60)
    for frame, level in zip(frames, frame_rms, strict=True):
        if _dbfs(float(level)) < noise_floor_db + 8.0:
            continue
        centered = frame - np.mean(frame)
        fft_size = 1 << (2 * len(centered) - 1).bit_length()
        spectrum = np.fft.rfft(centered, fft_size)
        correlation = np.fft.irfft(spectrum * np.conj(spectrum), fft_size)[: len(centered)]
        if correlation[0] <= 1e-9:
            continue
        upper = min(maximum_lag, len(correlation) - 1)
        if upper <= minimum_lag:
            continue
        local = correlation[minimum_lag : upper + 1]
        lag = minimum_lag + int(np.argmax(local))
        confidence = float(correlation[lag] / correlation[0])
        if confidence >= 0.3:
            pitches.append(sample_rate / lag)
    return {
        "noise_floor_db": round(noise_floor_db, 2),
        "signal_db": round(signal_db, 2),
        "snr_db": round(max(0.0, signal_db - noise_floor_db), 2),
        "pitch_hz_min": round(float(np.percentile(pitches, 10)), 2) if pitches else None,
        "pitch_hz_median": round(float(np.median(pitches)), 2) if pitches else None,
        "pitch_hz_max": round(float(np.percentile(pitches, 90)), 2) if pitches else None,
        "voiced_fraction": round(len(pitches) / max(1, len(frames)), 4),
        "clip_ratio": round(float(np.mean(np.abs(mono) >= 0.99)), 6) if mono.size else 0.0,
    }


def audio_test(arguments: argparse.Namespace) -> int:
    import numpy as np  # noqa: PLC0415
    import sounddevice as sd  # noqa: PLC0415

    root = Path(arguments.rvc_root).resolve()
    configure(root)
    device = int(arguments.device)
    duration = float(arguments.duration_seconds)
    info = sd.query_devices(device)
    sample_rate = int(info["default_samplerate"])
    if arguments.mode == "input":
        if int(info["max_input_channels"]) < 1:
            raise ValueError(f"device is not an audio input: {device}")
        samples = sd.rec(
            int(sample_rate * duration),
            samplerate=sample_rate,
            channels=1,
            dtype="float32",
            device=device,
            blocking=True,
        )
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        statistics = _input_statistics(samples, sample_rate)
        expected_frames = int(sample_rate * duration)
        captured_frames = int(samples.shape[0])
        finite = bool(np.isfinite(samples).all())
        completeness = min(1.0, captured_frames / max(1, expected_frames))
        stability = completeness if finite else 0.0
        emit(
            {
                "ok": True,
                "command": "audio-test",
                "mode": "input",
                "device": device,
                "sample_rate": sample_rate,
                "peak": peak,
                "rms": rms,
                **statistics,
                "captured_frames": captured_frames,
                "expected_frames": expected_frames,
                "device_stability": round(stability, 4),
            }
        )
        return 0
    if int(info["max_output_channels"]) < 1:
        raise ValueError(f"device is not an audio output: {device}")
    timeline = np.arange(int(sample_rate * duration), dtype="float32") / sample_rate
    tone = (0.08 * np.sin(2 * np.pi * 440.0 * timeline)).astype("float32")
    sd.play(tone, samplerate=sample_rate, device=device, blocking=True)
    emit(
        {
            "ok": True,
            "command": "audio-test",
            "mode": "output",
            "device": device,
            "sample_rate": sample_rate,
        }
    )
    return 0


def route_test(arguments: argparse.Namespace) -> int:
    import numpy as np  # noqa: PLC0415
    import sounddevice as sd  # noqa: PLC0415

    root = Path(arguments.rvc_root).resolve()
    configure(root)
    input_device = int(arguments.input_device)
    output_device = int(arguments.output_device)
    duration = float(arguments.duration_seconds)
    input_info = sd.query_devices(input_device)
    output_info = sd.query_devices(output_device)
    if int(input_info["max_input_channels"]) < 1:
        raise ValueError(f"device is not an audio input: {input_device}")
    if int(output_info["max_output_channels"]) < 1:
        raise ValueError(f"device is not an audio output: {output_device}")
    if int(input_info["hostapi"]) != int(output_info["hostapi"]):
        raise ValueError("routing test devices must use the same audio host API")
    sample_rate = int(min(input_info["default_samplerate"], output_info["default_samplerate"]))
    count = int(sample_rate * duration)
    timeline = np.arange(count, dtype="float32") / sample_rate
    envelope = np.zeros(count, dtype="float32")
    begin = int(0.2 * sample_rate)
    end = min(count, begin + int(0.65 * sample_rate))
    envelope[begin:end] = np.hanning(max(1, end - begin)).astype("float32")
    reference = (
        0.025
        * envelope
        * (
            np.sin(2 * np.pi * 587.33 * timeline)
            + 0.55 * np.sin(2 * np.pi * 941.0 * timeline)
        )
    ).astype("float32")
    captured = sd.playrec(
        reference,
        samplerate=sample_rate,
        channels=1,
        dtype="float32",
        device=(input_device, output_device),
        blocking=True,
    ).reshape(-1)
    fft_size = 1 << (len(captured) + len(reference) - 1).bit_length()
    correlation = np.fft.irfft(
        np.fft.rfft(captured, fft_size) * np.fft.rfft(reference[::-1], fft_size),
        fft_size,
    )
    lags = np.arange(len(correlation)) - (len(reference) - 1)
    allowed = (lags >= 0) & (lags <= int(sample_rate * 0.8))
    allowed_indices = np.flatnonzero(allowed)
    peak_index = int(allowed_indices[np.argmax(correlation[allowed])])
    denominator = float(np.linalg.norm(reference) * np.linalg.norm(captured))
    score = float(correlation[peak_index] / denominator) if denominator > 1e-9 else 0.0
    received_rms = float(np.sqrt(np.mean(np.square(captured), dtype=np.float64)))
    latency_ms = float(lags[peak_index] * 1000 / sample_rate) if score > 0.08 else None
    passed = bool(score >= 0.18 and _dbfs(received_rms) >= -55.0)
    emit(
        {
            "ok": True,
            "command": "route-test",
            "passed": passed,
            "input_device": input_device,
            "output_device": output_device,
            "sample_rate": sample_rate,
            "correlation": round(max(0.0, score), 4),
            "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
            "received_db": round(_dbfs(received_rms), 2),
            "detail": (
                "closed-loop signal returned through the selected route"
                if passed
                else "the selected input did not receive the coded output signal"
            ),
        }
    )
    return 0


def realtime(arguments: argparse.Namespace) -> int:
    from rvc_realtime_worker import run_resident_worker  # noqa: PLC0415

    root = Path(arguments.rvc_root).resolve()
    configure(root)
    if arguments.gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(arguments.gpu)
    return run_resident_worker()


def _convert_items(
    arguments: argparse.Namespace, items: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], str]:
    root = Path(arguments.rvc_root).resolve()
    model_path = Path(arguments.model).resolve()
    index_path = Path(arguments.index).resolve() if arguments.index else None
    if not model_path.is_file():
        raise FileNotFoundError(f"model file not found: {model_path}")
    if index_path and not index_path.is_file():
        raise FileNotFoundError(f"index file not found: {index_path}")
    normalized_items = []
    for item in items:
        input_path = Path(item["input"]).resolve()
        output_path = Path(item["output"]).resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"input file not found: {input_path}")
        if output_path.exists() and not arguments.overwrite:
            raise FileExistsError(output_path)
        if output_path.suffix.casefold() not in {".wav", ".flac"}:
            raise ValueError("RVC worker output must be WAV or FLAC")
        normalized_items.append((input_path, output_path))

    configure(root, model_path.parent)
    if arguments.gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(arguments.gpu)
    original_argv = sys.argv[:]
    sys.argv = [original_argv[0], "--pycmd", sys.executable, "--noautoopen"]
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import soundfile as sf  # noqa: PLC0415
            from configs.config import Config  # noqa: PLC0415
            from infer.vc.modules import VC  # noqa: PLC0415

            config = Config()
            converter = VC(config)
            converter.get_vc(model_path.name)
        results = []
        for input_path, output_path in normalized_items:
            started = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                status, result = converter.vc_single(
                    0,
                    str(input_path),
                    arguments.pitch,
                    arguments.f0,
                    str(index_path) if index_path else "",
                    arguments.index_rate if index_path else 0.0,
                    0,
                    arguments.rms_mix_rate,
                    arguments.protect,
                )
            elapsed = time.perf_counter() - started
            if not result or result[0] is None or result[1] is None:
                raise RuntimeError(str(status))
            sample_rate, audio = result
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(
                output_path,
                audio,
                sample_rate,
                format="WAV" if output_path.suffix.casefold() == ".wav" else "FLAC",
            )
            results.append(
                {
                    "input": str(input_path),
                    "output": str(output_path),
                    "sample_rate": sample_rate,
                    "duration_seconds": round(len(audio) / sample_rate, 3),
                    "elapsed_seconds": round(elapsed, 3),
                    "status": str(status),
                }
            )
    finally:
        sys.argv = original_argv
    return results, str(config.device)


def convert(arguments: argparse.Namespace) -> int:
    results, device = _convert_items(
        arguments, [{"input": arguments.input, "output": arguments.output}]
    )
    result = results[0]
    emit(
        {
            "ok": True,
            "command": "convert",
            **result,
            "model": str(Path(arguments.model).resolve()),
            "index": str(Path(arguments.index).resolve()) if arguments.index else None,
            "pitch": arguments.pitch,
            "f0": arguments.f0,
            "device": device,
        }
    )
    return 0


def convert_batch(arguments: argparse.Namespace) -> int:
    manifest_path = Path(arguments.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != "voxweave-rvc-batch" or manifest.get("version") != 1:
        raise ValueError("unsupported RVC batch manifest")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("RVC batch manifest must contain items")
    results, device = _convert_items(arguments, items)
    emit(
        {
            "ok": True,
            "command": "convert-batch",
            "manifest": str(manifest_path),
            "model": str(Path(arguments.model).resolve()),
            "index": str(Path(arguments.index).resolve()) if arguments.index else None,
            "pitch": arguments.pitch,
            "f0": arguments.f0,
            "device": device,
            "results": results,
        }
    )
    return 0


class ResidentOfflineConverter:
    """Keep one official RVC VC instance alive for consecutive offline jobs."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.model_path: Path | None = None
        self.config: Any = None
        self.converter: Any = None
        self.sf: Any = None

    def _load(self, model_path: Path, gpu: int | None) -> None:
        if self.converter is not None:
            if self.model_path != model_path:
                raise RuntimeError("resident offline worker requires restart for a model change")
            return
        configure(self.root, model_path.parent)
        if gpu is not None:
            os.environ["CUDA_VISIBLE_DEVICES"] = str(gpu)
        original_argv = sys.argv[:]
        sys.argv = [original_argv[0], "--pycmd", sys.executable, "--noautoopen"]
        try:
            with contextlib.redirect_stdout(sys.stderr):
                import soundfile as sf  # noqa: PLC0415
                from configs.config import Config  # noqa: PLC0415
                from infer.vc.modules import VC  # noqa: PLC0415

                self.config = Config()
                self.converter = VC(self.config)
                self.converter.get_vc(model_path.name)
                self.sf = sf
                self.model_path = model_path
        finally:
            sys.argv = original_argv

    def convert(self, command: dict[str, Any]) -> dict[str, Any]:
        model_path = Path(str(command["model"])).resolve()
        index_value = command.get("index")
        index_path = Path(str(index_value)).resolve() if index_value else None
        if not model_path.is_file():
            raise FileNotFoundError(f"model file not found: {model_path}")
        if index_path and not index_path.is_file():
            raise FileNotFoundError(f"index file not found: {index_path}")
        self._load(model_path, int(command.get("gpu", 0)))
        items = command.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("resident conversion requires at least one item")
        normalized: list[tuple[Path, Path]] = []
        for item in items:
            input_path = Path(str(item["input"])).resolve()
            output_path = Path(str(item["output"])).resolve()
            if not input_path.is_file():
                raise FileNotFoundError(f"input file not found: {input_path}")
            if output_path.exists() and not bool(command.get("overwrite")):
                raise FileExistsError(output_path)
            if output_path.suffix.casefold() not in {".wav", ".flac"}:
                raise ValueError("RVC worker output must be WAV or FLAC")
            normalized.append((input_path, output_path))

        results = []
        for input_path, output_path in normalized:
            started = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                status, result = self.converter.vc_single(
                    0,
                    str(input_path),
                    int(command.get("pitch", 0)),
                    str(command.get("f0", "rmvpe")),
                    str(index_path) if index_path else "",
                    float(command.get("index_rate", 0.72)) if index_path else 0.0,
                    0,
                    float(command.get("rms_mix_rate", 0.25)),
                    float(command.get("protect", 0.33)),
                )
            elapsed = time.perf_counter() - started
            if not result or result[0] is None or result[1] is None:
                raise RuntimeError(str(status))
            sample_rate, audio = result
            output_path.parent.mkdir(parents=True, exist_ok=True)
            self.sf.write(
                output_path,
                audio,
                sample_rate,
                format="WAV" if output_path.suffix.casefold() == ".wav" else "FLAC",
            )
            results.append(
                {
                    "input": str(input_path),
                    "output": str(output_path),
                    "sample_rate": sample_rate,
                    "duration_seconds": round(len(audio) / sample_rate, 3),
                    "elapsed_seconds": round(elapsed, 3),
                    "status": str(status),
                }
            )
        return {
            "ok": True,
            "command": "convert-resident",
            "model": str(model_path),
            "index": str(index_path) if index_path else None,
            "pitch": int(command.get("pitch", 0)),
            "f0": str(command.get("f0", "rmvpe")),
            "device": str(self.config.device),
            "results": results,
        }


def offline(arguments: argparse.Namespace) -> int:
    worker = ResidentOfflineConverter(Path(arguments.rvc_root))
    emit({"ok": True, "event": "worker_started"})
    for line in sys.stdin:
        command: dict[str, Any] = {}
        try:
            command = json.loads(line)
            if not isinstance(command, dict):
                continue
            if command.get("command") == "shutdown":
                break
            request_id = str(command.get("request_id") or "")
            emit({**worker.convert(command), "request_id": request_id})
        except Exception as error:  # noqa: BLE001 - resident protocol boundary
            emit(
                {
                    "ok": False,
                    "request_id": str(command.get("request_id") or "")
                    if isinstance(command, dict)
                    else "",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    emit({"ok": True, "event": "worker_stopped"})
    return 0


def _add_conversion_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", required=True)
    parser.add_argument("--index")
    parser.add_argument("--pitch", type=int, default=0)
    parser.add_argument("--f0", choices=("rmvpe", "fcpe", "pm"), default="rmvpe")
    parser.add_argument("--index-rate", type=float, default=0.72)
    parser.add_argument("--rms-mix-rate", type=float, default=0.25)
    parser.add_argument("--protect", type=float, default=0.33)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pinned official RVC adapter for VoxWeave")
    parser.add_argument("--rvc-root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.set_defaults(handler=doctor)
    devices_parser = commands.add_parser("devices")
    devices_parser.set_defaults(handler=devices)
    audio_test_parser = commands.add_parser("audio-test")
    audio_test_parser.add_argument("--mode", choices=("input", "output"), required=True)
    audio_test_parser.add_argument("--device", type=int, required=True)
    audio_test_parser.add_argument("--duration-seconds", type=float, default=2.0)
    audio_test_parser.set_defaults(handler=audio_test)
    route_test_parser = commands.add_parser("route-test")
    route_test_parser.add_argument("--input-device", type=int, required=True)
    route_test_parser.add_argument("--output-device", type=int, required=True)
    route_test_parser.add_argument("--duration-seconds", type=float, default=1.5)
    route_test_parser.set_defaults(handler=route_test)
    conversion = commands.add_parser("convert")
    conversion.add_argument("--input", required=True)
    conversion.add_argument("--output", required=True)
    _add_conversion_arguments(conversion)
    conversion.set_defaults(handler=convert)
    batch = commands.add_parser("convert-batch")
    batch.add_argument("--manifest", required=True)
    _add_conversion_arguments(batch)
    batch.set_defaults(handler=convert_batch)
    offline_parser = commands.add_parser("offline")
    offline_parser.set_defaults(handler=offline)
    realtime_parser = commands.add_parser("realtime")
    realtime_parser.add_argument("--gpu", type=int, default=0)
    realtime_parser.set_defaults(handler=realtime)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        return int(arguments.handler(arguments))
    except Exception as error:  # noqa: BLE001 - isolated engine boundary
        emit({"ok": False, "error_type": type(error).__name__, "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
