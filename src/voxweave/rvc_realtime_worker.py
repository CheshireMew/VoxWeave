from __future__ import annotations

import contextlib
import gc
import json
import os
import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

if __package__:
    from .rvc_realtime_audio import (
        RealtimeAudioProcessor,
        run_audio_stream,
        select_stream_spec,
    )
else:
    from rvc_realtime_audio import (  # type: ignore[no-redef]
        RealtimeAudioProcessor,
        run_audio_stream,
        select_stream_spec,
    )


def _emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def select_default_audio_route(
    hostapis: list[dict[str, Any]],
    devices: list[dict[str, Any]],
    fallback_input: int,
    fallback_output: int,
) -> tuple[int, int]:
    devices_by_id = {int(device["id"]): device for device in devices}

    def valid_route(input_id: int, output_id: int) -> bool:
        input_device = devices_by_id.get(input_id)
        output_device = devices_by_id.get(output_id)
        return bool(
            input_device
            and output_device
            and int(input_device["input_channels"]) > 0
            and int(output_device["output_channels"]) > 0
            and input_device["hostapi_id"] == output_device["hostapi_id"]
        )

    for hostapi in hostapis:
        if str(hostapi["name"]).casefold() != "windows wasapi":
            continue
        input_id = int(hostapi["default_input_device"])
        output_id = int(hostapi["default_output_device"])
        if valid_route(input_id, output_id):
            return input_id, output_id

    if valid_route(fallback_input, fallback_output):
        return fallback_input, fallback_output

    for hostapi in hostapis:
        input_id = int(hostapi["default_input_device"])
        output_id = int(hostapi["default_output_device"])
        if valid_route(input_id, output_id):
            return input_id, output_id
    raise RuntimeError("no audio host provides both an input and an output device")


def list_audio_devices() -> dict[str, Any]:
    import sounddevice as sd  # noqa: PLC0415

    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    hostapi_items = [
        {
            "id": index,
            "name": hostapi["name"],
            "default_input_device": hostapi["default_input_device"],
            "default_output_device": hostapi["default_output_device"],
        }
        for index, hostapi in enumerate(hostapis)
    ]
    device_items = []
    for index, device in enumerate(devices):
        hostapi_id = int(device["hostapi"])
        device_items.append(
            {
                "id": index,
                "name": device["name"],
                "hostapi_id": hostapi_id,
                "hostapi": hostapis[hostapi_id]["name"],
                "input_channels": int(device["max_input_channels"]),
                "output_channels": int(device["max_output_channels"]),
                "default_sample_rate": int(device["default_samplerate"]),
            }
        )
    default_input, default_output = select_default_audio_route(
        hostapi_items,
        device_items,
        int(sd.default.device[0]),
        int(sd.default.device[1]),
    )
    for device in device_items:
        device["default_input"] = device["id"] == default_input
        device["default_output"] = device["id"] == default_output
    return {
        "ok": True,
        "command": "devices",
        "hostapis": hostapi_items,
        "devices": device_items,
        "default_input_device": default_input,
        "default_output_device": default_output,
    }


def _validate_audio_devices(sd: Any, input_device: int, output_device: int) -> tuple[Any, Any, str]:
    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    if not 0 <= input_device < len(devices):
        raise ValueError(f"input device does not exist: {input_device}")
    if not 0 <= output_device < len(devices):
        raise ValueError(f"output device does not exist: {output_device}")
    input_info = devices[input_device]
    output_info = devices[output_device]
    if input_info["max_input_channels"] < 1:
        raise ValueError(f"device is not an audio input: {input_device}")
    if output_info["max_output_channels"] < 1:
        raise ValueError(f"device is not an audio output: {output_device}")
    if input_info["hostapi"] != output_info["hostapi"]:
        raise ValueError("input and output devices must use the same Windows audio host API")
    return input_info, output_info, hostapis[int(input_info["hostapi"])]["name"]


def _load_converter(arguments: Any, model_path: Path, index_path: Path | None) -> tuple[Any, Any]:
    from configs.config import Config  # noqa: PLC0415
    from infer.rtrvc import RVC  # noqa: PLC0415

    with contextlib.redirect_stdout(sys.stderr):
        config = Config()
        converter = RVC(
            int(arguments.pitch),
            0.0,
            str(model_path),
            str(index_path) if index_path else "",
            float(arguments.index_rate) if index_path else 0.0,
            config,
        )
    if not getattr(converter, "net_g", None) or not getattr(converter, "tgt_sr", None):
        raise RuntimeError("RVC realtime model initialization failed")
    return config, converter


