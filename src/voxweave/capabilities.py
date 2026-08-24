from __future__ import annotations

from typing import Any

CONTROL_PROTOCOL = "voxweave-control"
CONTROL_PROTOCOL_VERSION = 1
MODEL_PROTOCOL = "voxweave-rvc-model"
MODEL_PROTOCOL_VERSION = 1
CATALOG_PROTOCOL = "voxweave-model-catalog"
CATALOG_PROTOCOL_VERSION = 1

AUDIO_EXTENSIONS = (".wav", ".flac", ".mp3", ".m4a", ".aac")
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm")
MEDIA_EXTENSIONS = (*AUDIO_EXTENSIONS, *VIDEO_EXTENSIONS)

STARTER_MODEL_IDS = (
    "community.zh-male-young",
    "community.zh-female-senior",
)


def catalog_entry_capabilities(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        **entry,
        "starter": str(entry.get("id")) in STARTER_MODEL_IDS,
        "download_size_bytes": int(entry.get("model_size_bytes") or 0)
        + int(entry.get("index_size_bytes") or 0),
    }


def public_capabilities(catalog: list[dict[str, Any]]) -> dict[str, Any]:
    enriched = [catalog_entry_capabilities(entry) for entry in catalog]
    return {
        "media": {
            "audio_extensions": list(AUDIO_EXTENSIONS),
            "video_extensions": list(VIDEO_EXTENSIONS),
            "extensions": list(MEDIA_EXTENSIONS),
        },
        "starter_models": [
            {
                "id": entry["id"],
                "display_name": entry["display_name"],
                "download_size_bytes": entry["download_size_bytes"],
            }
            for entry in enriched
            if entry["starter"]
        ],
        "request_ids": {
            "scope": "global",
            "replay": "same operation, canonical arguments, and actor",
        },
    }
