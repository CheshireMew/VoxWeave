from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voxweave.config import Settings
from voxweave.database import Database
from voxweave.hashing import sha256_file
from voxweave.model_registry import ModelRegistry
from voxweave.realtime import RealtimeSessionManager
from voxweave.rvc_engine import RvcEngine
from voxweave.rvc_realtime_audio import (
    RealtimeAudioProcessor,
    UtteranceTestMode,
    VoiceActivityDecision,
    VoiceActivityState,
    select_mono_channel,
    select_stream_spec,
    should_process_audio,
)
from voxweave.rvc_realtime_worker import WorkerControl, select_default_audio_route


class AudioSettingsProbe:
    class PortAudioError(Exception):
        pass

    def __init__(self, supported_rates: set[int]) -> None:
        self.supported_rates = supported_rates
        self.input_calls: list[dict[str, Any]] = []
        self.output_calls: list[dict[str, Any]] = []

    def _check(self, *, samplerate: int) -> None:
        if samplerate not in self.supported_rates:
            raise self.PortAudioError

    def check_input_settings(self, **arguments: Any) -> None:
        self.input_calls.append(arguments)
        self._check(samplerate=arguments["samplerate"])

    def check_output_settings(self, **arguments: Any) -> None:
        self.output_calls.append(arguments)
        self._check(samplerate=arguments["samplerate"])


def test_resident_worker_control_receives_start_without_waiting_for_pipe_eof() -> None:
    read_descriptor, write_descriptor = os.pipe()
    with (
        os.fdopen(read_descriptor, "r", encoding="utf-8") as source,
        os.fdopen(write_descriptor, "w", encoding="utf-8", buffering=1) as sink,
    ):
        control = WorkerControl(source)
        control.start()
        sink.write('{"command":"prepare","prepare_id":"prepare"}\n')
        command = control.commands.get(timeout=2)
        assert command == {"command": "prepare", "prepare_id": "prepare"}
        sink.write('{"command":"start","session_id":"session"}\n')
        command = control.commands.get(timeout=2)
        assert command == {"command": "start", "session_id": "session"}
        sink.write('{"command":"shutdown"}\n')
        control.thread.join(timeout=2)
        assert control.shutdown_requested is True


def test_realtime_cache_reloads_changed_models_and_rebuilds_changed_routes(
    tmp_path,
) -> None:
    engine = RvcEngine(Settings(data_root=str(tmp_path)))
    model = {
        "id": "voice",
        "model_path": str(tmp_path / "voice.pth"),
        "model_sha256": "a" * 64,
        "index_path": None,
        "index_sha256": None,
    }
    arguments = {
        "input_device": 1,
        "output_device": 2,
        "input_device_name": "Microphone",
        "output_device_name": "Speaker",
        "input_device_sample_rate": 48000,
        "output_device_sample_rate": 48000,
        "hostapi": "MME",
        "block_seconds": 0.5,
    }
    original = engine.realtime_payload(model, arguments)
    changed_model = engine.realtime_payload({**model, "model_sha256": "b" * 64}, arguments)
    changed_route = engine.realtime_payload(
        model, {**arguments, "output_device_name": "Headphones"}
    )
    changed_vad = engine.realtime_payload(model, {**arguments, "vad_threshold": 0.7})
    changed_gate = engine.realtime_payload(model, {**arguments, "input_gate_db": -30})
    test_mode = engine.realtime_payload(model, {**arguments, "test_mode": True})
    assert changed_model["converter_key"] != original["converter_key"]
    assert changed_model["cache_key"] != original["cache_key"]
    assert changed_route["converter_key"] == original["converter_key"]
    assert changed_route["cache_key"] != original["cache_key"]
    assert changed_vad["converter_key"] == original["converter_key"]
    assert changed_vad["cache_key"] != original["cache_key"]
    assert changed_gate["converter_key"] == original["converter_key"]
    assert changed_gate["cache_key"] != original["cache_key"]
    assert test_mode["test_mode"] is True
    assert test_mode["cache_key"] == original["cache_key"]


def test_utterance_test_mode_buffers_before_playback_and_drains_the_tail() -> None:
    mode = UtteranceTestMode()
    mode.configure(True)
    mode.begin_utterance()
    mode.buffer_output("first")
    mode.buffer_output("second")

    assert mode.phase == "capture"
    assert mode.mark_silence(2) is False
    mode.mark_speech()
    assert mode.mark_silence(2) is False
    assert mode.mark_silence(2) is True
    assert mode.start_playback() == "first"
    assert mode.phase == "playback"
    assert mode.playback_output() == "second"
    assert mode.playback_output() is None
    assert mode.phase == "tail"
    assert mode.finish_tail_callback() is True
    assert mode.phase == "capture"


