from __future__ import annotations

import json

import pytest

from voxweave.bootstrap import persist_configuration
from voxweave.config import Settings, load_settings
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
