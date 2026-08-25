from __future__ import annotations

import json
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import soundfile as sf

from voxweave import __version__, storage_migration_helper, update_bootstrap
from voxweave.artifacts import ArtifactStore
from voxweave.config import Settings
from voxweave.database import Database
from voxweave.media_postprocess import dereverb
from voxweave.project_repository import ProjectRepository
from voxweave.projects import ProjectService
from voxweave.realtime_calibration import RealtimeCalibrationService
from voxweave.realtime_recordings import RealtimeRecordingService
from voxweave.realtime_routing_test import RealtimeRoutingTestService
from voxweave.realtime_session_state import RealtimeSessionState
from voxweave.storage import StorageArchiveManager
from voxweave.updater import UpdateService


class _RealtimeModels:
    def verify_snapshot(self, _model: dict[str, Any]) -> None:
        return None


def test_realtime_calibration_route_recording_and_project_handoff(tmp_path: Path) -> None:
    devices = {
        "devices": [
            {"id": 1, "input_channels": 1, "output_channels": 0, "hostapi_id": 7},
            {"id": 2, "input_channels": 0, "output_channels": 2, "hostapi_id": 7},
        ]
    }
    calibration = RealtimeCalibrationService(
        lambda: devices,
        lambda _arguments: {
            "rms": 0.1,
            "peak": 0.5,
            "noise_floor_db": -48.0,
            "signal_db": -18.0,
            "snr_db": 30.0,
            "pitch_hz_min": 105.0,
            "pitch_hz_median": 132.0,
            "pitch_hz_max": 188.0,
            "device_stability": 0.995,
        },
        lambda _selector: {"recommended": {"pitch": 7}},
    )
    calibrated = calibration.calibrate(
        {
            "input_device": 1,
            "output_device": 2,
            "duration_seconds": 2.0,
            "model": "voice-a",
        }
    )
    assert calibrated["recommended_block_seconds"] == 0.25
    assert calibrated["recommended_pitch"] == 7
    assert calibrated["recommended_index_rate"] == 0.78
    assert calibrated["snr_db"] == 30.0

    route_calls: list[tuple[int, int, float]] = []
    idle_sessions = SimpleNamespace(active=lambda: None)
    idle_worker = SimpleNamespace(status=lambda: {"state": "idle"})
    engine = SimpleNamespace(
        routing_test=lambda source, target, duration: route_calls.append((source, target, duration))
        or {
            "input_device": source,
            "output_device": target,
            "captured_frames": 9600,
            "played_frames": 9600,
            "loopback_detected": True,
        }
    )
    routing = RealtimeRoutingTestService(
        idle_sessions,  # type: ignore[arg-type]
        idle_worker,  # type: ignore[arg-type]
        engine,  # type: ignore[arg-type]
        threading.RLock(),
        threading.RLock(),
        lambda: False,
    )
    routed = routing.run({"input_device": 1, "output_device": 2, "duration_seconds": 0.2})
    assert routed["loopback_detected"] is True
    assert route_calls == [(1, 2, 0.2)]

    database = Database(tmp_path / "data" / "state" / "voxweave.sqlite3")
    database.execute(
        "INSERT INTO models(id,display_name,aliases_json,family,model_path,model_sha256,"
        "index_candidates_json,source_kind,recommended_json,status,imported_at,archived) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
        (
            "voice-a",
            "Voice A",
            "[]",
            "voice-a",
            str(tmp_path / "voice-a.pth"),
            "a" * 64,
            "[]",
            "external",
            "{}",
            "ready",
            "2026-08-25T00:00:00+00:00",
        ),
    )
    sessions = RealtimeSessionState(database, _RealtimeModels())  # type: ignore[arg-type]
    dry = tmp_path / "capture-dry.wav"
    wet = tmp_path / "capture-wet.wav"
    dry.write_bytes(b"dry recording")
    wet.write_bytes(b"wet recording")
    session_id = "session-recorded"
    sessions.begin(
        session_id,
        {
            "id": "voice-a",
            "model_sha256": "a" * 64,
            "index_sha256": None,
        },
        {
            "pitch": 7,
            "f0": "rmvpe",
            "index_rate": 0.78,
            "rms_mix_rate": 0.25,
        },
    )
    assert sessions.mark_stopping() == session_id
    ended = sessions.handle_worker_event(
        {
            "ok": True,
            "event": "stopped",
            "session_id": session_id,
            "recording_dry_path": str(dry),
            "recording_wet_path": str(wet),
            "callbacks": 10,
            "xruns": 0,
        }
    )
    assert ended.session_ended is True
    stopped = sessions.get(session_id)
    assert stopped["state"] == "stopped"
    manifest = Path(stopped["metrics"]["recording_manifest_path"])
    assert manifest.is_file()

    projects = ProjectService(
        ProjectRepository(database),
        SimpleNamespace(),  # type: ignore[arg-type]
        _RealtimeModels(),  # type: ignore[arg-type]
    )
    promoted = RealtimeRecordingService(sessions, projects).promote(
        {"session_id": session_id, "project_name": "Recorded take"}
    )
    assert promoted["project"]["input_path"] == str(dry.resolve())
    assert promoted["project"]["document"]["default_model"] == "voice-a"
    assert (
        json.loads(manifest.read_text(encoding="utf-8"))["promoted_project_id"]
        == promoted["project"]["id"]
    )
    database.close()