def test_realtime_test_mode_plays_the_complete_utterance_after_speech_ends() -> None:
    class FakeVad:
        def __init__(self) -> None:
            self.resets = 0

        def process(self, mono):
            active = bool(np.max(np.abs(mono)) >= 0.5)
            return VoiceActivityDecision(active, active, 0.9 if active else 0.0)

        def reset(self) -> None:
            self.resets += 1

    class FakeConverted:
        def repeat(self, *_shape):
            return self

        def t(self):
            return self

        def float(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return np.full((4, 1), 0.25, dtype=np.float32)

    processor = RealtimeAudioProcessor.__new__(RealtimeAudioProcessor)
    processor.np = np
    processor.sd = SimpleNamespace(CallbackAbort=RuntimeError)
    processor.spec = SimpleNamespace(output_channels=1, block_frame=4, sample_rate=8)
    processor.vad = FakeVad()
    processor.test_session = UtteranceTestMode()
    processor.test_session.configure(True)
    processor.metrics_lock = threading.Lock()
    processor.metrics = {
        "callbacks": 0,
        "inference_callbacks": 0,
        "skipped_callbacks": 0,
        "suppressed_callbacks": 0,
        "completed_utterances": 0,
        "xruns": 0,
    }
    processor.callback_error = []
    processor.warming = False
    processor.output_active = False
    processor.input_gate_db = -30.0
    accepted_inputs = []
    processor._push_input = lambda mono, _frames: accepted_inputs.append(mono.copy()) or 1
    processor._infer = lambda _frames: FakeConverted()
    processor._match_rms = lambda _inferred: None
    processor._crossfade = lambda inferred: inferred
    processor._clear_output_state = lambda: None
    processor._clear_conversion_state = lambda: setattr(processor, "output_active", False)

    noise = np.full((4, 1), 0.02, dtype=np.float32)
    noise_output = np.zeros((4, 1), dtype=np.float32)
    processor.callback(noise, noise_output, 4, None, None)
    assert np.all(noise_output == 0)
    assert processor.test_session.utterance_active is False

    speech = np.ones((4, 1), dtype=np.float32)
    silence = np.zeros((4, 1), dtype=np.float32)
    feedback = np.ones((4, 1), dtype=np.float32)
    outputs = [np.zeros((4, 1), dtype=np.float32) for _ in range(8)]

    processor.callback(speech, outputs[0], 4, None, None)
    processor.callback(speech, outputs[1], 4, None, None)
    processor.callback(silence, outputs[2], 4, None, None)
    processor.callback(silence, outputs[3], 4, None, None)
    processor.callback(feedback, outputs[4], 4, None, None)
    processor.callback(feedback, outputs[5], 4, None, None)
    processor.callback(feedback, outputs[6], 4, None, None)
    processor.callback(speech, outputs[7], 4, None, None)

    assert np.all(outputs[0] == 0)
    assert np.all(outputs[1] == 0)
    assert np.all(outputs[2] == 0)
    assert np.all(outputs[3] == 0.25)
    assert np.all(outputs[4] == 0.25)
    assert np.all(outputs[5] == 0)
    assert np.all(outputs[6] == 0)
    assert np.all(outputs[7] == 0)
    assert np.all(accepted_inputs[0] == 1)
    assert np.all(accepted_inputs[1] == 1)
    assert np.all(accepted_inputs[2] == 1)
    assert len(accepted_inputs) == 3
    assert processor.metrics["inference_callbacks"] == 3
    assert processor.metrics["suppressed_callbacks"] == 3
    assert processor.metrics["completed_utterances"] == 1
    assert processor.metrics["test_phase"] == "capture"
    assert processor.vad.resets == 2

    processor.test_session.configure(False)
    normal_output = np.zeros((4, 1), dtype=np.float32)
    processor.callback(speech, normal_output, 4, None, None)
    assert np.all(normal_output == 0.25)
    assert processor.metrics["test_mode"] is False
    assert processor.metrics["test_phase"] == "off"


def test_voice_activity_state_requires_speech_and_holds_the_end_of_an_utterance() -> None:
    state = VoiceActivityState(0.55)

    assert state.update(0.8) is False
    assert state.update(0.9) is True
    assert state.active is True
    for _ in range(7):
        assert state.update(0.1) is True
    assert state.update(0.1) is True
    assert state.active is False
    assert state.update(0.1) is False


def test_input_level_prevents_vad_from_muting_audible_microphone_audio() -> None:
    rejected = VoiceActivityDecision(
        process_block=False,
        active=False,
        probability=0.0,
    )
    accepted = VoiceActivityDecision(
        process_block=True,
        active=True,
        probability=0.9,
    )

    assert should_process_audio(rejected, -29.0, -30.0) is True
    assert should_process_audio(rejected, -37.4, -30.0) is False
    assert should_process_audio(accepted, -90.0, -30.0) is True


def test_realtime_stream_spec_uses_a_rate_supported_by_both_devices() -> None:
    probe = AudioSettingsProbe({48000})
    spec = select_stream_spec(
        probe,
        1,
        2,
        {"max_input_channels": 2, "default_samplerate": 44100},
        {"max_output_channels": 2, "default_samplerate": 48000},
        40000,
        0.5,
        0.05,
        2.5,
    )
    assert spec.sample_rate == 48000
    assert spec.input_channels == 1
    assert spec.output_channels == 2
    assert probe.input_calls[-1]["channels"] == 1
    assert probe.output_calls[-1]["channels"] == 2
    assert spec.block_frame == 24000
    assert spec.sola_buffer_frame == 1920


def test_realtime_mono_capture_does_not_cancel_opposed_array_channels() -> None:
    signal = np.linspace(-0.5, 0.5, 128, dtype=np.float32)
    array_input = np.column_stack((signal, -signal))

    selected = select_mono_channel(np, array_input)

    assert np.max(np.abs(np.mean(array_input, axis=1))) == 0
    assert np.array_equal(selected, signal)


def test_windows_wasapi_route_is_preferred_over_silent_mme_defaults() -> None:
    hostapis = [
        {
            "id": 0,
            "name": "MME",
            "default_input_device": 1,
            "default_output_device": 3,
        },
        {
            "id": 2,
            "name": "Windows WASAPI",
            "default_input_device": 12,
            "default_output_device": 11,
        },
    ]
    devices = [
        {
            "id": 1,
            "hostapi_id": 0,
            "input_channels": 2,
            "output_channels": 0,
        },
        {
            "id": 3,
            "hostapi_id": 0,
            "input_channels": 0,
            "output_channels": 2,
        },
        {
            "id": 11,
            "hostapi_id": 2,
            "input_channels": 0,
            "output_channels": 2,
        },
        {
            "id": 12,
            "hostapi_id": 2,
            "input_channels": 2,
            "output_channels": 0,
        },
    ]

    assert select_default_audio_route(hostapis, devices, 1, 3) == (12, 11)


def test_realtime_stream_spec_rejects_incompatible_devices() -> None:
    with pytest.raises(RuntimeError, match="no compatible sample rate"):
        select_stream_spec(
            AudioSettingsProbe(set()),
            1,
            2,
            {"max_input_channels": 1, "default_samplerate": 44100},
            {"max_output_channels": 2, "default_samplerate": 48000},
            40000,
            0.5,
            0.05,
            2.5,
        )


def register_model(database: Database, tmp_path: Path) -> None:
    model_path = tmp_path / "voice.pth"
    model_path.write_bytes(b"voice-model")
    database.execute(
        "INSERT INTO models("
        "id,display_name,aliases_json,family,checkpoint_epoch,model_path,model_sha256,"
        "index_path,index_sha256,index_candidates_json,rvc_version,sample_rate,f0,"
        "source_kind,license_spdx,source_url,recommended_json,status,imported_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "voice",
            "Voice",
            "[]",
            "voice",
            None,
            str(model_path),
            sha256_file(model_path),
            None,
            None,
            "[]",
            "v2",
            40000,
            1,
            "local",
            None,
            None,
            "{}",
            "ready",
            "now",
        ),
    )


