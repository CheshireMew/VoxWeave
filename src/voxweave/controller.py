from __future__ import annotations

from typing import Any

from .artifacts import ArtifactStore
from .batch import BatchManager
from .config import Settings
from .database import Database
from .diagnostics import DiagnosticsService
from .media_pipeline import MediaPipeline
from .model_catalog import ModelCatalogClient
from .model_importer import ModelImporter
from .model_inspector import ModelInspector
from .model_registry import ModelRegistry
from .model_scanner import ModelScanner
from .operation_router import OperationRouter
from .preset_repository import PresetRepository
from .presets import PresetService
from .protocol import describe
from .realtime import RealtimeSessionManager
from .rvc_engine import RvcEngine
from .storage import StorageArchiveManager
from .task_manager import TaskManager
from .task_service import TaskService


class Controller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_layout()
        self.database = Database(settings.database_path)
        self.models = ModelRegistry(self.database)
        self.model_inspector = ModelInspector(settings)
        self.model_scanner = ModelScanner(settings, self.models, self.model_inspector)
        self.model_importer = ModelImporter(
            settings,
            self.models,
            self.model_inspector,
            ModelCatalogClient(),
        )
        self.presets = PresetService(self.models, PresetRepository(self.database))
        self.tasks = TaskManager(self.database)
        self.artifacts = ArtifactStore(self.database)
        self.media = MediaPipeline(settings, self.models, self.artifacts)
        self.realtime = RealtimeSessionManager(
            self.database,
            self.models,
            RvcEngine(settings),
            self.tasks.pause_dispatch,
            self.tasks.resume_dispatch,
        )
        self.batch = BatchManager(
            self.database,
            self.tasks,
            self.models.resolve_for_execution,
        )
        self.storage = StorageArchiveManager(settings, self.database, self.artifacts)
        diagnostics = DiagnosticsService(settings, self.models, self.realtime, self.tasks)
        task_service = TaskService(self.tasks, self.artifacts, self.batch)
        self.router = OperationRouter(
            settings,
            self.models,
            self.model_scanner,
            self.model_importer,
            self.presets,
            self.tasks,
            task_service,
            self.artifacts,
            self.media,
            self.realtime,
            self.batch,
            self.storage,
            diagnostics,
        )
        self.tasks.start(preserved_task_ids=self.batch.durable_task_ids())
        self.batch.start()

    def execute(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None = None,
        actor: dict[str, Any] | None = None,
    ) -> Any:
        return self.router.execute(
            operation,
            arguments,
            request_id=request_id,
            actor=actor,
        )

    def task_events(
        self,
        task_id: str,
        after_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return self.tasks.events(task_id, after_id, limit)

    def all_task_events(
        self,
        after_id: int = 0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return self.tasks.events_all(after_id, limit)

    def describe(self) -> dict[str, Any]:
        payload = describe()
        payload["runtime"] = {
            "configured": bool(self.settings.rvc_root and self.settings.rvc_python),
            "rvc_root": self.settings.rvc_root,
            "hardware_backend": self.settings.hardware_backend,
            "inspection_operation": "runtime.inspect",
        }
        return payload

    def shutdown(self) -> None:
        self.realtime.shutdown()
        self.batch.shutdown()
        self.tasks.shutdown()
