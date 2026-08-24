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
