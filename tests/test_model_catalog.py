from __future__ import annotations

import json

import pytest

from voxweave.model_catalog import ModelCatalogClient


def test_bundled_catalog_produces_verified_import_arguments(tmp_path) -> None:
    catalog = {
        "protocol": "voxweave-model-catalog",
        "version": 1,
        "updated_at": "2026-08-12T00:00:00Z",
        "models": [
            {
                "id": "official.test.male",
                "display_name": "Test male",
                "aliases": ["test male"],
                "license_spdx": "Apache-2.0",
                "source_url": "https://example.test/model",
                "model_url": "https://example.test/model.pth",
                "model_size_bytes": 123,
                "model_sha256": "a" * 64,
                "recommended": {
                    "pitch": 0,
                    "f0": "rmvpe",
                    "index_rate": 0.72,
                    "rms_mix_rate": 0.25,
                    "protect": 0.33,
                    "content_mode": "clean",
                },
            }
        ],
    }
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    client = ModelCatalogClient(path)

    assert client.list_entries() == catalog["models"]
    arguments = client.import_arguments(None, "official.test.male", lambda: False)
    assert arguments["model"] == "https://example.test/model.pth"
    assert arguments["model_sha256"] == "a" * 64
    assert arguments["download_size_bytes"] == 123
    assert arguments["recommended"]["pitch"] == 0


@pytest.mark.parametrize(
    "models, message",
    [
        ([], "at least one model"),
        (
            [
                {
                    "id": "official.zero",
                    "display_name": "Zero",
                    "aliases": [],
                    "license_spdx": "CC-BY-4.0",
                    "source_url": "https://example.test/zero",
                    "model_url": "https://example.test/zero.pth",
                    "model_size_bytes": 0,
                    "model_sha256": "a" * 64,
                    "recommended": {},
                }
            ],
            "greater than 0",
        ),
    ],
)
def test_catalog_rejects_an_empty_or_impossible_download(
    tmp_path, models, message
) -> None:
    path = tmp_path / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "protocol": "voxweave-model-catalog",
                "version": 1,
                "updated_at": "2026-08-12T00:00:00Z",
                "models": models,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises((ValueError, TypeError), match=message):
        ModelCatalogClient(path).list_entries()