def test_dereverb_writes_verified_audio_and_reduces_late_decay(tmp_path: Path) -> None:
    sample_rate = 16000
    samples = np.zeros(sample_rate, dtype=np.float32)
    samples[800] = 0.8
    tail = 0.35 * np.exp(-np.arange(6000, dtype=np.float32) / 1600.0)
    samples[801 : 801 + len(tail)] += tail
    source = tmp_path / "reverberant.wav"
    output = tmp_path / "dereverbed.wav"
    sf.write(source, samples, sample_rate, subtype="PCM_24")

    result = dereverb(source, output, 0.8)
    processed, processed_rate = sf.read(output, dtype="float32")

    assert result["algorithm"] == "late-spectral-decay-suppression-v1"
    assert result["sha256"]
    assert result["channels"] == 1
    assert processed_rate == sample_rate
    assert len(processed) == len(samples)
    assert not np.allclose(processed, samples, atol=1e-5)
    assert float(np.mean(np.square(processed[2500:6500]))) < float(
        np.mean(np.square(samples[2500:6500]))
    )


def test_storage_migration_copies_verified_data_without_stale_service_state(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings = Settings(data_root=str(tmp_path / "source-data"))
    settings.ensure_layout()
    database = Database(settings.database_path)
    artifacts = ArtifactStore(database)
    storage = StorageArchiveManager(settings, database, artifacts)
    settings.config_path.write_text('{"language":"zh-CN"}\n', encoding="utf-8")
    settings.discovery_path.write_text('{"pid":999999}\n', encoding="utf-8")
    target = tmp_path / "target-data"
    plan = storage.migration_plan({"target_root": str(target)})
    prepared = storage.prepare_migration(
        {"target_root": str(target), "plan_digest": plan["plan_digest"]}
    )
    manifest_path = Path(prepared["manifest_path"])
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pointer = tmp_path / "location.json"
    manifest["pointer_path"] = str(pointer)
    manifest["application_command"] = ["VoxWeave.exe"]
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    database.close()
    transient = settings.state_dir / "synthetic-cache.sqlite3-wal"
    transient.write_bytes(b"stale wal")
    started: list[list[str]] = []
    monkeypatch.setattr(
        storage_migration_helper,
        "start_managed_process",
        lambda command: started.append(list(command)),
    )

    assert storage_migration_helper.run_migration(str(manifest_path)) == 0
    assert (target / "config" / "settings.json").read_text(
        encoding="utf-8"
    ) == '{"language":"zh-CN"}\n'
    assert not (target / "state" / "service.json").exists()
    assert not (target / "state" / "service.lock").exists()
    assert not (target / "state" / transient.name).exists()
    assert json.loads(pointer.read_text(encoding="utf-8"))["data_root"] == str(target.resolve())
    migrated = Database(target / "state" / "voxweave.sqlite3")
    record = migrated.fetch_one(
        "SELECT state FROM storage_migrations WHERE id=?",
        (prepared["migration_id"],),
    )
    assert record == {"state": "completed"}
    migrated.close()
    assert started == [["VoxWeave.exe"]]


def test_side_by_side_update_rolls_back_when_health_check_fails(
    tmp_path: Path, monkeypatch: Any
) -> None:
    settings = Settings(data_root=str(tmp_path / "data"))
    settings.ensure_layout()
    version = "99.0.0"
    update_dir = settings.downloads_dir / "updates" / version
    update_dir.mkdir(parents=True)
    archive = update_dir / "VoxWeave-Windows.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("VoxWeave/VoxWeave.exe", b"MZ synthetic executable")
        package.writestr("VoxWeave/NOTICE.txt", b"synthetic test release")
    service = UpdateService(settings)
    installed = service.install(
        {"version": version},
        lambda _fraction, _stage, _detail: None,
        lambda: False,
        "install-test",
    )
    executable = Path(installed["executable_path"])
    assert executable.is_file()
    assert executable.parent != Path(__import__("sys").executable).resolve().parent
    pending = service.activate({"version": version})
    assert pending["state"] == "pending"
    state = json.loads(service.state_path.read_text(encoding="utf-8"))
    token = state["pending"]["token"]
    launched: list[list[str]] = []
    terminated: list[bool] = []

    class _Process:
        def poll(self) -> None:
            return None

    monkeypatch.setattr(
        update_bootstrap,
        "start_managed_process",
        lambda command: launched.append(list(command)) or _Process(),
    )
    monkeypatch.setattr(
        update_bootstrap,
        "terminate_process_tree",
        lambda _process: terminated.append(True),
    )
    clock = iter((0.0, 46.0))
    monkeypatch.setattr(update_bootstrap.time, "monotonic", lambda: next(clock))

    assert update_bootstrap.run_update_bootstrap(str(service.state_path), version, token) == 1
    rolled_back = json.loads(service.state_path.read_text(encoding="utf-8"))
    assert rolled_back["active_version"] == __version__
    assert rolled_back["installations"][version]["state"] == "failed"
    assert "pending" not in rolled_back
    assert executable.is_file()
    assert terminated == [True]
    assert launched[0][0] == str(executable)
    assert launched[-1][0] == rolled_back["active_executable"]

    pending_again = service.activate({"version": version})
    second_state = json.loads(service.state_path.read_text(encoding="utf-8"))
    second_token = second_state["pending"]["token"]
    health = settings.state_dir / "update-health" / f"{second_token}.json"
    health.parent.mkdir(parents=True, exist_ok=True)
    health.write_text(json.dumps({"token": second_token}) + "\n", encoding="utf-8")
    monkeypatch.setattr(update_bootstrap.time, "monotonic", lambda: 0.0)
    assert pending_again["state"] == "pending"
    assert (
        update_bootstrap.run_update_bootstrap(str(service.state_path), version, second_token) == 0
    )
    activated = json.loads(service.state_path.read_text(encoding="utf-8"))
    assert activated["active_version"] == version
    assert activated["installations"][version]["state"] == "active"
    assert activated["installations"][__version__]["state"] == "installed"
    rollback = service.rollback({"version": __version__})
    assert rollback["state"] == "pending"
    assert rollback["previous_version"] == version
