from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    ValidationError,
    model_validator,
)

from . import __version__

PROTOCOL = "voxweave-control"
PROTOCOL_VERSION = 1
SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"
MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class OperationError(RuntimeError):
    """An expected operation failure with a stable public error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def public_error_code(error: Exception) -> str:
    if isinstance(error, OperationError):
        return error.code
    if isinstance(error, ValidationError | ValueError | TypeError):
        return "invalid_arguments"
    if isinstance(error, FileNotFoundError):
        return "file_not_found"
    if isinstance(error, FileExistsError):
        return "target_exists"
    if isinstance(error, LookupError):
        return "not_found"
    if isinstance(error, InterruptedError):
        return "cancelled"
    return "operation_failed"


def _absolute_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or not Path(value).is_absolute():
        raise ValueError("must be a non-empty absolute path")
    return str(Path(value).expanduser().resolve())


def _https_url(value: str) -> str:
    if not isinstance(value, str) or not value.lower().startswith("https://"):
        raise ValueError("must use HTTPS")
    return value


def _non_empty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return value


AbsolutePath = Annotated[str, AfterValidator(_absolute_path)]
HttpsUrl = Annotated[str, AfterValidator(_https_url)]
NonEmpty = Annotated[str, AfterValidator(_non_empty)]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ModelId = Annotated[str, Field(pattern=MODEL_ID_PATTERN)]


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyCommand(Command):
    pass


class RealtimeSettings(Command):
    model: str = Field(max_length=256)
    hostapi: str = Field(max_length=256)
    input_device: str = Field(max_length=512)
    output_device: str = Field(max_length=512)
    pitch: int = Field(ge=-36, le=36)
    f0: Literal["rmvpe", "fcpe", "pm"]
    index_rate: float = Field(ge=0, le=1)
    rms_mix_rate: float = Field(ge=0, le=1)
    vad_threshold: float = Field(ge=0.1, le=0.9)
    input_gate_db: float = Field(ge=-60, le=-20)
    block_seconds: Literal[0.25, 0.5, 1.0]
    test_mode: bool


class SettingsUpdateCommand(Command):
    language: Literal["zh-CN", "en"] | None = None
    realtime: RealtimeSettings | None = None

    @model_validator(mode="after")
    def require_change(self) -> SettingsUpdateCommand:
        if self.language is None and self.realtime is None:
            raise ValueError("at least one setting must be provided")
        return self


class RuntimeInstallCommand(Command):
    rvc_root: AbsolutePath | None = None
    rvc_python: AbsolutePath | None = None
    install_separation: bool = False
    install_speaker_model: bool = True


class ModelScanCommand(Command):
    weight_roots: list[AbsolutePath] = Field(default_factory=list)
    index_roots: list[AbsolutePath] = Field(default_factory=list)
    remember_roots: bool = False


class ModelResolveCommand(Command):
    voice: NonEmpty


class ModelRecommendedParameters(Command):
    pitch: int = Field(ge=-36, le=36)
    f0: Literal["rmvpe", "fcpe", "pm"]
    index_rate: float = Field(ge=0, le=1)
    rms_mix_rate: float = Field(ge=0, le=1)
    protect: float = Field(ge=0, le=0.5)
    content_mode: Literal["clean", "mixed", "singing"]


class ModelImportCommand(Command):
    model: NonEmpty
    index: AbsolutePath | None = None
    index_url: HttpsUrl | None = None
    id: ModelId | None = None
    display_name: NonEmpty | None = None
    aliases: list[NonEmpty] = Field(default_factory=list)
    license_spdx: NonEmpty | None = None
    source_url: HttpsUrl | None = None
    model_sha256: Sha256 | None = None
    index_sha256: Sha256 | None = None
    download_size_bytes: int | None = Field(default=None, gt=0)
    index_size_bytes: int | None = Field(default=None, gt=0)
    recommended: ModelRecommendedParameters | None = None

    @model_validator(mode="after")
    def validate_source(self) -> ModelImportCommand:
        is_url = self.model.lower().startswith("https://")
        if not is_url and not Path(self.model).is_absolute():
            raise ValueError("model must be an absolute path or HTTPS URL")
        if self.index and self.index_url:
            raise ValueError("index and index_url cannot both be provided")
        if is_url:
            required = {
                "id": self.id,
                "display_name": self.display_name,
                "license_spdx": self.license_spdx,
                "model_sha256": self.model_sha256,
                "download_size_bytes": self.download_size_bytes,
            }
            missing = sorted(name for name, value in required.items() if value is None)
            if missing:
                raise ValueError(f"URL model imports require: {missing}")
            _https_url(self.model)
        if self.index_url and (self.index_sha256 is None or self.index_size_bytes is None):
            raise ValueError("URL index imports require index_sha256 and index_size_bytes")
        return self


class ModelCatalogInstallCommand(Command):
    catalog_url: HttpsUrl | None = None
    model_id: ModelId


class PresetListCommand(Command):
    model: NonEmpty | None = None


class ConversionParameters(Command):
    pitch: int | None = Field(default=None, ge=-36, le=36)
    f0: Literal["rmvpe", "fcpe", "pm"] | None = None
    index_rate: float | None = Field(default=None, ge=0, le=1)
    rms_mix_rate: float | None = Field(default=None, ge=0, le=1)
    protect: float | None = Field(default=None, ge=0, le=0.5)
    content_mode: Literal["clean", "mixed", "singing"] | None = None


class PresetSaveCommand(Command):
    model: NonEmpty
    name: NonEmpty
    parameters: ConversionParameters


class MediaInspectCommand(Command):
    input: AbsolutePath


class MediaAnalyzeCommand(Command):
    input: AbsolutePath
    input_sha256: Sha256 | None = None
    content_mode: Literal["clean", "mixed", "singing"] = "clean"


class RealtimeStartCommand(Command):
    model: NonEmpty
    input_device: int = Field(ge=0)
    output_device: int = Field(ge=0)
    pitch: int = Field(default=0, ge=-36, le=36)
    f0: Literal["rmvpe", "fcpe", "pm"] = "rmvpe"
    index_rate: float = Field(default=0.72, ge=0, le=1)
    rms_mix_rate: float = Field(default=0.25, ge=0, le=1)
    vad_threshold: float = Field(default=0.55, ge=0.1, le=0.9)
    input_gate_db: float = Field(default=-40.0, ge=-60, le=-20)
    block_seconds: Literal[0.25, 0.5, 1.0] = 0.5
    test_mode: bool = False


class ConversionPreviewCommand(Command):
    input: AbsolutePath
    input_sha256: Sha256 | None = None
    model: NonEmpty
    variants: list[ConversionParameters] = Field(default_factory=lambda: [ConversionParameters()])
    start_seconds: float = Field(default=0, ge=0)
    duration_seconds: float = Field(default=15, ge=10, le=20)
    output_directory: AbsolutePath | None = None
    content_mode: Literal["clean", "mixed", "singing"] = "clean"

    @model_validator(mode="after")
    def validate_variants(self) -> ConversionPreviewCommand:
        if not 1 <= len(self.variants) <= 4:
            raise ValueError("variants must contain one to four parameter objects")
        return self


class ConversionRunCommand(Command):
    input: AbsolutePath
    input_sha256: Sha256 | None = None
    output: AbsolutePath
    model: NonEmpty
    pitch: int | None = Field(default=None, ge=-36, le=36)
    f0: Literal["rmvpe", "fcpe", "pm"] | None = None
    index_rate: float | None = Field(default=None, ge=0, le=1)
    rms_mix_rate: float | None = Field(default=None, ge=0, le=1)
    protect: float | None = Field(default=None, ge=0, le=0.5)
    content_mode: Literal["clean", "mixed", "singing"] = "clean"
    selected_speakers: list[NonEmpty] = Field(default_factory=list)
    analysis_manifest: AbsolutePath | None = None
    overlap_policy: Literal["skip", "convert"] = "convert"
    overwrite: bool = False


def _normalize_extension(value: str) -> str:
    value = value.strip().casefold()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", value):
        raise ValueError("extensions must use forms such as .wav or .mp4")
    return value


Extension = Annotated[str, AfterValidator(_normalize_extension)]


class BatchCreateCommand(Command):
    input_root: AbsolutePath
    output_root: AbsolutePath
    model: NonEmpty
    preset: ConversionParameters = Field(default_factory=ConversionParameters)
    preset_name: NonEmpty = "default"
    recursive: bool = True
    watch: bool = False
    extensions: list[Extension] = Field(default_factory=list)


class BatchIdCommand(Command):
    batch_id: NonEmpty


class BatchWatchCommand(BatchIdCommand):
    enabled: bool


class BatchListCommand(Command):
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = None


class StorageArchiveCommand(Command):
    destination_root: AbsolutePath
    older_than_days: int = Field(default=30, ge=1)
    task_ids: list[NonEmpty] | None = None
    confirm_source_removal: Literal[True]

    @model_validator(mode="after")
    def validate_task_ids(self) -> StorageArchiveCommand:
        if self.task_ids is not None and not self.task_ids:
            raise ValueError("task_ids must be a non-empty array when provided")
        return self


class TaskIdCommand(Command):
    task_id: NonEmpty


class TaskListCommand(Command):
    limit: int = Field(default=200, ge=1, le=500)
    cursor: str | None = None


class TaskEventsCommand(Command):
    after_id: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2000)


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class JsonObject(RootModel[dict[str, Any]]):
    pass


class ObjectList(RootModel[list[dict[str, Any]]]):
    pass


class TaskRecord(ResultModel):
    id: str
    task_id: str
    operation: str
    state: str
    progress: float
    created_at: str
    updated_at: str


class TaskPage(ResultModel):
    items: list[TaskRecord]
    next_cursor: str | None


class ModelRecord(ResultModel):
    id: str
    display_name: str
    status: str


class ModelList(RootModel[list[ModelRecord]]):
    pass


class BatchRecord(ResultModel):
    id: str
    input_root: str
    output_root: str
    model_id: str
    state: str


class BatchPage(ResultModel):
    items: list[BatchRecord]
    next_cursor: str | None


class EventList(ResultModel):
    events: list[dict[str, Any]]


@dataclass(frozen=True, slots=True)
class OperationSpec:
    command: type[Command]
    long_running: bool = False
    result: type[BaseModel] = JsonObject


OPERATION_SPECS: dict[str, OperationSpec] = {
    "diagnostics.snapshot": OperationSpec(EmptyCommand, True),
    "settings.update": OperationSpec(SettingsUpdateCommand),
    "runtime.inspect": OperationSpec(EmptyCommand, True),
    "runtime.install": OperationSpec(RuntimeInstallCommand, True),
    "model.scan": OperationSpec(ModelScanCommand, True, ModelList),
    "model.list": OperationSpec(EmptyCommand, result=ModelList),
    "model.catalog.list": OperationSpec(EmptyCommand, result=ObjectList),
    "model.resolve": OperationSpec(ModelResolveCommand, result=ModelRecord),
    "model.import": OperationSpec(ModelImportCommand, True, ModelRecord),
    "model.catalog.install": OperationSpec(ModelCatalogInstallCommand, True, ModelRecord),
    "preset.list": OperationSpec(PresetListCommand, result=ObjectList),
    "preset.save": OperationSpec(PresetSaveCommand),
    "media.inspect": OperationSpec(MediaInspectCommand, True),
    "media.analyze": OperationSpec(MediaAnalyzeCommand, True),
    "realtime.devices": OperationSpec(EmptyCommand),
    "realtime.prepare": OperationSpec(RealtimeStartCommand),
    "realtime.start": OperationSpec(RealtimeStartCommand),
    "realtime.status": OperationSpec(EmptyCommand),
    "realtime.stop": OperationSpec(EmptyCommand),
    "conversion.preview": OperationSpec(ConversionPreviewCommand, True),
    "conversion.run": OperationSpec(ConversionRunCommand, True),
    "batch.create": OperationSpec(BatchCreateCommand, result=BatchRecord),
    "batch.get": OperationSpec(BatchIdCommand, result=BatchRecord),
    "batch.list": OperationSpec(BatchListCommand, result=BatchPage),
    "batch.run": OperationSpec(BatchIdCommand, True),
    "batch.retry": OperationSpec(BatchIdCommand, True),
    "batch.watch": OperationSpec(BatchWatchCommand, result=BatchRecord),
    "storage.archive": OperationSpec(StorageArchiveCommand, True),
    "task.list": OperationSpec(TaskListCommand, result=TaskPage),
    "task.events": OperationSpec(TaskEventsCommand, result=EventList),
    "task.get": OperationSpec(TaskIdCommand, result=TaskRecord),
    "task.cancel": OperationSpec(TaskIdCommand, result=TaskRecord),
    "task.retry": OperationSpec(TaskIdCommand, result=TaskRecord),
}


def parse_arguments(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
    spec = OPERATION_SPECS.get(operation)
    if spec is None:
        raise OperationError("unsupported_operation", f"unsupported operation: {operation}")
    command = spec.command.model_validate(arguments)
    return command.model_dump(mode="json", exclude_none=True)


def validate_completion_result(operation: str, result: Any) -> Any:
    spec = OPERATION_SPECS.get(operation)
    if spec is None:
        return result
    try:
        return spec.result.model_validate(result).model_dump(mode="json")
    except ValidationError as exc:
        raise OperationError(
            "invalid_result", f"{operation} produced a result that violates its contract"
        ) from exc


def validate_execute_result(operation: str, result: Any) -> Any:
    spec = OPERATION_SPECS[operation]
    if spec.long_running:
        try:
            return TaskRecord.model_validate(result).model_dump(mode="json")
        except ValidationError as exc:
            raise OperationError(
                "invalid_result",
                f"{operation} produced a task record that violates its contract",
            ) from exc
    return validate_completion_result(operation, result)


def describe() -> dict[str, Any]:
    operations = {}
    for name, spec in OPERATION_SPECS.items():
        operations[name] = {
            "long_running": spec.long_running,
            "arguments_schema": spec.command.model_json_schema(),
            "result_schema": spec.result.model_json_schema(),
        }
        if spec.long_running:
            operations[name]["submission_schema"] = TaskRecord.model_json_schema()
    return {
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION,
        "product": "VoxWeave",
        "product_version": __version__,
        "operations": operations,
    }


def success(request_id: str | None, result: Any) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": True,
        "result": result,
    }


def failure(request_id: str | None, error_type: str, error: str) -> dict[str, Any]:
    return {
        "protocol": PROTOCOL,
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "ok": False,
        "error_type": error_type,
        "error": error,
    }