def _load_vad_model() -> Any:
    from silero_vad import load_silero_vad  # noqa: PLC0415

    return load_silero_vad(onnx=True)


def _release_cuda(torch: Any) -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


class ResidentRealtimeWorker:
    def __init__(
        self,
        *,
        np: Any,
        sd: Any,
        torch: Any,
        functional: Any,
        transforms: Any,
    ) -> None:
        self.np = np
        self.sd = sd
        self.torch = torch
        self.functional = functional
        self.transforms = transforms
        self.config: Any = None
        self.converter: Any = None
        self.vad_model: Any = None
        self.processor: RealtimeAudioProcessor | None = None
        self.converter_key: str | None = None
        self.prepared_key: str | None = None

    def close(self) -> None:
        self.processor = None
        self.converter = None
        self.vad_model = None
        self.config = None
        _release_cuda(self.torch)

    def run_session(self, command: dict[str, Any], stop_event: threading.Event) -> None:
        session_id = str(command["session_id"])
        model_id = str(command.get("model_id") or "")

        def emit_session(payload: dict[str, Any]) -> None:
            _emit({**payload, "session_id": session_id, "model_id": model_id})

        try:
            arguments = SimpleNamespace(**command)
            model_path = Path(arguments.model).resolve()
            index_path = Path(arguments.index).resolve() if arguments.index else None
            if not model_path.is_file():
                raise FileNotFoundError(f"model file not found: {model_path}")
            if index_path and not index_path.is_file():
                raise FileNotFoundError(f"index file not found: {index_path}")

            input_device = int(arguments.input_device)
            output_device = int(arguments.output_device)
            cache_key = str(arguments.cache_key)
            cached = self.prepared_key == cache_key and self.processor is not None
            if not cached:
                self.prepared_key = None
                self.processor = None
                next_converter_key = str(arguments.converter_key)
                emit_session({"ok": True, "event": "warming"})
                if self.converter_key != next_converter_key:
                    self.converter = None
                    self.config = None
                    self.converter_key = None
                    _release_cuda(self.torch)
                    self.config, self.converter = _load_converter(arguments, model_path, index_path)
                    self.converter_key = next_converter_key
                if self.vad_model is None:
                    self.vad_model = _load_vad_model()

                input_info, output_info, hostapi = _validate_audio_devices(
                    self.sd, input_device, output_device
                )
                spec = select_stream_spec(
                    self.sd,
                    input_device,
                    output_device,
                    input_info,
                    output_info,
                    int(self.converter.tgt_sr),
                    float(arguments.block_seconds),
                    float(arguments.crossfade_seconds),
                    float(arguments.extra_seconds),
                )
                self.processor = RealtimeAudioProcessor(
                    np=self.np,
                    torch=self.torch,
                    functional=self.functional,
                    transforms=self.transforms,
                    sd=self.sd,
                    converter=self.converter,
                    vad_model=self.vad_model,
                    device=self.config.device,
                    spec=spec,
                    vad_threshold=float(arguments.vad_threshold),
                    input_gate_db=float(arguments.input_gate_db),
                    rms_mix_rate=float(arguments.rms_mix_rate),
                    f0_method=arguments.f0,
                )
                self.processor.warmup()
                self.prepared_key = cache_key
            else:
                _input_info, _output_info, hostapi = _validate_audio_devices(
                    self.sd, input_device, output_device
                )
                spec = self.processor.spec

            emit_session(
                {
                    "ok": True,
                    "event": "ready",
                    "cache_key": cache_key,
                    "cached": cached,
                    "device": str(self.config.device),
                    "sample_rate": spec.sample_rate,
                    "block_frames": spec.block_frame,
                }
            )
            if not stop_event.is_set():
                run_audio_stream(
                    sd=self.sd,
                    processor=self.processor,
                    input_device=input_device,
                    output_device=output_device,
                    hostapi=hostapi,
                    block_seconds=float(arguments.block_seconds),
                    test_mode=bool(arguments.test_mode),
                    emit=emit_session,
                    stop_event=stop_event,
                )
                self.processor.reset()
            emit_session({"ok": True, "event": "stopped"})
        except Exception as error:  # noqa: BLE001 - resident worker command boundary
            if self.processor is not None and self.prepared_key is not None:
                with contextlib.suppress(Exception):
                    self.processor.reset()
            emit_session(
                {
                    "ok": False,
                    "event": "failed",
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "model_ready": (self.processor is not None and self.prepared_key is not None),
                    "cache_key": self.prepared_key,
                }
            )


