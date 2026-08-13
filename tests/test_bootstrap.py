from __future__ import annotations

import json

import pytest

from voxweave.bootstrap import persist_configuration
from voxweave.config import (
    Settings,
    application_root,
    load_settings,
    persist_data_root_pointer,
)
from voxweave.discovery import ServiceLock


def test_bootstrap_configuration_is_atomic_and_respects_service_ownership(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path / "data"), language="en")
    pointer = tmp_path / "source-pointer.json"
    persist_configuration(settings, pointer)
    assert json.loads(settings.config_path.read_text(encoding="utf-8"))["language"] == "en"
    assert json.loads(pointer.read_text(encoding="utf-8")) == {
        "data_root": str(tmp_path / "data")
    }
    owner = ServiceLock(settings.lock_path)
    owner.acquire()
    try:
        changed = settings.updated(language="zh-CN")
        with pytest.raises(RuntimeError, match="already owns the lock"):
            persist_configuration(changed, pointer)
    finally:
        owner.release()
    assert json.loads(settings.config_path.read_text(encoding="utf-8"))["language"] == "en"
    assert json.loads(pointer.read_text(encoding="utf-8")) == {
        "data_root": str(tmp_path / "data")
    }


def test_legacy_model_roots_are_migrated_and_removed_from_persisted_settings(
    tmp_path, monkeypatch
) -> None:
    config = tmp_path / "config" / "settings.json"
    config.parent.mkdir(parents=True)
    config.write_text(
        json.dumps({"data_root": str(tmp_path), "model_roots": ["D:/weights"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("voxweave.config.resolve_data_root", lambda: tmp_path)
    settings = load_settings()
    assert settings.weight_roots == ["D:/weights"]
    assert settings.index_roots == []
    persisted = json.loads(config.read_text(encoding="utf-8"))
    assert "model_roots" not in persisted
    assert persisted["weight_roots"] == ["D:/weights"]
    assert persisted["index_roots"] == []
    assert persisted["realtime"]["block_seconds"] == 0.5


def test_realtime_settings_are_normalized_and_written_atomically(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    assert settings.realtime["input_gate_db"] == -30.0
    realtime = {
        **settings.realtime,
        "model": "local.voice.default",
        "hostapi": "Windows WASAPI",
        "input_device": "Microphone",
        "output_device": "Speakers",
        "pitch": 8,
        "test_mode": True,
    }
    settings.update(realtime=realtime)
    persisted = json.loads(settings.config_path.read_text(encoding="utf-8"))
    assert persisted["realtime"] == realtime
    assert settings.updated(language="en").realtime == realtime
    with pytest.raises(ValueError, match="realtime.pitch"):
        settings.updated(realtime={**realtime, "pitch": 60})


def test_frozen_application_root_is_next_to_executable(tmp_path, monkeypatch) -> None:
    executable = tmp_path / "release" / "VoxWeave.exe"
    monkeypatch.setattr("voxweave.config.sys.frozen", True, raising=False)
    monkeypatch.setattr("voxweave.config.sys.executable", str(executable))
    assert application_root() == executable.parent


def test_data_root_pointer_is_written_atomically(tmp_path) -> None:
    pointer = tmp_path / "profile" / "location.json"
    data_root = tmp_path / "data"
    persist_data_root_pointer(data_root, pointer)
    assert json.loads(pointer.read_text(encoding="utf-8")) == {
        "data_root": str(data_root.resolve())
    }
    assert not pointer.with_suffix(".json.tmp").exists()
