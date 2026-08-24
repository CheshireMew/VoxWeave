from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

import pytest

from voxweave.settings_file_store import load_settings


def test_current_machine_models_are_distinct() -> None:
    settings = load_settings(create=False)
    if not settings.rvc_root or not Path(settings.rvc_root).is_dir():
        pytest.skip("current-machine RVC runtime is not configured")
    if not settings.database_path.is_file():
        pytest.skip("current-machine model database is unavailable")
    uri = "file:" + quote(settings.database_path.resolve().as_posix(), safe="/:") + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM models").fetchall()
    connection.close()
    models = [dict(row) for row in rows]
    if len(models) < 13:
        pytest.skip("current-machine model scan has not been run")
    selectors = {"公开御姐", "keruan", "guaiguai"}
    selected = []
    for model in models:
        values = {
            model["id"].casefold(),
            model["display_name"].casefold(),
            *[value.casefold() for value in json.loads(model["aliases_json"])],
        }
        if values & {value.casefold() for value in selectors}:
            selected.append(model)
    assert len(selected) == 3
    assert len({item["id"] for item in selected}) == 3
    assert len({item["model_sha256"] for item in selected}) == 3
    assert all(item["status"] == "ready" for item in selected)
