from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from voxweave.conversion_runner import ConversionRunner
from voxweave.database import Database, utc_now
from voxweave.media_errors import MediaPipelineError
from voxweave.model_registry import ModelRegistry
from voxweave.model_repository import ModelRepository
from voxweave.preset_repository import PresetRepository
from voxweave.presets import PresetService
from voxweave.protocol import OperationError, parse_arguments
from voxweave.result_versions import ResultVersionRepository


class FakeModels:
    def __init__(self) -> None:
        self.model = {
            "id": "voice-a",
            "model_sha256": "a" * 64,
            "status": "ready",
        }

    def resolve(self, selector: str) -> dict[str, Any]:
        if selector != self.model["id"]:
            raise LookupError(selector)
        return dict(self.model)

    def list_models(self) -> list[dict[str, Any]]:
        return [dict(self.model)]


def insert_model(database: Database, model_path: Path) -> None:
    database.execute(
        "INSERT INTO models("
        "id,display_name,aliases_json,family,model_path,model_sha256,"
        "index_candidates_json,source_kind,recommended_json,status,imported_at,archived) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,0)",
        (
            "voice-a",
            "Voice A",
            "[]",
            "voice-a",
            str(model_path),
            "a" * 64,
            "[]",
            "external",
            "{}",
            "ready",
            utc_now(),
        ),
    )


