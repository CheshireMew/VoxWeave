from __future__ import annotations

import hashlib

import pytest

from voxweave.config import Settings
from voxweave.database import Database
from voxweave.hashing import verify_file
from voxweave.model_catalog import ModelCatalogClient
from voxweave.model_importer import ModelImporter
from voxweave.model_inspector import ModelInspector
from voxweave.model_registry import ModelConflictError, ModelRegistry
from voxweave.protocol import OperationError


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


def test_model_registration_can_be_archived_without_deleting_model_files(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    registry, _importer = _services(settings)
    model_path = tmp_path / "voice.pth"
    model_path.write_bytes(b"model")
    registered = registry.register(
        model_path,
        model_id="local.voice",
        display_name="Voice",
        inspection={"status": "ready"},
    )

    archived = registry.set_archived(registered["id"], True)

    assert archived["archived"] is True
    assert model_path.is_file()
    with pytest.raises(OperationError, match="archived"):
        registry.resolve_for_execution(registered["id"])
    assert registry.set_archived(registered["id"], False)["archived"] is False
    assert registry.resolve_for_execution(registered["id"])["id"] == registered["id"]


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

    def fail_download(_spec, target, **_kwargs):
        target.write_bytes(b"partial-download")
        raise ValueError("download hash mismatch")

    monkeypatch.setattr("voxweave.model_importer.download_verified", fail_download)
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
    failures = list((settings.root / "model-import-failed").glob("managed.partial-download-task-*"))
    assert len(failures) == 1
    assert (failures[0] / "model.pth").read_bytes() == b"partial-download"
    assert registry.list_models() == []


def test_catalog_state_reports_a_registered_model_with_missing_files_as_repairable(
    tmp_path,
) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    registry, _importer = _services(settings)
    model_path = tmp_path / "catalog-model.pth"
    model_path.write_bytes(b"catalog")
    registry.register(
        model_path,
        model_id="catalog.missing",
        display_name="Catalog missing",
        source_kind="catalog",
        inspection={"status": "ready"},
    )
    model_path.replace(tmp_path / "archived-catalog-model.pth")

    state = registry.catalog_state(
        {
            "id": "catalog.missing",
            "model_sha256": hashlib.sha256(b"catalog").hexdigest(),
        }
    )

    assert state == {
        "registered": True,
        "installed": False,
        "available": False,
        "archived": False,
        "status": "missing",
        "repairable": True,
    }


def test_catalog_repair_archives_the_old_install_and_registers_verified_replacement(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    registry, importer = _services(settings)
    target = settings.managed_models_dir / "catalog.repair"
    target.mkdir(parents=True)
    old_bytes = b"old-catalog-model"
    new_bytes = b"new-catalog-model"
    (target / "model.pth").write_bytes(old_bytes)
    registry.register(
        target / "model.pth",
        model_id="catalog.repair",
        display_name="Catalog repair",
        source_kind="catalog",
        inspection={"status": "ready"},
    )

    def fake_download(spec, download_target, **_kwargs):
        download_target.write_bytes(new_bytes)
        return verify_file(download_target, expected_sha256=spec.sha256)

    monkeypatch.setattr("voxweave.model_importer.download_verified", fake_download)
    monkeypatch.setattr(
        importer.inspector,
        "inspect",
        lambda _path: {"status": "ready", "version": "v2", "sample_rate": 40000},
    )
    new_hash = hashlib.sha256(new_bytes).hexdigest()

    result = importer.import_model(
        {
            "id": "catalog.repair",
            "model": "https://models.example/replacement.pth",
            "display_name": "Catalog repair",
            "license_spdx": "CC-BY-4.0",
            "source_url": "https://models.example/catalog.repair",
            "download_size_bytes": len(new_bytes),
            "model_sha256": new_hash,
            "source_kind": "catalog",
        },
        lambda *_args: None,
        lambda: False,
        "repair-task",
    )

    assert (target / "model.pth").read_bytes() == new_bytes
    assert result["model_sha256"] == new_hash
    assert result["source_kind"] == "catalog"
    archived = list((settings.root / "model-import-failed").glob("catalog.repair-repair-*"))
    assert len(archived) == 1
    assert (archived[0] / "model.pth").read_bytes() == old_bytes