class ProcessEngine:
    def __init__(self, working_directory: Path) -> None:
        self.working_directory = working_directory

    @staticmethod
    def _worker_code() -> str:
        return """
import json
import sys

def emit(event, **values):
    print(json.dumps({"ok": True, "event": event, **values}), flush=True)

prepared_key = None
emit("worker_started")
for line in sys.stdin:
    message = json.loads(line)
    command = message["command"]
    if command in {"prepare", "start"}:
        common = {"model_id": message["model_id"]}
        if command == "prepare":
            common["prepare_id"] = message["prepare_id"]
        else:
            common["session_id"] = message["session_id"]
        if prepared_key != message["cache_key"]:
            emit("warming", **common)
            prepared_key = message["cache_key"]
        emit("ready", cache_key=message["cache_key"], **common)
        if command == "prepare":
            continue
        emit("running", estimated_latency_ms=550, **common)
        emit("metrics", callbacks=1, infer_ms=120, overloaded=False, **common)
    elif command == "stop":
        emit("stopped", session_id=message["session_id"])
    elif command == "shutdown":
        emit("worker_stopped")
        break
"""

    def realtime_worker_command(self) -> tuple[list[str], Path]:
        return [
            sys.executable,
            "-u",
            "-c",
            self._worker_code(),
        ], self.working_directory / "worker.py"

    @staticmethod
    def realtime_payload(model: dict[str, Any], _arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "command": "start",
            "model_id": model["id"],
            "cache_key": f"prepared-{model['id']}",
        }

    @staticmethod
    def audio_devices() -> dict[str, Any]:
        return {
            "hostapis": [
                {
                    "id": 0,
                    "name": "Test Host",
                    "default_input_device": 1,
                    "default_output_device": 2,
                }
            ],
            "devices": [
                {
                    "id": 1,
                    "name": "Test Microphone",
                    "hostapi_id": 0,
                    "hostapi": "Test Host",
                    "input_channels": 1,
                    "output_channels": 0,
                    "default_sample_rate": 48000,
                },
                {
                    "id": 2,
                    "name": "Test Speaker",
                    "hostapi_id": 0,
                    "hostapi": "Test Host",
                    "input_channels": 0,
                    "output_channels": 2,
                    "default_sample_rate": 48000,
                },
            ],
            "default_input_device": 1,
            "default_output_device": 2,
        }

