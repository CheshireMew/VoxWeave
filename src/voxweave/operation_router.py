from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .batch import BatchManager
from .capabilities import catalog_entry_capabilities
from .config import Settings
from .diagnostics import DiagnosticsService
from .media_pipeline import MediaPipeline
from .model_importer import ModelImporter
from .model_registry import ModelRegistry
from .model_scanner import ModelScanner
from .operation_receipt_repository import OperationReceiptRepository
from .presets import PresetService
from .projects import ProjectService
from .protocol import (
    OPERATION_SPECS,
    OperationError,
    parse_arguments,
    public_error_code,
    validate_execute_result,
)
from .realtime import RealtimeSessionManager
from .realtime_calibration import RealtimeCalibrationService
from .realtime_control import RealtimeControlService
from .realtime_recordings import RealtimeRecordingService
from .realtime_routing_test import RealtimeRoutingTestService
from .realtime_scenes import RealtimeWorkspaceService
from .runtime_install import install_runtime
from .settings_service import SettingsService
from .storage import StorageArchiveManager
from .task_manager import Handler, TaskManager
from .task_service import TaskService
from .updater import UpdateService


@dataclass(frozen=True, slots=True)
class RequestMetadata:
    request_id: str | None
    actor: dict[str, Any] | None


SyncHandler = Callable[[dict[str, Any], RequestMetadata], Any]
Preparer = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OperationBinding:
    handler: SyncHandler | Handler
    preparers: tuple[Preparer, ...] = ()


