from __future__ import annotations

import pytest

from voxweave.config import Settings
from voxweave.database import Database
from voxweave.model_catalog import ModelCatalogClient
from voxweave.model_importer import ModelImporter
from voxweave.model_inspector import ModelInspector
from voxweave.model_registry import ModelConflictError, ModelRegistry


def _arguments(model_id: str) -> dict[str, object]:
    return {
        "id": model_id,
        "model": "https://models.example/model.pth",
        "display_name": f"Model {model_id}",
        "license_spdx": "CC-BY-4.0",
    }


def _services(settings: Settings) -> tuple[ModelRegistry, ModelImporter]:
    registry = ModelRegistry(Database(settings.database_path))
    importer = ModelImporter(
        settings,
        registry,
        ModelInspector(settings),
        ModelCatalogClient(),
    )
    return registry, importer


def test_managed_model_publish_registers_the_published_file(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    _registry, importer = _services(settings)
    staging = settings.downloads_dir / "task" / "model"
    staging.mkdir(parents=True)
    (staging / "model.pth").write_bytes(b"published-model")

    result = importer._publish(
        staging,
        _arguments("managed.ready"),
        {"status": "ready", "version": "v2", "sample_rate": 40000, "f0": 1},
        has_index=False,
    )

    published = settings.managed_models_dir / "managed.ready" / "model.pth"
    assert result["status"] == "ready"
    assert result["model_path"] == str(published.resolve())
    assert published.read_bytes() == b"published-model"
    assert not staging.exists()


def test_managed_model_publish_archives_files_when_registration_conflicts(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    registry, importer = _services(settings)
    original = tmp_path / "original.pth"
    original.write_bytes(b"original-model")
    registry.register(
        original,
        model_id="managed.conflict",
        display_name="Original",
        inspection={"status": "ready"},
    )
    staging = settings.downloads_dir / "task" / "model"
    staging.mkdir(parents=True)
    (staging / "model.pth").write_bytes(b"different-model")

    with pytest.raises(ModelConflictError, match="another hash"):
        importer._publish(
            staging,
            _arguments("managed.conflict"),
            {"status": "ready"},
            has_index=False,
        )

    assert not (settings.managed_models_dir / "managed.conflict").exists()
    failures = list((settings.root / "model-import-failed").glob("managed.conflict-*"))
    assert len(failures) == 1
    assert (failures[0] / "model.pth").read_bytes() == b"different-model"
    assert registry.resolve("managed.conflict")["model_path"] == str(original.resolve())


def test_failed_url_download_archives_partial_staging(tmp_path, monkeypatch) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    registry, importer = _services(settings)

    def fail_download(
        _url,
        target,
        _expected_size,
        _expected_sha256,
        _progress,
        _cancelled,
        _progress_start,
        _progress_end,
    ):
        target.write_bytes(b"partial-download")
        raise ValueError("download hash mismatch")

    monkeypatch.setattr(importer, "_download", fail_download)
    arguments = {
        **_arguments("managed.partial"),
        "download_size_bytes": 100,
        "model_sha256": "0" * 64,
    }
    with pytest.raises(ValueError, match="hash mismatch"):
        importer.import_model(
            arguments,
            lambda _value, _stage, _detail: None,
            lambda: False,
            "download-task",
        )

    assert not (settings.downloads_dir / "model-import" / "download-task").exists()
    failures = list(
        (settings.root / "model-import-failed").glob(
            "managed.partial-download-task-*"
        )
    )
    assert len(failures) == 1
    assert (failures[0] / "model.pth").read_bytes() == b"partial-download"
    assert registry.list_models() == []
