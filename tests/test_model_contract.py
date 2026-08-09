from __future__ import annotations

from pathlib import Path

import pytest

from voxweave.config import load_settings
from voxweave.database import Database
from voxweave.model_registry import ModelRegistry


def test_current_machine_models_are_distinct() -> None:
    settings = load_settings(create=False)
    if not settings.rvc_root or not Path(settings.rvc_root).is_dir():
        pytest.skip("current-machine RVC runtime is not configured")
    registry = ModelRegistry(Database(settings.database_path), settings)
    models = registry.list_models()
    if len(models) < 13:
        pytest.skip("current-machine model scan has not been run")
    selected = [
        registry.resolve("公开御姐"),
        registry.resolve("Keruan"),
        registry.resolve("Guaiguai"),
    ]
    assert len({item["id"] for item in selected}) == 3
    assert len({item["model_sha256"] for item in selected}) == 3
    assert all(item["status"] == "ready" for item in selected)
