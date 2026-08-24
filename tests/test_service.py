from __future__ import annotations

import json
import logging
import shutil
import time

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from voxweave.config import Settings
from voxweave.controller import Controller
from voxweave.protocol import OPERATION_SPECS
from voxweave.service import create_app
from voxweave.settings_file_store import SettingsFileStore


def test_high_frequency_status_successes_do_not_fill_info_log(tmp_path, caplog) -> None:
    settings = Settings(data_root=str(tmp_path))
    caplog.set_level(logging.INFO, logger="voxweave.service")
    with TestClient(create_app(settings, token="secret")) as client:
        for index in range(20):
            response = client.post(
                "/v1/execute",
                headers={"Authorization": "Bearer secret"},
                json={
                    "protocol": "voxweave-control",
                    "version": 1,
                    "request_id": f"quiet-status-{index}",
                    "operation": "realtime.status",
                    "arguments": {},
                },
            ).json()
            assert response["ok"] is True

    assert not [
        record
        for record in caplog.records
        if record.name == "voxweave.service"
        and record.levelno >= logging.INFO
        and getattr(record, "operation", None) == "realtime.status"
    ]


def test_health_describe_and_stable_error(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        assert client.get("/v1/health").json()["ok"] is True
        headers = {"Authorization": "Bearer secret"}
        handshake = client.get("/v1/handshake", headers=headers).json()
        assert handshake["protocol"] == "voxweave-control"
        assert handshake["version"] == 1
        description = client.get("/v1/describe", headers=headers).json()
        assert description["protocol"] == "voxweave-control"
        response = client.post(
            "/v1/execute",
            headers=headers,
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "operation": "conversion.run",
                "arguments": {"input": "relative.wav"},
            },
        ).json()
        assert response["ok"] is False
        assert response["error_type"] == "invalid_arguments"


