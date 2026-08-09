from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]


def _schema(name: str) -> dict:
    return json.loads((ROOT / "schemas" / name).read_text(encoding="utf-8"))


def test_empty_official_catalog_is_valid() -> None:
    catalog = json.loads((ROOT / "catalog" / "catalog.v1.json").read_text(encoding="utf-8"))
    Draft202012Validator(_schema("catalog.v1.schema.json")).validate(catalog)


def test_rvc_model_contract_example_is_valid() -> None:
    model = {
        "protocol": "voxweave-rvc-model",
        "version": 1,
        "id": "local.example.default",
        "display_name": "Example",
        "aliases": ["Example Voice"],
        "family": "example",
        "checkpoint_epoch": None,
        "model_path": "D:\\Models\\example.pth",
        "model_sha256": "a" * 64,
        "index_path": None,
        "index_sha256": None,
        "index_candidates": [],
        "rvc_version": "v2",
        "sample_rate": 40000,
        "f0": True,
        "source_kind": "external",
        "license_spdx": None,
        "source_url": None,
        "recommended": {
            "pitch": 9,
            "f0": "rmvpe",
            "index_rate": 0.72,
            "rms_mix_rate": 0.25,
            "protect": 0.33,
            "content_mode": "clean",
        },
        "status": "ready",
        "imported_at": "2026-08-09T00:00:00Z",
    }
    Draft202012Validator(_schema("voxweave-rvc-model.v1.schema.json")).validate(model)
