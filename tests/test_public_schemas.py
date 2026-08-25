from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

import pytest
from jsonschema import Draft202012Validator

from voxweave.config import Settings
from voxweave.database import Database
from voxweave.model_registry import ModelRegistry
from voxweave.settings_file_store import load_settings

ROOT = Path(__file__).parents[1]


def _schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def test_official_catalog_is_valid_and_mirrors_bundled_catalog() -> None:
    catalog = json.loads((ROOT / "catalog" / "catalog.v1.json").read_text(encoding="utf-8"))
    Draft202012Validator(_schema("catalog.v1.schema.json")).validate(catalog)
    bundled = json.loads(
        (ROOT / "src" / "voxweave" / "resources" / "catalog.v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert catalog == bundled
    assert len(catalog["models"]) >= 3
    assert all(model["model_url"].startswith("https://") for model in catalog["models"])
    assert all(
        model["recommended"]["pitch"] == (9 if model["gender"] == "female" else 0)
        for model in catalog["models"]
    )


def test_rvc_model_contract_validates_registry_output(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    model_path = tmp_path / "example.pth"
    model_path.write_bytes(b"not-a-pickle-and-never-loaded-in-process")
    model = ModelRegistry(Database(settings.database_path)).register(
        model_path,
        inspection={"status": "runtime_missing"},
    )
    Draft202012Validator(_schema("voxweave-rvc-model.v1.schema.json")).validate(model)


def test_current_machine_conversion_results_match_public_schema() -> None:
    settings = load_settings(create=False)
    if not settings.database_path.is_file():
        pytest.skip("current-machine task database is unavailable")
    uri = "file:" + quote(settings.database_path.resolve().as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    rows = connection.execute(
        "SELECT result_json FROM tasks WHERE operation='conversion.run' "
        "AND state='completed' AND result_json IS NOT NULL"
    ).fetchall()
    connection.close()
    if not rows:
        pytest.skip("current-machine conversion results are unavailable")
    validator = Draft202012Validator(_schema("voxweave-conversion-result.v1.schema.json"))
    for (payload,) in rows:
        validator.validate(json.loads(payload))


def test_conversion_schema_accepts_project_multi_model_and_processing_result() -> None:
    media = {
        "path": "D:/VoxWeave/input.wav",
        "sha256": "a" * 64,
        "size_bytes": 1,
        "media_type": "audio",
        "duration_seconds": 1,
        "format_name": "wav",
        "audio_streams": [],
        "video_streams": [],
        "subtitle_streams": [],
    }
    model = {
        "id": "voice.one",
        "display_name": "Voice One",
        "model_sha256": "b" * 64,
        "index_sha256": None,
    }
    chain = {
        "noise_reduction_db": 0,
        "highpass_hz": 80,
        "low_eq_db": 0,
        "presence_eq_db": 2,
        "compressor": True,
        "deesser": False,
        "target_lufs": -16,
        "limiter_dbfs": -1,
        "trim_silence": False,
    }
    result = {
        "protocol": "voxweave-conversion-result",
        "version": 1,
        "input": media,
        "output": {
            **media,
            "path": "D:/VoxWeave/output.wav",
            "full_decode": "passed",
            "audio_quality": [],
        },
        "model": {
            "id": "multiple",
            "display_name": "Multiple voices",
            "models": [model],
        },
        "parameters": {
            "pitch": 0,
            "f0": "rmvpe",
            "index_rate": 0.72,
            "rms_mix_rate": 0.25,
            "protect": 0.33,
            "content_mode": "clean",
            "processing_chain": chain,
        },
        "selected_speakers": [],
        "assignments": [
            {"segment_ids": ["segment-1"], "model": model, "parameters": {}}
        ],
        "project": {"id": "project-1", "revision": 3},
        "separation": None,
        "loudness_match": {},
        "processing_chain": {"enabled": True, "settings": chain, "filters": []},
        "segments": [],
        "manifest_path": "D:/VoxWeave/result.json",
    }
    Draft202012Validator(_schema("voxweave-conversion-result.v1.schema.json")).validate(
        result
    )
