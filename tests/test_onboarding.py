from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from voxweave.onboarding import (
    MINIMUM_INITIAL_FREE_BYTES,
    choose_automatic_data_root,
    discover_existing_data_root,
    discover_existing_runtime,
    discover_runtime_for_data_root,
    plan_initial_setup,
)


def _write_settings(root: Path, **values: object) -> None:
    path = root / "config" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"data_root": str(root), **values}),
        encoding="utf-8",
    )


def _fake_rvc(root: Path) -> Path:
    for relative in (
        "configs/config.py",
        "infer/vc/modules.py",
        "assets/hubert_base/pytorch_model.bin",
        "assets/rmvpe/rmvpe.pt",
        ".venv/Scripts/python.exe",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    return root


def test_existing_voxweave_data_is_reused_before_creating_another_root(tmp_path) -> None:
    drive = tmp_path / "drive"
    existing = drive / "Tools" / "VoxWeave"
    _write_settings(existing)
    (existing / "state").mkdir()
    (existing / "state" / "voxweave.sqlite3").write_bytes(b"database")

    setup = plan_initial_setup(
        application_root=tmp_path / "app",
        drive_roots=[drive],
    )

    assert setup.data_root == existing.resolve()
    assert setup.reused_existing_data is True
    assert setup.reason == "existing_data"


def test_pointer_target_beats_a_less_complete_standard_location(tmp_path) -> None:
    drive = tmp_path / "drive"
    standard = drive / "VoxWeave"
    pointed = tmp_path / "custom-data"
    _write_settings(standard)
    _write_settings(pointed)
    (pointed / "state").mkdir()
    (pointed / "state" / "voxweave.sqlite3").write_bytes(b"database")
    pointer = tmp_path / "location.json"
    pointer.write_text(json.dumps({"data_root": str(pointed)}), encoding="utf-8")

    assert discover_existing_data_root([drive], pointer_paths=(pointer,)) == pointed.resolve()


def test_new_data_root_prefers_non_system_drive_with_most_space(tmp_path, monkeypatch) -> None:
    system = tmp_path / "system"
    data = tmp_path / "data"
    system.mkdir()
    data.mkdir()
    free = {system: 100 * 1024**3, data: 50 * 1024**3}
    monkeypatch.setattr(
        "voxweave.onboarding.shutil.disk_usage",
        lambda path: SimpleNamespace(free=free[path]),
    )
    monkeypatch.setattr("voxweave.onboarding._is_system_drive", lambda path: path == system)

    selected = choose_automatic_data_root([system, data])

    assert selected == (data / "VoxWeave").resolve()


def test_automatic_data_root_requires_safe_free_space(tmp_path, monkeypatch) -> None:
    drive = tmp_path / "drive"
    drive.mkdir()
    monkeypatch.setattr(
        "voxweave.onboarding.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=MINIMUM_INITIAL_FREE_BYTES - 1),
    )
    assert choose_automatic_data_root([drive]) is None


def test_existing_rvc_environment_and_ffmpeg_are_detected(tmp_path, monkeypatch) -> None:
    drive = tmp_path / "drive"
    rvc = _fake_rvc(drive / "Tools" / "RVC")
    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")
    monkeypatch.setattr(
        "voxweave.onboarding._system_ffmpeg_pair",
        lambda: (ffmpeg, ffprobe),
    )

    runtime = discover_existing_runtime([drive], application_root=tmp_path / "app")

    assert runtime is not None
    assert runtime.rvc_root == rvc.resolve()
    assert runtime.rvc_python == (rvc / ".venv" / "Scripts" / "python.exe").resolve()
    assert runtime.ffmpeg == ffmpeg
    assert runtime.ffprobe == ffprobe


def test_existing_data_reuses_its_configured_runtime(tmp_path) -> None:
    drive = tmp_path / "drive"
    existing = drive / "VoxWeave"
    rvc = _fake_rvc(tmp_path / "custom-rvc")
    _write_settings(
        existing,
        rvc_root=str(rvc),
        rvc_python=str(rvc / ".venv" / "Scripts" / "python.exe"),
    )

    setup = plan_initial_setup(
        application_root=tmp_path / "app",
        drive_roots=[drive],
    )

    assert setup.reused_existing_data is True
    assert setup.runtime is not None
    assert setup.runtime.rvc_root == rvc.resolve()


def test_empty_configured_data_root_discovers_existing_machine_runtime(tmp_path) -> None:
    drive = tmp_path / "drive"
    data_root = tmp_path / "selected-data"
    rvc = _fake_rvc(drive / "Tools" / "RVC")

    runtime = discover_runtime_for_data_root(
        data_root,
        application_root=tmp_path / "app",
        drive_roots=[drive],
    )

    assert runtime is not None
    assert runtime.rvc_root == rvc.resolve()
