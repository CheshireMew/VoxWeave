from __future__ import annotations

import urllib.error
from email.message import Message
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
import soundfile as sf

from voxweave.batch_output import output_path
from voxweave.config import Settings
from voxweave.conversion_runner import ConversionRunner, output_keeps_video
from voxweave.database import Database
from voxweave.gui_realtime_controls import parse_windows_hotkey
from voxweave.hashing import FileVerificationLedger
from voxweave.mcp_server import VoxWeaveMcpServer
from voxweave.protocol import parse_arguments, validate_execute_result
from voxweave.realtime_scenes import RealtimeSceneRepository, RealtimeWorkspaceService
from voxweave.rvc_realtime_audio import RealtimeAudioRecorder
from voxweave.updater import UpdateService


def _scene_arguments() -> dict[str, Any]:
    return {
        "name": "Streaming",
        "settings": {
            "model": "voice.one",
            "hostapi": "Windows WASAPI",
            "input_device": "Microphone",
            "output_device": "CABLE Input",
            "pitch": 0,
            "f0": "rmvpe",
            "index_rate": 0.72,
            "rms_mix_rate": 0.25,
            "vad_threshold": 0.5,
            "input_gate_db": -42,
            "block_seconds": 0.5,
            "test_mode": False,
            "recording": True,
        },
        "hotkeys": {
            "start_stop": "Ctrl+Alt+F9",
            "bypass": "Ctrl+Alt+F10",
            "mute": "Ctrl+Alt+F11",
        },
    }


def test_output_keeps_video_only_for_video_container_targets(tmp_path) -> None:
    video = {"media_type": "video"}
    audio = {"media_type": "audio"}
    assert output_keeps_video(video, tmp_path / "result.mp4") is True
    assert output_keeps_video(video, tmp_path / "result.mp3") is False
    assert output_keeps_video(audio, tmp_path / "result.mkv") is False


