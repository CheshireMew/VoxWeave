from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .hashing import sha256_file
from .media_pipeline import MediaPipeline
from .model_registry import ModelRegistry
from .project_repository import ProjectRepository
from .protocol import AudioProcessingChain, OperationError, ProjectDocument
from .task_manager import TaskContext


class ProjectService:
    def __init__(
        self,
        repository: ProjectRepository,
        media: MediaPipeline,
        models: ModelRegistry,
    ) -> None:
        self.repository = repository
        self.media = media
        self.models = models

    @staticmethod
    def _require_input(path_value: str) -> Path:
        path = Path(path_value).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    def create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        source = self._require_input(arguments["input"])
        document = ProjectDocument.model_validate(
            arguments.get("document") or {}
        ).model_dump(mode="json")
        return self.repository.create(
            name=arguments["name"],
            input_path=str(source),
            output_path=arguments.get("output"),
            content_mode=arguments.get("content_mode", "clean"),
            document=document,
        )

    def get(self, project_id: str) -> dict[str, Any]:
        return self.repository.get(project_id)

    def list(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.repository.list(
            arguments["limit"],
            arguments.get("cursor"),
            include_archived=bool(arguments.get("include_archived", False)),
        )

    @staticmethod
    def _input_snapshot(project: dict[str, Any]) -> dict[str, Any]:
        source = Path(project["input_path"]).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        stat = source.stat()
        snapshot: dict[str, Any] = {
            "path": str(source),
            "size_bytes": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }
        if project.get("input_sha256"):
            snapshot["sha256"] = project["input_sha256"]
        return snapshot

    def prepare_analysis(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = self.repository.get(arguments["project_id"])
        if int(project["revision"]) != int(arguments["expected_revision"]):
            raise OperationError("revision_conflict", "project changed before submission")
        return {
            "project": {"id": project["id"], "revision": project["revision"]},
            "input": self._input_snapshot(project),
        }

    def prepare_run(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = self.repository.get(arguments["project_id"])
        if int(project["revision"]) != int(arguments["expected_revision"]):
            raise OperationError("revision_conflict", "project changed before submission")
        models: dict[str, dict[str, Any]] = {}
        for assignment in self._assignments(project):
            model = self.models.resolve_for_execution(assignment["model"])
            models[model["id"]] = {
                "id": model["id"],
                "model_sha256": model["model_sha256"],
                "index_sha256": model.get("index_sha256"),
            }
        return {
            "project": {"id": project["id"], "revision": project["revision"]},
            "input": self._input_snapshot(project),
            "models": models,
        }

    def prepare_preview(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = self.repository.get(arguments["project_id"])
        if int(project["revision"]) != int(arguments["expected_revision"]):
            raise OperationError("revision_conflict", "project changed before submission")
        segment = next(
            (
                item
                for item in project["document"].get("segments", [])
                if item["id"] == arguments["segment_id"]
            ),
            None,
        )
        if segment is None:
            raise LookupError(f"project segment not found: {arguments['segment_id']}")
        selector = segment.get("model") or project["document"].get("default_model")
        if not selector:
            raise OperationError(
                "project_segments_unassigned", "preview segment has no voice model"
            )
        model = self.models.resolve_for_execution(selector)
        revision = {
            "id": model["id"],
            "model_sha256": model["model_sha256"],
            "index_sha256": model.get("index_sha256"),
        }
        return {
            "project": {"id": project["id"], "revision": project["revision"]},
            "input": self._input_snapshot(project),
            "model": revision,
            "models": {model["id"]: revision},
        }

    def update(self, arguments: dict[str, Any]) -> dict[str, Any]:
        project = self.repository.get(arguments["project_id"])
        changes: dict[str, Any] = {}
        mapping = {
            "name": "name",
            "output": "output_path",
            "content_mode": "content_mode",
        }
        for source_key, target_key in mapping.items():
            if source_key in arguments:
                changes[target_key] = arguments[source_key]
        if "document" in arguments:
            changes["document"] = ProjectDocument.model_validate(
                arguments["document"]
            ).model_dump(mode="json")
        if "input" in arguments:
            source = self._require_input(arguments["input"])
            changes.update(
                input_path=str(source),
                input_sha256=None,
                analysis_manifest=None,
                analysis_sha256=None,
            )
            if "document" not in arguments:
                previous = dict(project["document"])
                previous["segments"] = []
                changes["document"] = previous
        return self.repository.update(
            project["id"], arguments["expected_revision"], changes
        )

    def archive(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.repository.update(
            arguments["project_id"],
            arguments["expected_revision"],
            {"state": "archived" if arguments.get("archived", True) else "active"},
        )

    def restore(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.repository.restore(
            arguments["project_id"],
            arguments["expected_revision"],
            arguments["revision"],
        )

    def analyze(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        project = self.repository.get(arguments["project_id"])
        if project["state"] != "active":
            raise OperationError("project_archived", "archived project cannot be analyzed")
        if int(project["revision"]) != int(arguments["expected_revision"]):
            raise OperationError("revision_conflict", "project changed before analysis started")
        result = self.media.analyze(
            {
                "input": project["input_path"],
                "input_sha256": project.get("input_sha256"),
                "content_mode": project["content_mode"],
            },
            context,
        )
        existing = {
            segment["id"]: segment
            for segment in project["document"].get("segments", [])
        }
        segments = []
        for analyzed in result.get("segments", []):
            previous = existing.get(analyzed["id"], {})
            segments.append(
                {
                    **analyzed,
                    "enabled": bool(previous.get("enabled", True)),
                    "model": previous.get("model"),
                    "parameters": previous.get("parameters", {}),
                    "label": previous.get("label", ""),
                    "notes": previous.get("notes", ""),
                }
            )
        document = ProjectDocument.model_validate(
            {
                **project["document"],
                "duration_seconds": result.get("duration_seconds")
                or result.get("input", {}).get("duration_seconds")
                or 0,
                "waveform_peaks": result.get("waveform_peaks") or [],
                "segments": segments,
            }
        ).model_dump(mode="json")
        manifest = Path(result["manifest_path"])
        return self.repository.update(
            project["id"],
            project["revision"],
            {
                "input_sha256": result["input"]["sha256"],
                "analysis_manifest": str(manifest),
                "analysis_sha256": sha256_file(manifest),
                "document": document,
            },
        )

    @staticmethod
    def _merged_parameters(
        defaults: dict[str, Any], overrides: dict[str, Any]
    ) -> dict[str, Any]:
        """Merge persisted segment overrides without letting schema defaults mask the project."""
        merged = dict(defaults)
        neutral_chain = AudioProcessingChain().model_dump(mode="json")
        override_chain = dict(overrides.get("processing_chain") or {})
        for key, value in overrides.items():
            if key != "processing_chain" and value is not None:
                merged[key] = value
        if override_chain and override_chain != neutral_chain:
            chain = dict(merged.get("processing_chain") or neutral_chain)
            for key, value in override_chain.items():
                if value != neutral_chain.get(key):
                    chain[key] = value
            merged["processing_chain"] = chain
        return merged

    @staticmethod
    def _assignments(project: dict[str, Any]) -> list[dict[str, Any]]:
        document = project["document"]
        default_model = document.get("default_model")
        default_parameters = document.get("default_parameters") or {}
        grouped: dict[str, dict[str, Any]] = {}
        missing = []
        for segment in document.get("segments", []):
            if not segment.get("enabled", True):
                continue
            model = segment.get("model") or default_model
            if not model:
                missing.append(segment["id"])
                continue
            parameters = ProjectService._merged_parameters(
                default_parameters, segment.get("parameters") or {}
            )
            key = json.dumps([model, parameters], ensure_ascii=False, sort_keys=True)
            group = grouped.setdefault(
                key,
                {"model": model, "parameters": parameters, "segment_ids": []},
            )
            group["segment_ids"].append(segment["id"])
        if missing:
            raise OperationError(
                "project_segments_unassigned",
                f"enabled project segments have no model: {missing[:20]}",
            )
        if not grouped:
            raise OperationError(
                "project_has_no_segments", "project has no enabled assigned segments"
            )
        return list(grouped.values())

    def run(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        project = self.repository.get(arguments["project_id"])
        if project["state"] != "active":
            raise OperationError("project_archived", "archived project cannot be rendered")
        if int(project["revision"]) != int(arguments["expected_revision"]):
            raise OperationError("revision_conflict", "project changed before render started")
        self._require_input(project["input_path"])
        if not project.get("output_path"):
            raise OperationError("project_output_required", "project output path is not set")
        manifest_value = project.get("analysis_manifest")
        if not manifest_value or not Path(manifest_value).is_file():
            raise OperationError(
                "project_analysis_required", "project must be analyzed before render"
            )
        result = self.media.conversion.run(
            {
                "input": project["input_path"],
                "input_sha256": project.get("input_sha256"),
                "output": project["output_path"],
                "content_mode": project["content_mode"],
                "analysis_manifest": manifest_value,
                "assignments": self._assignments(project),
                "project": {
                    "id": project["id"],
                    "name": project["name"],
                    "revision": project["revision"],
                },
                "overlap_policy": project["document"].get("overlap_policy", "convert"),
                "processing_chain": (
                    project["document"].get("default_parameters") or {}
                ).get("processing_chain")
                or {},
                "overwrite": bool(arguments.get("overwrite", False)),
            },
            context,
        )
        result["project"] = {
            "id": project["id"],
            "name": project["name"],
            "revision": project["revision"],
        }
        return result

    def preview(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        project = self.repository.get(arguments["project_id"])
        if int(project["revision"]) != int(arguments["expected_revision"]):
            raise OperationError("revision_conflict", "project changed before preview started")
        segment = next(
            (
                item
                for item in project["document"].get("segments", [])
                if item["id"] == arguments["segment_id"]
            ),
            None,
        )
        if segment is None:
            raise LookupError(f"project segment not found: {arguments['segment_id']}")
        model = segment.get("model") or project["document"].get("default_model")
        if not model:
            raise OperationError(
                "project_segments_unassigned", "preview segment has no voice model"
            )
        parameters = self._merged_parameters(
            project["document"].get("default_parameters") or {},
            segment.get("parameters") or {},
        )
        return self.media.preview(
            {
                "input": project["input_path"],
                "input_sha256": project.get("input_sha256"),
                "model": model,
                "variants": [parameters],
                "start_seconds": max(0, float(segment["start_seconds"]) - 1.0),
                "duration_seconds": 10.0,
                "content_mode": project["content_mode"],
            },
            context,
        )
