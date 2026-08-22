from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Settings
from .model_registry import ModelRegistry
from .realtime import RealtimeSessionManager
from .runtime import inspect_runtime
from .task_manager import TaskContext, TaskManager


class DiagnosticsService:
    def __init__(
        self,
        settings: Settings,
        models: ModelRegistry,
        realtime: RealtimeSessionManager,
        tasks: TaskManager,
    ) -> None:
        self.settings = settings
        self.models = models
        self.realtime = realtime
        self.tasks = tasks

    def inspect_runtime(self, context: TaskContext) -> dict[str, Any]:
        if context.cancelled():
            raise InterruptedError("task cancellation requested")
        context.progress(0.1, "inspecting", "checking configured runtime")
        result = inspect_runtime(self.settings, context.cancelled)
        if context.cancelled():
            raise InterruptedError("task cancellation requested")
        return result

    def snapshot(self, context: TaskContext) -> dict[str, Any]:
        storage = self._storage_summary(context)
        logs = []
        if self.settings.logs_dir.exists():
            for path in sorted(self.settings.logs_dir.glob("*.jsonl*")):
                if path.is_file():
                    stat = path.stat()
                    logs.append(
                        {
                            "name": path.name,
                            "size_bytes": stat.st_size,
                            "modified_ns": stat.st_mtime_ns,
                        }
                    )
        return {
            "protocol": "voxweave-diagnostics",
            "version": 1,
            "settings": self.settings.payload(),
            "runtime": self.inspect_runtime(context),
            "models": self.models.list_models(),
            "realtime": self.realtime.status(),
            "tasks": self.tasks.list(limit=500)["items"],
            "events": self.tasks.recent_events(500),
            "storage": storage,
            "logs": logs,
        }

    def _storage_summary(self, context: TaskContext) -> dict[str, Any]:
        areas: dict[str, Path] = {
            "artifacts": self.settings.artifacts_dir,
            "downloads": self.settings.downloads_dir,
            "cache": self.settings.cache_dir,
            "runtime": self.settings.root / "runtime",
            "pip_cache": self.settings.root / "pip-cache",
            "managed_models": self.settings.managed_models_dir,
        }
        storage = {}
        for index, (name, root) in enumerate(areas.items()):
            file_count = 0
            total_bytes = 0
            if root.exists():
                for path in root.rglob("*"):
                    if context.cancelled():
                        raise InterruptedError("task cancellation requested")
                    if path.is_file():
                        try:
                            total_bytes += path.stat().st_size
                            file_count += 1
                        except (FileNotFoundError, PermissionError, OSError):
                            continue
            storage[name] = {
                "files": file_count,
                "bytes": total_bytes,
            }
            context.progress(
                0.05 + 0.45 * ((index + 1) / len(areas)),
                "inspecting_storage",
                name,
            )
        return storage