def test_realtime_scene_lifecycle_and_device_name_resolution(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    repository = RealtimeSceneRepository(database)
    scene = repository.create(_scene_arguments())
    assert scene["revision"] == 1
    updated = repository.update(
        {
            "scene_id": scene["id"],
            "expected_revision": 1,
            "name": "Meeting",
            "settings": None,
            "hotkeys": None,
        }
    )
    assert updated["name"] == "Meeting"
    assert updated["revision"] == 2

    class FakeRealtime:
        def __init__(self) -> None:
            self.started: dict[str, Any] = {}

        @staticmethod
        def devices() -> dict[str, Any]:
            return {
                "devices": [
                    {
                        "id": 3,
                        "name": "Microphone",
                        "hostapi_id": 1,
                        "hostapi": "Windows WASAPI",
                        "input_channels": 1,
                        "output_channels": 0,
                    },
                    {
                        "id": 4,
                        "name": "CABLE Input",
                        "hostapi_id": 1,
                        "hostapi": "Windows WASAPI",
                        "input_channels": 0,
                        "output_channels": 2,
                    },
                ]
            }

        def start(self, arguments: dict[str, Any]) -> dict[str, Any]:
            self.started = arguments
            return {
                "session_id": "session",
                "state": "starting",
                "stage": "warming",
                "metrics": {},
                "worker": {
                    "state": "warming",
                    "pid": 1,
                    "model_id": "voice.one",
                    "model_ready": False,
                },
            }

    fake = FakeRealtime()
    workspace = RealtimeWorkspaceService(database, fake)  # type: ignore[arg-type]
    result = workspace.apply({"scene_id": scene["id"], "start": True, "recording": None})
    validate_execute_result("realtime.start", result)
    assert fake.started["input_device"] == 3
    assert fake.started["output_device"] == 4
    routing = validate_execute_result("realtime.routing.inspect", workspace.routing())
    assert routing["virtual_audio_available"] is True


def test_realtime_recorder_writes_dry_and_wet_files(tmp_path) -> None:
    recorder = RealtimeAudioRecorder(np, str(tmp_path), "session", 8000, 2)
    recorder.enqueue(
        np.ones(80, dtype=np.float32) * 0.1,
        np.ones((80, 2), dtype=np.float32) * 0.2,
    )
    result = recorder.close()
    assert sf.info(result["recording_dry_path"]).frames == 80
    assert sf.info(result["recording_wet_path"]).channels == 2


def test_processing_chain_and_advanced_batch_contracts(tmp_path) -> None:
    input_path = tmp_path / "voice.wav"
    input_path.write_bytes(b"voice")
    conversion_output = tmp_path / "output.wav"
    parsed = parse_arguments(
        "conversion.run",
        {
            "input": str(input_path.resolve()),
            "output": str(conversion_output.resolve()),
            "model": "voice.one",
            "processing_chain": {
                "noise_reduction_db": 12,
                "compressor": True,
                "target_lufs": -16,
            },
        },
    )
    assert parsed["processing_chain"]["noise_reduction_db"] == 12
    assert parsed["processing_chain"]["limiter_dbfs"] == -1

    nested = tmp_path / "input" / "show"
    nested.mkdir(parents=True)
    source = nested / "take.flac"
    source.write_bytes(b"audio")
    rule = {
        "input_root": str(tmp_path / "input"),
        "output_root": str(tmp_path / "output"),
        "model_id": "voice.one",
        "preset_name": "clean",
        "naming_template": "{stem}-{source_ext}-{model}-{preset}-{hash}",
        "preserve_structure": True,
        "output_format": "mp3",
    }
    output = output_path(rule, source, "a" * 64)
    assert output.parent.name == "show"
    assert output.suffix == ".mp3"
    assert "take-flac-voice.one-clean" in output.name


def test_conversion_runner_passes_processing_settings_to_output_chain(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "converted.wav"
    source.write_bytes(b"converted")
    captured = {}

    def process(_settings, incoming, output, chain, cancelled):
        captured.update(incoming=incoming, chain=chain, cancelled=cancelled)
        output.write_bytes(b"processed")
        return {"enabled": True, "settings": chain, "filters": ["acompressor"]}

    monkeypatch.setattr("voxweave.conversion_runner.process_audio_chain", process)
    runner = ConversionRunner.__new__(ConversionRunner)
    runner.settings = object()

    def cancelled() -> bool:
        return False

    state = SimpleNamespace(
        arguments={"processing_chain": {"compressor": True, "limiter_dbfs": -1}},
        context=SimpleNamespace(progress=lambda *_args: None, cancelled=cancelled),
        work_dir=tmp_path,
        files=FileVerificationLedger(),
        checkpoint={"stages": {}},
        checkpoint_path=tmp_path / "checkpoint.json",
    )

    processed, metadata = runner._process_output_chain(state, source)

    assert processed == tmp_path / "processed-output.wav"
    assert captured["chain"]["compressor"] is True
    assert captured["cancelled"] is cancelled
    assert metadata["filters"] == ["acompressor"]


def test_windows_hotkey_parser_and_mcp_dispatch(monkeypatch, tmp_path) -> None:
    modifiers, key = parse_windows_hotkey("Ctrl+Alt+F9")
    assert modifiers & 0x0001 and modifiers & 0x0002
    assert key == 0x78
    with pytest.raises(ValueError, match="duplicate hotkey modifier"):
        parse_windows_hotkey("Ctrl+Control+F9")
    with pytest.raises(ValueError, match="must be unique"):
        parse_arguments(
            "realtime.scene.create",
            {
                **_scene_arguments(),
                "hotkeys": {
                    "start_stop": "Ctrl+Alt+F9",
                    "bypass": "Ctrl+Alt+F9",
                    "mute": "Ctrl+Alt+F11",
                },
            },
        )

    server = VoxWeaveMcpServer.__new__(VoxWeaveMcpServer)
    server.settings = Settings(data_root=str(tmp_path))
    server.operations = {}
    server.session_id = "test-session"
    monkeypatch.setattr(
        server,
        "_describe",
        lambda: {
            "operations": {
                "model.list": {
                    "arguments_schema": {"type": "object", "properties": {}},
                    "long_running": False,
                    "mutating": False,
                },
                "conversion.run": {
                    "arguments_schema": {"type": "object", "properties": {}},
                    "long_running": True,
                    "mutating": False,
                },
            }
        },
    )
    tools = server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert tools
    listed = {item["name"]: item for item in tools["result"]["tools"]}
    assert listed["voxweave_model_list"]["annotations"]["readOnlyHint"] is True
    assert listed["voxweave_conversion_run"]["annotations"]["readOnlyHint"] is False
    monkeypatch.setattr(
        "voxweave.mcp_server.request_json",
        lambda *_args, **_kwargs: {"ok": True, "result": {"items": []}},
    )
    called = server.dispatch(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "voxweave_model_list", "arguments": {}},
        }
    )
    assert called and called["result"]["isError"] is False


def test_update_release_selection_is_semantic_and_does_not_download(tmp_path) -> None:
    service = UpdateService(Settings(data_root=str(tmp_path)))
    result = service._public(
        {
            "tag_name": "v99.2.0",
            "name": "VoxWeave 99.2",
            "html_url": "https://example.invalid/release",
            "published_at": "2026-01-01T00:00:00Z",
            "body": "notes",
            "prerelease": False,
            "assets": [
                {
                    "name": "VoxWeave-Windows.zip",
                    "size": 123,
                    "digest": "sha256:" + "a" * 64,
                }
            ],
        }
    )
    assert result["update_available"] is True
    assert result["download_size_bytes"] == 123


def test_update_check_explains_github_rate_limit(monkeypatch) -> None:
    headers = Message()
    headers["X-RateLimit-Remaining"] = "0"
    headers["X-RateLimit-Reset"] = "1787589000"

    def limited(*_args, **_kwargs):
        raise urllib.error.HTTPError(
            "https://api.github.com/releases",
            403,
            "rate limited",
            headers,
            None,
        )

    monkeypatch.setattr("voxweave.updater.urllib.request.urlopen", limited)
    with pytest.raises(RuntimeError, match="GitHub API rate limit exceeded"):
        UpdateService._request_json("https://api.github.com/releases")