class DelayedStopEngine(ProcessEngine):
    @staticmethod
    def _worker_code() -> str:
        return """
import json
import sys
import time

def emit(event, **values):
    print(json.dumps({"ok": True, "event": event, **values}), flush=True)

emit("worker_started")
for line in sys.stdin:
    message = json.loads(line)
    if message["command"] == "start":
        time.sleep(0.25)
        common = {"session_id": message["session_id"], "model_id": message["model_id"]}
        emit("ready", cache_key=message["cache_key"], **common)
        emit("running", **common)
    elif message["command"] == "stop":
        emit("stopped", session_id=message["session_id"])
    elif message["command"] == "shutdown":
        break
"""


class IgnoringStopEngine(ProcessEngine):
    @staticmethod
    def _worker_code() -> str:
        return """
import json
import sys
import time

def emit(event, **values):
    print(json.dumps({"ok": True, "event": event, **values}), flush=True)

emit("worker_started")
message = json.loads(sys.stdin.readline())
emit(
    "running",
    session_id=message["session_id"],
    model_id=message["model_id"],
)
time.sleep(30)
"""


def wait_for_state(
    manager: RealtimeSessionManager, expected: str, timeout: float = 5
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status()
        if status["state"] == expected:
            return status
        time.sleep(0.02)
    raise AssertionError(f"realtime session did not reach {expected}: {manager.status()}")


def wait_for_worker_state(
    manager: RealtimeSessionManager, expected: str, timeout: float = 5
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status()
        if status["worker"]["state"] == expected:
            return status
        time.sleep(0.02)
    raise AssertionError(f"realtime worker did not reach {expected}: {manager.status()}")


def test_realtime_model_prepares_before_audio_session_starts(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    database = Database(settings.database_path)
    register_model(database, tmp_path)
    manager = RealtimeSessionManager(
        database,
        ModelRegistry(database),
        ProcessEngine(tmp_path),  # type: ignore[arg-type]
    )
    arguments = {
        "model": "voice",
        "input_device": 1,
        "output_device": 2,
        "block_seconds": 0.5,
    }

    preparing = manager.prepare(arguments)
    assert preparing["session_id"] is None
    prepared = wait_for_worker_state(manager, "ready")
    assert prepared["state"] == "idle"
    assert prepared["worker"]["model_id"] == "voice"
    assert prepared["worker"]["model_ready"] is True
    worker_pid = prepared["worker"]["pid"]

    submitted = manager.start(arguments)
    running = wait_for_state(manager, "running")
    assert running["worker"]["pid"] == worker_pid
    assert [event["stage"] for event in manager.events(submitted["id"])] == [
        "waiting_for_worker",
        "ready",
        "streaming",
    ]
    manager.stop()
    wait_for_state(manager, "stopped")
    manager.shutdown()


def test_realtime_subprocess_lifecycle_is_persisted(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    database = Database(settings.database_path)
    register_model(database, tmp_path)
    manager = RealtimeSessionManager(
        database,
        ModelRegistry(database),
        ProcessEngine(tmp_path),  # type: ignore[arg-type]
    )
    submitted = manager.start(
        {"model": "voice", "input_device": 1, "output_device": 2, "block_seconds": 0.5}
    )
    assert submitted["state"] == "starting"
    assert submitted["arguments"]["input_device_name"] == "Test Microphone"
    assert submitted["arguments"]["output_device_name"] == "Test Speaker"
    running = wait_for_state(manager, "running")
    assert running["metrics"]["estimated_latency_ms"] == 550
    assert running["worker"]["state"] == "ready"
    assert running["worker"]["model_ready"] is True
    worker_pid = running["worker"]["pid"]
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and manager.status()["metrics"].get("callbacks") != 1:
        time.sleep(0.02)
    assert manager.status()["metrics"] == {
        "estimated_latency_ms": 550,
        "event": "metrics",
        "ok": True,
        "callbacks": 1,
        "infer_ms": 120,
        "overloaded": False,
    }
    stopping = manager.stop()
    assert stopping["state"] == "stopping"
    stopped = wait_for_state(manager, "stopped")
    events = manager.events(stopped["id"])
    assert [event["stage"] for event in events] == [
        "waiting_for_worker",
        "warming",
        "ready",
        "streaming",
        "stopping",
        "stopped",
    ]
    assert stopped["worker"] == {
        "state": "ready",
        "pid": worker_pid,
        "model_id": "voice",
        "model_ready": True,
    }

    restarted = manager.start(
        {"model": "voice", "input_device": 1, "output_device": 2, "block_seconds": 0.5}
    )
    restarted_running = wait_for_state(manager, "running")
    assert restarted_running["worker"]["pid"] == worker_pid
    assert [event["stage"] for event in manager.events(restarted["id"])] == [
        "waiting_for_worker",
        "ready",
        "streaming",
    ]
    manager.stop()
    wait_for_state(manager, "stopped")
    manager.shutdown()


def test_realtime_manager_owns_restart_recovery(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    database = Database(settings.database_path)
    register_model(database, tmp_path)
    database.execute(
        "INSERT INTO realtime_sessions("
        "id,model_id,model_sha256,arguments_json,state,stage,created_at,updated_at) "
        "VALUES('active','voice',?,'{}','running','streaming','now','now')",
        ("a" * 64,),
    )
    manager = RealtimeSessionManager(
        database,
        ModelRegistry(database),
        ProcessEngine(tmp_path),  # type: ignore[arg-type]
    )
    recovered = manager.status()
    assert recovered["state"] == "interrupted"
    assert recovered["stage"] == "service_restart"
    assert manager.events("active")[-1]["state"] == "interrupted"
    manager.shutdown()


def test_stop_during_starting_cannot_be_overwritten_by_late_running_event(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    database = Database(settings.database_path)
    register_model(database, tmp_path)
    manager = RealtimeSessionManager(
        database,
        ModelRegistry(database),
        DelayedStopEngine(tmp_path),  # type: ignore[arg-type]
    )
    submitted = manager.start({"model": "voice", "input_device": 1, "output_device": 2})
    assert manager.stop()["state"] == "stopping"
    stopped = wait_for_state(manager, "stopped")
    states = [event["state"] for event in manager.events(submitted["id"])]
    assert states == ["starting", "stopping", "stopped"]
    assert stopped["stage"] == "stopped"
    manager.shutdown()


def test_unresponsive_realtime_worker_is_killed_at_stop_deadline(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("voxweave.realtime.STOP_TIMEOUT_SECONDS", 0.3)
    settings = Settings(data_root=str(tmp_path))
    database = Database(settings.database_path)
    register_model(database, tmp_path)
    manager = RealtimeSessionManager(
        database,
        ModelRegistry(database),
        IgnoringStopEngine(tmp_path),  # type: ignore[arg-type]
    )
    submitted = manager.start({"model": "voice", "input_device": 1, "output_device": 2})
    wait_for_state(manager, "running")
    manager.stop()
    failed = wait_for_state(manager, "failed", timeout=3)
    assert failed["error_type"] == "stop_timeout"
    assert [event["state"] for event in manager.events(submitted["id"])].count("failed") == 1
    manager.shutdown()