def test_token_is_required(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        assert client.get("/v1/describe").status_code == 401
        assert client.get("/v1/handshake").status_code == 401


def test_every_described_long_operation_has_a_registered_handler(tmp_path) -> None:
    controller = Controller(Settings(data_root=str(tmp_path)))
    try:
        expected = {
            operation
            for operation, contract in OPERATION_SPECS.items()
            if contract.long_running
        }
        assert set(controller.tasks.handlers) == expected
    finally:
        controller.shutdown()


def test_service_owned_settings_update_preserves_runtime_fields(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    SettingsFileStore(settings).commit(
        settings,
        rvc_root=str(tmp_path / "runtime"),
        rvc_python=str(tmp_path / "runtime" / "python.exe"),
    )
    with TestClient(create_app(settings, token="secret")) as client:
        realtime = {
            "model": "local.voice.default",
            "hostapi": "Windows WASAPI",
            "input_device": "Microphone",
            "output_device": "Speakers",
            "pitch": 9,
            "f0": "rmvpe",
            "index_rate": 0.72,
            "rms_mix_rate": 0.25,
            "vad_threshold": 0.35,
            "input_gate_db": -40.0,
            "block_seconds": 0.5,
            "test_mode": True,
        }
        response = client.post(
            "/v1/execute",
            headers={"Authorization": "Bearer secret"},
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "request_id": "settings-update-profile",
                "operation": "settings.update",
                "arguments": {
                    "expected_revision": settings.revision,
                    "language": "en",
                    "realtime": realtime,
                },
            },
        ).json()
    assert response["ok"] is True
    payload = json.loads(settings.config_path.read_text(encoding="utf-8"))
    assert payload["language"] == "en"
    assert payload["rvc_root"] == str(tmp_path / "runtime")
    assert payload["rvc_python"] == str(tmp_path / "runtime" / "python.exe")
    assert payload["realtime"] == realtime
    assert response["result"]["settings"]["realtime"] == realtime


def test_settings_updates_are_revisioned_replayable_and_evented_once(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    headers = {"Authorization": "Bearer secret"}

    with TestClient(create_app(settings, token="secret")) as client:

        def command(
            operation: str, arguments: dict, request_id: str | None = None
        ) -> dict:
            payload = {
                "protocol": "voxweave-control",
                "version": 1,
                "operation": operation,
                "arguments": arguments,
            }
            if request_id:
                payload["request_id"] = request_id
            return client.post("/v1/execute", headers=headers, json=payload).json()

        initial = command("settings.get", {})["result"]
        update_arguments = {
            "expected_revision": initial["revision"],
            "language": "en",
            "realtime": {"pitch": 5},
        }
        first = command("settings.update", update_arguments, "settings-once")
        replayed = command("settings.update", update_arguments, "settings-once")
        stale = command(
            "settings.update",
            {"expected_revision": initial["revision"], "realtime": {"pitch": 8}},
            "settings-stale",
        )
        conflict = command(
            "settings.update",
            {**update_arguments, "language": "zh-CN"},
            "settings-once",
        )
        events = command(
            "settings.events", {"after_revision": initial["revision"], "limit": 10}
        )["result"]

    assert first["ok"] is True
    assert replayed == first
    assert first["result"]["revision"] == initial["revision"] + 1
    assert first["result"]["settings"]["language"] == "en"
    assert first["result"]["settings"]["realtime"]["pitch"] == 5
    assert first["result"]["settings"]["realtime"]["input_gate_db"] == -40.0
    assert stale["error_type"] == "revision_conflict"
    assert conflict["error_type"] == "idempotency_conflict"
    assert events["current_revision"] == first["result"]["revision"]
    assert len(events["events"]) == 1
    assert set(events["events"][0]["changed_fields"]) == {"language", "realtime"}


def test_long_request_replay_happens_before_input_snapshot_preparation(tmp_path) -> None:
    source = tmp_path / "changing.wav"
    source.write_bytes(b"first-version")
    settings = Settings(data_root=str(tmp_path / "data"))
    headers = {"Authorization": "Bearer secret"}
    command = {
        "protocol": "voxweave-control",
        "version": 1,
        "request_id": "inspect-before-preparer",
        "operation": "media.inspect",
        "arguments": {"input": str(source)},
    }
    with TestClient(create_app(settings, token="secret")) as client:
        first = client.post("/v1/execute", headers=headers, json=command).json()
        source.write_bytes(b"second-version-with-different-metadata")
        replayed = client.post("/v1/execute", headers=headers, json=command).json()

    assert first["ok"] is True
    assert replayed["ok"] is True
    assert replayed["result"]["id"] == first["result"]["id"]
    assert replayed["result"]["snapshot"] == first["result"]["snapshot"]


def test_diagnostics_snapshot_comes_from_service_state(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        response = client.post(
            "/v1/execute",
            headers={"Authorization": "Bearer secret"},
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "request_id": "diagnostics-snapshot",
                "operation": "diagnostics.snapshot",
                "arguments": {},
            },
        ).json()
        task_id = response["result"]["id"]
        deadline = time.monotonic() + 5
        task = None
        while time.monotonic() < deadline:
            task_response = client.post(
                "/v1/execute",
                headers={"Authorization": "Bearer secret"},
                json={
                    "protocol": "voxweave-control",
                    "version": 1,
                    "operation": "task.get",
                    "arguments": {"task_id": task_id},
                },
            ).json()
            task = task_response["result"]
            if task["state"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
    assert response["ok"] is True
    assert task and task["state"] == "completed"
    result = task["result"]
    assert result["protocol"] == "voxweave-diagnostics"
    assert result["settings"] == settings.payload()
    assert set(result["storage"]) == {
        "artifacts",
        "downloads",
        "cache",
        "runtime",
        "pip_cache",
        "managed_models",
    }
    assert isinstance(result["events"], list)
    assert result["realtime"]["state"] == "idle"


def test_realtime_status_is_available_without_starting_audio(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        response = client.post(
            "/v1/execute",
            headers={"Authorization": "Bearer secret"},
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "operation": "realtime.status",
                "arguments": {},
            },
        ).json()
    assert response["ok"] is True
    assert response["result"] == {
        "session_id": None,
        "state": "idle",
        "stage": "idle",
        "metrics": {},
        "worker": {
            "state": "not_started",
            "pid": None,
            "model_id": None,
            "model_ready": False,
        },
    }


def test_authenticated_task_websocket_streams_real_events(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        headers = {"Authorization": "Bearer secret"}
        submitted = client.post(
            "/v1/execute",
            headers=headers,
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "request_id": "import-missing-model",
                "operation": "model.import",
                "arguments": {"model": str(tmp_path / "missing.pth")},
            },
        ).json()
        task_id = submitted["result"]["id"]
        with client.websocket_connect(f"/v1/events?token=secret&task_id={task_id}") as socket:
            event = socket.receive_json()
        assert event["task_id"] == task_id
        assert event["state"] == "queued"


def test_authenticated_global_websocket_streams_all_task_events(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        headers = {"Authorization": "Bearer secret"}
        submitted = client.post(
            "/v1/execute",
            headers=headers,
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "request_id": "global-import-missing-model",
                "operation": "model.import",
                "arguments": {"model": str(tmp_path / "missing.pth")},
            },
        ).json()
        with client.websocket_connect("/v1/events?token=secret") as socket:
            event = socket.receive_json()
        assert event["task_id"] == submitted["result"]["id"]
        assert event["state"] == "queued"


def test_long_command_request_id_is_idempotent_and_conflicts_are_explicit(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    with TestClient(create_app(settings, token="secret")) as client:
        headers = {"Authorization": "Bearer secret"}
        command = {
            "protocol": "voxweave-control",
            "version": 1,
            "request_id": "same-import",
            "operation": "model.import",
            "arguments": {"model": str(tmp_path / "missing.pth")},
        }
        first = client.post("/v1/execute", headers=headers, json=command).json()
        repeated = client.post("/v1/execute", headers=headers, json=command).json()
        assert repeated["result"]["id"] == first["result"]["id"]
        conflicting = client.post(
            "/v1/execute",
            headers=headers,
            json={
                **command,
                "arguments": {"model": str(tmp_path / "different-missing.pth")},
            },
        ).json()
        assert conflicting["ok"] is False
        assert conflicting["error_type"] == "idempotency_conflict"


def test_real_media_inspection_flows_from_service_task_to_visible_result(tmp_path) -> None:
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        return
    source = tmp_path / "tone.wav"
    sf.write(source, np.zeros(8000, dtype=np.float32), 8000)
    settings = Settings(
        data_root=str(tmp_path / "data"), ffprobe=ffprobe, ffmpeg=ffmpeg
    )
    with TestClient(create_app(settings, token="secret")) as client:
        headers = {"Authorization": "Bearer secret"}
        submitted = client.post(
            "/v1/execute",
            headers=headers,
            json={
                "protocol": "voxweave-control",
                "version": 1,
                "request_id": "inspect-real-wave",
                "operation": "media.inspect",
                "arguments": {"input": str(source)},
            },
        ).json()["result"]
        deadline = time.monotonic() + 5
        task = submitted
        while time.monotonic() < deadline and task["state"] not in {"completed", "failed"}:
            task = client.post(
                "/v1/execute",
                headers=headers,
                json={
                    "protocol": "voxweave-control",
                    "version": 1,
                    "operation": "task.get",
                    "arguments": {"task_id": submitted["id"]},
                },
            ).json()["result"]
            time.sleep(0.02)
    assert task["state"] == "completed", task.get("error")
    assert task["result"]["path"] == str(source.resolve())
    assert task["result"]["media_type"] == "audio"
    assert len(task["result"]["sha256"]) == 64
    assert task["artifacts"] == []
