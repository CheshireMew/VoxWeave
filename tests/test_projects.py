from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voxweave.database import Database
from voxweave.project_repository import ProjectRepository
from voxweave.projects import ProjectService
from voxweave.protocol import OperationError, parse_arguments


class FakeModels:
    def resolve_for_execution(self, selector: str) -> dict[str, Any]:
        return {
            "id": selector,
            "display_name": selector,
            "model_sha256": selector[0] * 64,
            "index_sha256": None,
            "recommended": {
                "pitch": 0,
                "f0": "rmvpe",
                "index_rate": 0.72,
                "rms_mix_rate": 0.25,
                "protect": 0.33,
                "content_mode": "clean",
            },
        }


class FakeConversion:
    def __init__(self) -> None:
        self.arguments: dict[str, Any] | None = None

    def run(self, arguments: dict[str, Any], _context: Any) -> dict[str, Any]:
        self.arguments = arguments
        return {
            "protocol": "voxweave-conversion-result",
            "version": 1,
            "input": {},
            "output": {},
            "model": {},
            "parameters": {},
            "selected_speakers": [],
            "assignments": [],
            "separation": None,
            "loudness_match": {},
            "segments": [],
            "manifest_path": "result.json",
        }


class FakeMedia:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.conversion = FakeConversion()

    def analyze(self, arguments: dict[str, Any], _context: Any) -> dict[str, Any]:
        manifest = self.root / "analysis.json"
        result = {
            "input": {"sha256": "a" * 64},
            "content_mode": arguments["content_mode"],
            "segments": [
                {
                    "id": "segment-1",
                    "start_seconds": 0.0,
                    "end_seconds": 1.0,
                    "speaker": "speaker-1",
                    "speaker_similarity": 0.9,
                    "overlap": False,
                },
                {
                    "id": "segment-2",
                    "start_seconds": 1.1,
                    "end_seconds": 2.2,
                    "speaker": "speaker-2",
                    "speaker_similarity": 0.8,
                    "overlap": "unresolved",
                },
            ],
        }
        manifest.write_text(json.dumps(result), encoding="utf-8")
        return {**result, "manifest_path": str(manifest)}


def service(tmp_path: Path) -> tuple[ProjectService, Database, FakeMedia, Path]:
    source = tmp_path / "source.wav"
    source.write_bytes(b"source")
    database = Database(tmp_path / "state.sqlite3")
    media = FakeMedia(tmp_path)
    projects = ProjectService(ProjectRepository(database), media, FakeModels())  # type: ignore[arg-type]
    return projects, database, media, source


def test_project_revision_history_and_restore(tmp_path: Path) -> None:
    projects, database, _media, source = service(tmp_path)
    created = projects.create(
        {
            "name": "Episode",
            "input": str(source),
            "output": str(tmp_path / "result.wav"),
            "content_mode": "clean",
            "document": {},
        }
    )
    updated = projects.update(
        {
            "project_id": created["id"],
            "expected_revision": 1,
            "name": "Episode edited",
        }
    )
    restored = projects.restore(
        {
            "project_id": created["id"],
            "expected_revision": 2,
            "revision": 1,
        }
    )
    assert restored["name"] == "Episode"
    assert restored["revision"] == 3
    assert [item["revision"] for item in projects.repository.history(created["id"])] == [
        3,
        2,
        1,
    ]
    with pytest.raises(OperationError, match="project revision changed"):
        projects.update(
            {
                "project_id": created["id"],
                "expected_revision": updated["revision"],
                "name": "stale",
            }
        )
    database.close()


def test_project_analysis_builds_editable_segments_and_render_assignments(
    tmp_path: Path,
) -> None:
    projects, database, media, source = service(tmp_path)
    created = projects.create(
        {
            "name": "Interview",
            "input": str(source),
            "output": str(tmp_path / "result.wav"),
            "content_mode": "clean",
            "document": {
                "default_model": "voice-a",
                "default_parameters": {
                    "processing_chain": {
                        "compressor": True,
                        "target_lufs": -16,
                    }
                },
            },
        }
    )
    context = SimpleNamespace(task_id="task-1", snapshot={}, cancelled=lambda: False)
    analyzed = projects.analyze(
        {"project_id": created["id"], "expected_revision": 1}, context
    )
    assert [segment["id"] for segment in analyzed["document"]["segments"]] == [
        "segment-1",
        "segment-2",
    ]
    document = dict(analyzed["document"])
    document["segments"] = [dict(segment) for segment in document["segments"]]
    document["segments"][1]["model"] = "voice-b"
    document["segments"][1]["parameters"] = {"pitch": 7}
    edited = projects.update(
        {
            "project_id": created["id"],
            "expected_revision": analyzed["revision"],
            "document": document,
        }
    )
    result = projects.run(
        {
            "project_id": created["id"],
            "expected_revision": edited["revision"],
            "overwrite": False,
        },
        context,
    )
    assert result["project"]["revision"] == edited["revision"]
    assignments = media.conversion.arguments["assignments"]  # type: ignore[index]
    assert {assignment["model"] for assignment in assignments} == {"voice-a", "voice-b"}
    assert next(item for item in assignments if item["model"] == "voice-b")["parameters"][
        "pitch"
    ] == 7
    assert all(item["parameters"]["processing_chain"]["compressor"] for item in assignments)
    assert media.conversion.arguments["processing_chain"]["target_lufs"] == -16
    database.close()


def test_conversion_assignment_contract_rejects_duplicate_segments(tmp_path: Path) -> None:
    manifest = tmp_path / "analysis.json"
    manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="assigned more than once"):
        parse_arguments(
            "conversion.run",
            {
                "input": str(tmp_path / "input.wav"),
                "output": str(tmp_path / "output.wav"),
                "analysis_manifest": str(manifest),
                "assignments": [
                    {"segment_ids": ["segment-1"], "model": "voice-a"},
                    {"segment_ids": ["segment-1"], "model": "voice-b"},
                ],
            },
        )