def test_preset_lifecycle_and_bundle_round_trip(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    model_path = tmp_path / "voice.pth"
    model_path.write_bytes(b"model")
    insert_model(database, model_path)
    service = PresetService(FakeModels(), PresetRepository(database))  # type: ignore[arg-type]
    created = service.save(
        {
            "model": "voice-a",
            "name": "Narration",
            "kind": "conversion",
            "parameters": {"pitch": 2, "f0": "rmvpe"},
        }
    )
    assert created["revision"] == 1
    updated = service.update(
        {
            "preset_id": created["id"],
            "expected_revision": 1,
            "name": "Narration warm",
            "parameters": {"pitch": 3, "f0": "rmvpe"},
        }
    )
    assert updated["revision"] == 2
    copied = service.copy({"preset_id": updated["id"], "name": "Narration copy"})
    assert copied["id"] != updated["id"]
    assert copied["parameters"] == updated["parameters"]
    assert copied["revision"] == 1
    archived = service.archive(
        {"preset_id": created["id"], "expected_revision": 2, "archived": True}
    )
    assert archived["archived"] is True
    visible = service.list({"model": "voice-a"})
    assert [item["id"] for item in visible] == [copied["id"]]
    bundle = service.export({"preset_ids": [created["id"]]})
    restored = service.import_bundle(bundle)
    assert restored[0]["archived"] is False
    assert restored[0]["parameters"]["pitch"] == 3
    database.close()


def test_imported_preset_requires_confirmation_until_updated_for_current_model(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "state.sqlite3")
    model_path = tmp_path / "voice.pth"
    model_path.write_bytes(b"model")
    insert_model(database, model_path)
    service = PresetService(FakeModels(), PresetRepository(database))  # type: ignore[arg-type]
    imported = service.import_bundle(
        {
            "protocol": "voxweave-preset-bundle",
            "version": 1,
            "presets": [
                {
                    "model_id": "voice-a",
                    "model_sha256": "b" * 64,
                    "name": "Older model",
                    "kind": "conversion",
                    "parameters": {"pitch": 2, "f0": "rmvpe"},
                }
            ],
        }
    )[0]
    assert imported["needs_reconfirmation"] is True

    updated = service.update(
        {
            "preset_id": imported["id"],
            "expected_revision": imported["revision"],
            "parameters": imported["parameters"],
        }
    )
    assert updated["model_sha256"] == "a" * 64
    assert service.list({"model": "voice-a"})[0]["needs_reconfirmation"] is False
    database.close()


def test_model_metadata_is_revisioned_and_kept_outside_model_registration(
    tmp_path: Path,
) -> None:
    model_path = tmp_path / "voice.pth"
    model_path.write_bytes(b"model")
    database = Database(tmp_path / "state.sqlite3")
    insert_model(database, model_path)
    repository = ModelRepository(database)
    first = repository.update_metadata(
        "voice-a",
        0,
        {"custom_name": "Hero", "tags": ["game", "male"], "favorite": True},
    )
    assert first["revision"] == 1
    assert first["tags"] == ["game", "male"]
    with pytest.raises(OperationError, match="revision changed"):
        repository.update_metadata("voice-a", 0, {"notes": "stale"})
    stored_model = repository.get("voice-a")
    assert "custom_name" not in stored_model
    database.close()


def test_model_library_tracks_cover_usage_duplicates_and_integrity(tmp_path: Path) -> None:
    model_path = tmp_path / "shared.pth"
    cover_path = tmp_path / "cover.png"
    model_path.write_bytes(b"shared model revision")
    cover_path.write_bytes(b"synthetic image")
    database = Database(tmp_path / "state.sqlite3")
    models = ModelRegistry(database)
    models.register(
        model_path,
        model_id="voice-a",
        display_name="Voice A",
        inspection={"status": "ready"},
    )
    models.register(
        model_path,
        model_id="voice-b",
        display_name="Voice B",
        inspection={"status": "ready"},
    )
    updated = models.update_metadata(
        {
            "model_id": "voice-a",
            "expected_revision": 0,
            "cover_path": str(cover_path),
            "favorite": True,
        }
    )
    assert updated["cover_path"] == str(cover_path)
    used = models.resolve_for_execution("voice-a")
    assert used["usage_count"] == 1
    assert used["last_used_at"]
    verified = models.verify_integrity({"model_id": "voice-a"}, lambda *_args: None, lambda: False)
    assert verified["integrity_status"] == "verified"
    library = {model["id"]: model for model in models.list_models()}
    assert library["voice-a"]["duplicate_model_ids"] == ["voice-b"]
    assert library["voice-b"]["duplicate_model_ids"] == ["voice-a"]
    database.close()


def test_result_versions_keep_complete_reproducible_result(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    now = utc_now()
    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,"
        "snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("task-1", "conversion.run", "{}", "completed", 1.0, "completed", "{}", now, now),
    )
    repository = ResultVersionRepository(database)
    input_path = tmp_path / "in.wav"
    input_path.write_bytes(b"source")
    result = {
        "input": {"path": str(input_path), "sha256": "a" * 64},
        "output": {"path": str(tmp_path / "out.wav"), "sha256": "b" * 64},
        "model": {
            "id": "voice-a",
            "model_sha256": "c" * 64,
            "index_sha256": None,
        },
        "parameters": {"pitch": 2},
    }
    rerun_arguments = {
        "input": str(input_path),
        "input_sha256": "a" * 64,
        "output": str(tmp_path / "out.wav"),
        "model": "voice-a",
        "pitch": 2,
    }
    version = repository.record("task-1", result, rerun_arguments)
    updated = repository.update({"version_id": version["id"], "label": "Best", "favorite": True})
    assert updated["result"] == result
    assert updated["favorite"] is True
    assert repository.list({"favorites_only": True, "limit": 10})["items"][0]["label"] == "Best"

    database.execute(
        "INSERT INTO tasks(id,operation,arguments_json,state,progress,stage,"
        "snapshot_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("task-2", "conversion.run", "{}", "completed", 1.0, "completed", "{}", now, now),
    )
    changed = {
        **result,
        "output": {"path": str(tmp_path / "out-v2.wav"), "sha256": "d" * 64},
        "parameters": {"pitch": 5},
    }
    child = repository.record(
        "task-2",
        changed,
        {**rerun_arguments, "pitch": 5, "parent_version_id": version["id"]},
    )
    assert child["parent_id"] == version["id"]
    assert child["root_id"] == version["id"]
    assert child["generation"] == 2
    assert child["differences"]["parameters"] == {
        "before": {"pitch": 2},
        "after": {"pitch": 5},
    }
    assert repository.get(version["id"])["children"] == [child["id"]]

    class _ExactModels:
        model_hash = "c" * 64

        def resolve(self, selector: str) -> dict[str, Any]:
            assert selector == "voice-a"
            return {
                "id": selector,
                "status": "ready",
                "archived": False,
                "model_sha256": self.model_hash,
                "index_sha256": None,
            }

        def verify_snapshot(self, _model: dict[str, Any]) -> None:
            return None

    runner = ConversionRunner.__new__(ConversionRunner)
    runner.results = repository
    runner.models = _ExactModels()  # type: ignore[assignment]
    captured: dict[str, Any] = {}
    runner.run = lambda arguments, _context: captured.update(arguments) or result  # type: ignore[method-assign]
    runner.rerun(
        {"version_id": version["id"], "overwrite": False},
        type("Context", (), {"task_id": "rerun-task"})(),
    )
    assert captured["input_sha256"] == version["input_sha256"]
    assert captured["parent_version_id"] == version["id"]
    assert captured["model"] == "voice-a"
    runner.models.model_hash = "e" * 64  # type: ignore[attr-defined]
    with pytest.raises(MediaPipelineError, match="exact model revision"):
        runner.rerun(
            {"version_id": version["id"], "overwrite": False},
            type("Context", (), {"task_id": "rerun-task-2"})(),
        )
    database.close()


def test_model_compare_contract_has_bounded_unique_models(tmp_path: Path) -> None:
    input_path = tmp_path / "input.wav"
    input_path.write_bytes(b"audio")
    parsed = parse_arguments(
        "model.compare",
        {"input": str(input_path), "models": ["voice-a", "voice-b"]},
    )
    assert parsed["models"] == ["voice-a", "voice-b"]
    with pytest.raises(ValueError, match="unique models"):
        parse_arguments(
            "model.compare",
            {"input": str(input_path), "models": ["voice-a", "VOICE-A"]},
        )