class WorkerControl:
    def __init__(self, source: Any) -> None:
        self.source = source
        self.commands: queue.Queue[dict[str, Any]] = queue.Queue()
        self.lock = threading.Lock()
        self.pending_stops: set[str] = set()
        self.active_session_id: str | None = None
        self.active_stop_event: threading.Event | None = None
        self.shutdown_requested = False
        self.thread = threading.Thread(
            target=self._read, name="voxweave-realtime-control", daemon=True
        )

    def start(self) -> None:
        self.thread.start()

    def _read(self) -> None:
        if os.name == "nt":
            self._read_windows_pipe()
            return
        while True:
            line = self.source.readline()
            if not line:
                self.request_shutdown()
                return
            if not self._handle_line(line):
                return

    def _read_windows_pipe(self) -> None:
        import ctypes  # noqa: PLC0415
        import msvcrt  # noqa: PLC0415

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.PeekNamedPipe.argtypes = (
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_ulong),
            ctypes.c_void_p,
        )
        kernel32.PeekNamedPipe.restype = ctypes.c_int
        handle = ctypes.c_void_p(msvcrt.get_osfhandle(self.source.fileno()))
        available = ctypes.c_ulong()
        while True:
            ready = kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None)
            if not ready:
                error = ctypes.get_last_error()
                if error in {109, 232}:
                    self.request_shutdown()
                    return
                raise ctypes.WinError(error)
            if available.value == 0:
                time.sleep(0.05)
                continue
            line = self.source.readline()
            if not line:
                self.request_shutdown()
                return
            if not self._handle_line(line):
                return

    def _handle_line(self, line: str) -> bool:
        try:
            payload = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            return True
        if not isinstance(payload, dict):
            return True
        command = payload.get("command")
        if command == "start":
            self.commands.put(payload)
        elif command == "stop":
            self.request_stop(str(payload.get("session_id") or ""))
        elif command == "shutdown":
            self.request_shutdown()
            return False
        return True

    def request_stop(self, session_id: str) -> None:
        with self.lock:
            if session_id and session_id == self.active_session_id:
                if self.active_stop_event:
                    self.active_stop_event.set()
                return
            if session_id:
                self.pending_stops.add(session_id)

    def request_shutdown(self) -> None:
        with self.lock:
            if self.shutdown_requested:
                return
            self.shutdown_requested = True
            if self.active_stop_event:
                self.active_stop_event.set()
        self.commands.put({"command": "shutdown"})

    def begin_session(self, session_id: str) -> threading.Event:
        stop_event = threading.Event()
        with self.lock:
            self.active_session_id = session_id
            self.active_stop_event = stop_event
            if self.shutdown_requested or session_id in self.pending_stops:
                stop_event.set()
            self.pending_stops.discard(session_id)
        return stop_event

    def end_session(self, session_id: str) -> None:
        with self.lock:
            if self.active_session_id != session_id:
                return
            self.active_session_id = None
            self.active_stop_event = None


def run_resident_worker() -> int:
    import numpy as np  # noqa: PLC0415
    import sounddevice as sd  # noqa: PLC0415
    import torch  # noqa: PLC0415
    import torch.nn.functional as functional  # noqa: PLC0415
    import torchaudio.transforms as transforms  # noqa: PLC0415

    worker = ResidentRealtimeWorker(
        np=np,
        sd=sd,
        torch=torch,
        functional=functional,
        transforms=transforms,
    )
    control = WorkerControl(sys.stdin)
    control.start()
    original_argv = sys.argv[:]
    sys.argv = [original_argv[0], "--pycmd", sys.executable, "--noautoopen"]
    _emit({"ok": True, "event": "worker_started"})
    try:
        while True:
            payload = control.commands.get()
            if payload.get("command") == "shutdown":
                break
            session_id = str(payload.get("session_id") or "")
            if not session_id:
                continue
            stop_event = control.begin_session(session_id)
            worker.run_session(payload, stop_event)
            control.end_session(session_id)
            if control.shutdown_requested:
                break
    finally:
        sys.argv = original_argv
        worker.close()
    _emit({"ok": True, "event": "worker_stopped"})
    return 0
