from __future__ import annotations

from typing import Any

from .artifacts import ArtifactStore
from .batch import BatchManager
from .capabilities import public_capabilities
from .config import Settings
from .database import Database
from .diagnostics import DiagnosticsService
from .media_pipeline import MediaPipeline
from .model_catalog import ModelCatalogClient
from .model_importer import ModelImporter
from .model_inspector import ModelInspector
from .model_registry import ModelRegistry
from .model_scanner import ModelScanner
from .operation_receipt_repository import OperationReceiptRepository
from .operation_router import OperationRouter
from .preset_repository import PresetRepository
from .presets import PresetService
from .project_repository import ProjectRepository
from .projects import ProjectService
from .protocol import describe
from .realtime import RealtimeSessionManager
from .realtime_calibration import RealtimeCalibrationService
from .realtime_control import RealtimeControlService
from .realtime_recordings import RealtimeRecordingService
from .realtime_routing_test import RealtimeRoutingTestService
from .realtime_scenes import RealtimeWorkspaceService
from .rvc_engine import RvcEngine
from .settings_repository import SettingsRepository
from .settings_service import SettingsService
from .storage import StorageArchiveManager
from .task_event_stream import TaskEventStream
from .task_manager import TaskManager
from .task_service import TaskService
from .updater import UpdateService


class Controller:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.ensure_layout()
        self.database = Database(settings.database_path)
        self.settings_service = SettingsService(
            settings, SettingsRepository(self.database)
        )
        self.receipts = OperationReceiptRepository(self.database)
        self.models = ModelRegistry(self.database)
        self.model_inspector = ModelInspector(settings)
        self.model_scanner = ModelScanner(
            settings,
            self.models,
            self.model_inspector,
            self.settings_service,
        )
        self.model_importer = ModelImporter(
            settings,
            self.models,
            self.model_inspector,
            ModelCatalogClient(),
        )
        self.presets = PresetService(self.models, PresetRepository(self.database))
        self.tasks = TaskManager(self.database)
        self.task_event_stream = TaskEventStream(self.tasks)
        self.artifacts = ArtifactStore(self.database)
        self.media = MediaPipeline(settings, self.models, self.artifacts)
        self.projects = ProjectService(
            ProjectRepository(self.database), self.media, self.models
        )
        self.realtime = RealtimeSessionManager(
            self.database,
            self.models,
            RvcEngine(settings),
            self.tasks.pause_dispatch,
            self.tasks.resume_dispatch,
            self.media.release_engine,
        )
        self.realtime_workspace = RealtimeWorkspaceService(
            self.database, self.realtime
        )
        self.realtime_calibration = RealtimeCalibrationService(
            self.realtime.devices,
            self.realtime.audio_test,
            self.models.resolve,
        )
        self.realtime_control = RealtimeControlService(
            self.realtime.sessions,
            self.realtime.worker,
            settings.artifacts_dir,
            self.realtime._control_lock,
        )
        self.realtime_routing_test = RealtimeRoutingTestService(
            self.realtime.sessions,
            self.realtime.worker,
            self.realtime.requests.engine,
            self.realtime._control_lock,
            self.realtime._lock,
            lambda: self.realtime._service_stopping,
        )
        self.realtime_recordings = RealtimeRecordingService(
            self.realtime.sessions,
            self.projects,
        )
        self.batch = BatchManager(
            self.database,
            self.tasks,
            self.models.resolve_for_execution,
        )
        self.storage = StorageArchiveManager(settings, self.database, self.artifacts)
        self.updater = UpdateService(settings)
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
            self.projects,
            self.realtime,
            self.realtime_calibration,
            self.realtime_control,
            self.realtime_routing_test,
            self.realtime_recordings,
            self.realtime_workspace,
            self.batch,
            self.storage,
            self.updater,
            diagnostics,
            self.settings_service,
            self.receipts,
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
        payload["capabilities"] = public_capabilities(
            self.model_importer.catalog.list_entries()
        )
        return payload

    def shutdown(self) -> None:
        failures: list[str] = []
        for name, close in (
            ("batch watcher", self.batch.shutdown),
            ("realtime manager", self.realtime.shutdown),
            ("task worker", self.tasks.shutdown),
            ("media engine", self.media.shutdown),
            ("database", self.database.close),
        ):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - complete coordinated shutdown
                failures.append(f"{name}: {exc}")
        if failures:
            raise RuntimeError("; ".join(failures))