class OperationRouter:
    def __init__(
        self,
        settings: Settings,
        models: ModelRegistry,
        scanner: ModelScanner,
        importer: ModelImporter,
        presets: PresetService,
        tasks: TaskManager,
        task_service: TaskService,
        artifacts: ArtifactStore,
        media: MediaPipeline,
        projects: ProjectService,
        realtime: RealtimeSessionManager,
        realtime_calibration: RealtimeCalibrationService,
        realtime_control: RealtimeControlService,
        realtime_routing_test: RealtimeRoutingTestService,
        realtime_recordings: RealtimeRecordingService,
        realtime_workspace: RealtimeWorkspaceService,
        batch: BatchManager,
        storage: StorageArchiveManager,
        updater: UpdateService,
        diagnostics: DiagnosticsService,
        settings_service: SettingsService,
        receipts: OperationReceiptRepository,
    ) -> None:
        self.settings = settings
        self.models = models
        self.tasks = tasks
        self.receipts = receipts
        snapshot_model = self._model_preparer
        snapshot_input = self._input_preparer

        def sync(handler: Callable[[dict[str, Any]], Any]) -> SyncHandler:
            return lambda arguments, _metadata: handler(arguments)

        bindings: dict[str, OperationBinding] = {
            "diagnostics.snapshot": OperationBinding(
                lambda _arguments, context: diagnostics.snapshot(context)
            ),
            "settings.get": OperationBinding(
                sync(lambda _arguments: settings_service.get())
            ),
            "settings.update": OperationBinding(
                lambda arguments, _metadata: settings_service.update(arguments)
            ),
            "settings.events": OperationBinding(
                sync(settings_service.events)
            ),
            "runtime.inspect": OperationBinding(
                lambda _arguments, context: diagnostics.inspect_runtime(context)
            ),
            "runtime.install": OperationBinding(
                lambda arguments, context: install_runtime(
                    settings,
                    settings_service,
                    arguments,
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "model.scan": OperationBinding(
                lambda arguments, context: scanner.execute(
                    arguments,
                    context.progress,
                    context.cancelled,
                )
            ),
            "model.list": OperationBinding(sync(lambda _arguments: models.list_models())),
            "model.catalog.list": OperationBinding(
                sync(
                    lambda _arguments: [
                        {
                            **catalog_entry_capabilities(entry),
                            **models.catalog_state(entry),
                        }
                        for entry in importer.catalog.list_entries()
                    ]
                )
            ),
            "model.resolve": OperationBinding(
                sync(lambda arguments: models.resolve(arguments["voice"]))
            ),
            "model.archive": OperationBinding(
                sync(
                    lambda arguments: models.set_archived(
                        arguments["model_id"], arguments["archived"]
                    )
                )
            ),
            "model.verify": OperationBinding(
                lambda arguments, context: models.verify_integrity(
                    arguments, context.progress, context.cancelled
                )
            ),
            "model.metadata.update": OperationBinding(sync(models.update_metadata)),
            "model.import": OperationBinding(
                lambda arguments, context: importer.import_model(
                    arguments,
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "model.catalog.install": OperationBinding(
                lambda arguments, context: importer.install_from_catalog(
                    arguments.get("catalog_url"),
                    arguments["model_id"],
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "preset.list": OperationBinding(sync(presets.list)),
            "preset.save": OperationBinding(sync(presets.save)),
            "preset.update": OperationBinding(sync(presets.update)),
            "preset.archive": OperationBinding(sync(presets.archive)),
            "preset.copy": OperationBinding(sync(presets.copy)),
            "preset.export": OperationBinding(sync(presets.export)),
            "preset.import": OperationBinding(sync(presets.import_bundle)),
            "media.inspect": OperationBinding(media.inspect, (snapshot_input,)),
            "media.analyze": OperationBinding(media.analyze, (snapshot_input,)),
            "realtime.devices": OperationBinding(sync(lambda _arguments: realtime.devices())),
            "realtime.audio_test": OperationBinding(sync(realtime.audio_test)),
            "realtime.calibrate": OperationBinding(sync(realtime_calibration.calibrate)),
            "realtime.prepare": OperationBinding(sync(realtime.prepare)),
            "realtime.start": OperationBinding(sync(realtime.start)),
            "realtime.control": OperationBinding(sync(realtime_control.control)),
            "realtime.status": OperationBinding(sync(lambda _arguments: realtime.status())),
            "realtime.stop": OperationBinding(sync(lambda _arguments: realtime.stop())),
            "realtime.release": OperationBinding(sync(lambda _arguments: realtime.release())),
            "realtime.routing.inspect": OperationBinding(
                sync(lambda _arguments: realtime_workspace.routing())
            ),
            "realtime.routing.test": OperationBinding(sync(realtime_routing_test.run)),
            "realtime.recording.promote": OperationBinding(
                sync(realtime_recordings.promote)
            ),
            "realtime.scene.create": OperationBinding(
                sync(realtime_workspace.repository.create)
            ),
            "realtime.scene.update": OperationBinding(
                sync(realtime_workspace.repository.update)
            ),
            "realtime.scene.archive": OperationBinding(
                sync(realtime_workspace.repository.archive)
            ),
            "realtime.scene.get": OperationBinding(
                sync(lambda arguments: realtime_workspace.repository.get(arguments["scene_id"]))
            ),
            "realtime.scene.list": OperationBinding(
                sync(
                    lambda arguments: realtime_workspace.repository.list(
                        bool(arguments["include_archived"])
                    )
                )
            ),
            "realtime.scene.apply": OperationBinding(sync(realtime_workspace.apply)),
            "conversion.preview": OperationBinding(
                media.preview,
                (snapshot_model, snapshot_input),
            ),
            "model.compare": OperationBinding(
                media.compare,
                (snapshot_model, snapshot_input),
            ),
            "conversion.run": OperationBinding(
                media.conversion.run,
                (snapshot_model, snapshot_input),
            ),
            "project.create": OperationBinding(sync(projects.create)),
            "project.update": OperationBinding(sync(projects.update)),
            "project.archive": OperationBinding(sync(projects.archive)),
            "project.get": OperationBinding(
                sync(lambda arguments: projects.get(arguments["project_id"]))
            ),
            "project.list": OperationBinding(sync(projects.list)),
            "project.history": OperationBinding(
                sync(lambda arguments: projects.repository.history(arguments["project_id"]))
            ),
            "project.restore": OperationBinding(sync(projects.restore)),
            "project.analyze": OperationBinding(
                projects.analyze,
                (projects.prepare_analysis,),
            ),
            "project.run": OperationBinding(
                projects.run,
                (projects.prepare_run,),
            ),
            "project.preview": OperationBinding(
                projects.preview,
                (projects.prepare_preview,),
            ),
            "batch.create": OperationBinding(sync(batch.create)),
            "batch.update": OperationBinding(sync(batch.update)),
            "batch.archive": OperationBinding(sync(batch.archive)),
            "batch.get": OperationBinding(sync(lambda arguments: batch.get(arguments["batch_id"]))),
            "batch.list": OperationBinding(
                sync(lambda arguments: batch.list(arguments["limit"], arguments.get("cursor")))
            ),
            "batch.run": OperationBinding(batch.run),
            "batch.plan": OperationBinding(
                lambda arguments, context: batch.submissions.plan(
                    batch.rules.get(arguments["batch_id"]),
                    context.progress,
                    context.cancelled,
                )
            ),
            "batch.retry": OperationBinding(batch.retry),
            "batch.item.retry": OperationBinding(sync(batch.item_retries.retry)),
            "batch.watch": OperationBinding(
                sync(
                    lambda arguments: batch.set_watch(
                        arguments["batch_id"],
                        bool(arguments["enabled"]),
                    )
                )
            ),
            "storage.archive": OperationBinding(
                lambda arguments, context: storage.archive(
                    arguments,
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "storage.inspect": OperationBinding(
                lambda arguments, context: storage.inspect(
                    arguments,
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "storage.restore": OperationBinding(
                lambda arguments, context: storage.restore(
                    arguments,
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "storage.migration.plan": OperationBinding(
                lambda arguments, _context: storage.migration_plan(arguments)
            ),
            "storage.migration.prepare": OperationBinding(
                sync(storage.prepare_migration)
            ),
            "update.check": OperationBinding(
                lambda arguments, context: updater.check(
                    arguments,
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "update.download": OperationBinding(
                lambda arguments, context: updater.download(
                    arguments,
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "update.install": OperationBinding(
                lambda arguments, context: updater.install(
                    arguments,
                    context.progress,
                    context.cancelled,
                    context.task_id,
                )
            ),
            "update.activate": OperationBinding(sync(updater.activate)),
            "update.rollback": OperationBinding(sync(updater.rollback)),
            "result.get": OperationBinding(
                sync(lambda arguments: media.conversion.results.get(arguments["version_id"]))
            ),
            "result.list": OperationBinding(sync(media.conversion.results.list)),
            "result.update": OperationBinding(sync(media.conversion.results.update)),
            "result.rerun": OperationBinding(media.conversion.rerun),
            "task.list": OperationBinding(
                sync(
                    lambda arguments: tasks.list(
                        arguments["limit"],
                        arguments.get("cursor"),
                    )
                )
            ),
            "task.events": OperationBinding(
                sync(
                    lambda arguments: {
                        "events": tasks.events_all(
                            arguments["after_id"],
                            arguments["limit"],
                        )
                    }
                )
            ),
            "task.get": OperationBinding(
                sync(lambda arguments: task_service.get(arguments["task_id"]))
            ),
            "task.cancel": OperationBinding(
                sync(lambda arguments: tasks.cancel(arguments["task_id"]))
            ),
            "task.retry": OperationBinding(
                lambda arguments, metadata: task_service.retry(
                    arguments["task_id"],
                    request_id=metadata.request_id,
                    actor=metadata.actor,
                )
            ),
        }
        self._validate_bindings(bindings)
        self.bindings = bindings
        for operation, binding in bindings.items():
            if OPERATION_SPECS[operation].long_running:
                tasks.register(operation, binding.handler)  # type: ignore[arg-type]

    @staticmethod
    def _validate_bindings(bindings: dict[str, OperationBinding]) -> None:
        missing = set(OPERATION_SPECS) - set(bindings)
        extra = set(bindings) - set(OPERATION_SPECS)
        if missing or extra:
            raise RuntimeError(
                f"operation bindings do not match protocol; missing={sorted(missing)}, "
                f"extra={sorted(extra)}"
            )

    def _model_preparer(self, arguments: dict[str, Any]) -> dict[str, Any]:
        snapshots: dict[str, dict[str, Any]] = {}
        result: dict[str, Any] = {}

        def resolve(selector: str) -> dict[str, Any]:
            model = self.models.resolve(selector)
            snapshots[model["id"]] = {
                "id": model["id"],
                "model_sha256": model["model_sha256"],
                "index_sha256": model.get("index_sha256"),
            }
            return model

        if arguments.get("model"):
            model = resolve(arguments["model"])
            arguments["model"] = model["id"]
            result["model"] = snapshots[model["id"]]
        for assignment in arguments.get("assignments") or []:
            model = resolve(assignment["model"])
            assignment["model"] = model["id"]
        if arguments.get("models"):
            resolved_models = []
            for selector in arguments["models"]:
                model = resolve(selector)
                resolved_models.append(model["id"])
            arguments["models"] = resolved_models
        if snapshots:
            result["models"] = snapshots
        return result

    @staticmethod
    def _input_preparer(arguments: dict[str, Any]) -> dict[str, Any]:
        source = Path(arguments["input"]).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        stat = source.stat()
        snapshot = {
            "path": str(source),
            "size_bytes": stat.st_size,
            "modified_ns": stat.st_mtime_ns,
        }
        if arguments.get("input_sha256"):
            snapshot["sha256"] = arguments["input_sha256"]
        return {"input": snapshot}

    def execute(
        self,
        operation: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None,
        actor: dict[str, Any] | None,
    ) -> Any:
        parsed = parse_arguments(operation, arguments)
        spec = OPERATION_SPECS[operation]
        requires_receipt = spec.long_running or spec.mutating
        if requires_receipt and not request_id:
            raise OperationError(
                "request_id_required",
                "mutating and long-running commands require request_id",
            )
        if spec.long_running and request_id:
            existing_task = self.tasks.find_by_request(
                request_id,
                operation,
                parsed,
                actor,
            )
            if existing_task is not None:
                result = validate_execute_result(operation, existing_task)
                self.receipts.complete(request_id, result)
                return result
        if requires_receipt:
            state, record = self.receipts.claim(
                request_id or "", operation, parsed, actor
            )
            if state == "running":
                record = self.receipts.await_terminal(request_id or "")
                state = str(record["state"])
            if state == "completed":
                return record["result"] if record else None
            if state == "failed":
                raise OperationError(
                    str(record.get("error_type") or "operation_failed"),
                    str(record.get("error") or "operation failed"),
                )

        binding = self.bindings[operation]
        try:
            snapshot: dict[str, Any] = {}
            for prepare in binding.preparers:
                snapshot.update(prepare(parsed))
            if spec.long_running:
                result = self.tasks.submit(
                    operation,
                    parsed,
                    request_id=request_id,
                    actor=actor,
                    snapshot=snapshot,
                )
            else:
                handler: SyncHandler = binding.handler  # type: ignore[assignment]
                result = handler(parsed, RequestMetadata(request_id, actor))
            result = validate_execute_result(operation, result)
        except Exception as exc:
            if requires_receipt:
                self.receipts.fail(
                    request_id or "",
                    public_error_code(exc),
                    str(exc) or "operation failed",
                )
            raise
        if requires_receipt:
            self.receipts.complete(request_id or "", result)
        return result
