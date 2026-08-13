from __future__ import annotations

from voxweave.config import Settings
from voxweave.runtime_verification import (
    load_runtime_verification,
    save_runtime_verification,
)


def runtime_report(settings: Settings, *, ready: bool = True) -> dict:
    return {
        "ready": ready,
        "rvc_root": settings.rvc_root,
        "rvc_python": settings.rvc_python,
        "ffmpeg": settings.ffmpeg,
        "ffprobe": settings.ffprobe,
        "hardware_backend": settings.hardware_backend,
        "doctor": {"ok": ready},
    }


def test_matching_ready_verification_is_reused(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("voxweave.config.SOURCE_ROOT", tmp_path / "app")
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    settings = Settings(
        data_root=str(tmp_path / "data"),
        rvc_root=str(tmp_path / "rvc"),
        rvc_python=str(python),
        ffmpeg=str(tmp_path / "ffmpeg.exe"),
        ffprobe=str(tmp_path / "ffprobe.exe"),
    )

    save_runtime_verification(settings, runtime_report(settings))

    cached = load_runtime_verification(settings)
    assert cached and cached["ready"] is True
    assert cached["cached"] is True
    assert cached["verified_at"]


def test_changed_runtime_configuration_does_not_reuse_verification(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("voxweave.config.SOURCE_ROOT", tmp_path / "app")
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    settings = Settings(
        data_root=str(tmp_path / "data"),
        rvc_root=str(tmp_path / "rvc"),
        rvc_python=str(python),
        ffmpeg=str(tmp_path / "ffmpeg.exe"),
        ffprobe=str(tmp_path / "ffprobe.exe"),
    )
    save_runtime_verification(settings, runtime_report(settings))

    changed = settings.updated(ffmpeg=str(tmp_path / "other-ffmpeg.exe"))

    assert load_runtime_verification(changed) is None


def test_failed_manual_verification_invalidates_previous_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("voxweave.config.SOURCE_ROOT", tmp_path / "app")
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    settings = Settings(
        data_root=str(tmp_path / "data"),
        rvc_root=str(tmp_path / "rvc"),
        rvc_python=str(python),
        ffmpeg=str(tmp_path / "ffmpeg.exe"),
        ffprobe=str(tmp_path / "ffprobe.exe"),
    )
    save_runtime_verification(settings, runtime_report(settings))

    save_runtime_verification(settings, runtime_report(settings, ready=False))

    assert load_runtime_verification(settings) is None


def test_legacy_data_root_verification_moves_to_application_directory(
    tmp_path, monkeypatch
) -> None:
    application_root = tmp_path / "app"
    generator_root = tmp_path / "generator"
    monkeypatch.setattr("voxweave.config.SOURCE_ROOT", generator_root)
    python = tmp_path / "python.exe"
    python.write_bytes(b"")
    settings = Settings(
        data_root=str(tmp_path / "old-data"),
        rvc_root=str(tmp_path / "rvc"),
        rvc_python=str(python),
        ffmpeg=str(tmp_path / "ffmpeg.exe"),
        ffprobe=str(tmp_path / "ffprobe.exe"),
    )
    save_runtime_verification(settings, runtime_report(settings))
    payload = settings.runtime_verification_path.read_text(encoding="utf-8")
    legacy = settings.state_dir / "runtime-verification.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(payload, encoding="utf-8")
    monkeypatch.setattr("voxweave.config.SOURCE_ROOT", application_root)
    local_target = settings.runtime_verification_path

    assert load_runtime_verification(settings) is not None
    assert local_target.is_file()
