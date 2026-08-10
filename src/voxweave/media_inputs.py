from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .media_io import inspect_media
from .model_registry import ModelRegistry
from .protocol import OperationError
from .task_manager import TaskContext


class MediaInputResolver:
    def __init__(self, settings: Settings, models: ModelRegistry) -> None:
        self.settings = settings
        self.models = models

    @staticmethod
    def _verify_submission_revision(path: Path, context: TaskContext) -> None:
        revision = context.snapshot.get("input")
        if not revision:
            return
        resolved = path.expanduser().resolve()
        if str(resolved) != revision.get("path"):
            raise OperationError("input_revision_mismatch", "task input path changed")
        if not resolved.is_file():
            raise OperationError("input_missing", f"media no longer exists: {resolved}")
        stat = resolved.stat()
        if (
            stat.st_size != revision.get("size_bytes")
            or stat.st_mtime_ns != revision.get("modified_ns")
        ):
            raise OperationError(
                "input_changed",
                f"media changed after task submission: {resolved}",
            )

    def inspect(
        self,
        path: Path,
        arguments: dict[str, Any],
        context: TaskContext,
    ) -> dict[str, Any]:
        self._verify_submission_revision(path, context)
        media = inspect_media(self.settings, path, context.cancelled)
        expected = arguments.get("input_sha256") or context.snapshot.get("input", {}).get(
            "sha256"
        )
        if expected and media["sha256"].casefold() != str(expected).casefold():
            raise OperationError(
                "input_changed",
                "input SHA-256 does not match the submitted media revision",
            )
        return media

    def model(
        self,
        arguments: dict[str, Any],
        context: TaskContext,
    ) -> dict[str, Any]:
        model = self.models.resolve_for_execution(arguments["model"])
        revision = context.snapshot.get("model")
        if revision and (
            model["id"] != revision.get("id")
            or model["model_sha256"] != revision.get("model_sha256")
            or model.get("index_sha256") != revision.get("index_sha256")
        ):
            raise OperationError(
                "model_revision_changed",
                f"model revision changed after task submission: {model['id']}",
            )
        return model
