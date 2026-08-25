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
from .capabilities import CONTROL_PROTOCOL, CONTROL_PROTOCOL_VERSION
from .config import SettingsConflictError
from .hotkeys import parse_windows_hotkey
from .parameter_contracts import (
    BLOCK_SECONDS_SPEC,
    F0_SPEC,
    INDEX_RATE_SPEC,
    INPUT_GATE_DB_SPEC,
    PITCH_SPEC,
    PROTECT_SPEC,
    RMS_MIX_RATE_SPEC,
    TEST_MODE_SPEC,
    VAD_THRESHOLD_SPEC,
    BlockSeconds,
    F0Method,
)

PROTOCOL = CONTROL_PROTOCOL
PROTOCOL_VERSION = CONTROL_PROTOCOL_VERSION
SHA256_PATTERN = r"^[0-9a-fA-F]{64}$"
MODEL_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"


class OperationError(RuntimeError):
    """An expected operation failure with a stable public error code."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def public_error_code(error: Exception) -> str:
    if isinstance(error, SettingsConflictError):
        return "revision_conflict"
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
    pitch: int = Field(ge=PITCH_SPEC.minimum, le=PITCH_SPEC.maximum)
    f0: F0Method
    index_rate: float = Field(ge=INDEX_RATE_SPEC.minimum, le=INDEX_RATE_SPEC.maximum)
    rms_mix_rate: float = Field(
        ge=RMS_MIX_RATE_SPEC.minimum, le=RMS_MIX_RATE_SPEC.maximum
    )
    vad_threshold: float = Field(
        ge=VAD_THRESHOLD_SPEC.minimum, le=VAD_THRESHOLD_SPEC.maximum
    )
    input_gate_db: float = Field(
        ge=INPUT_GATE_DB_SPEC.minimum, le=INPUT_GATE_DB_SPEC.maximum
    )
    block_seconds: BlockSeconds
    test_mode: bool
    push_to_talk: bool


class RealtimeSettingsPatch(Command):
    model: str | None = Field(default=None, max_length=256)
    hostapi: str | None = Field(default=None, max_length=256)
    input_device: str | None = Field(default=None, max_length=512)
    output_device: str | None = Field(default=None, max_length=512)
    pitch: int | None = Field(default=None, ge=PITCH_SPEC.minimum, le=PITCH_SPEC.maximum)
    f0: F0Method | None = None
    index_rate: float | None = Field(
        default=None, ge=INDEX_RATE_SPEC.minimum, le=INDEX_RATE_SPEC.maximum
    )
    rms_mix_rate: float | None = Field(
        default=None, ge=RMS_MIX_RATE_SPEC.minimum, le=RMS_MIX_RATE_SPEC.maximum
    )
    vad_threshold: float | None = Field(
        default=None, ge=VAD_THRESHOLD_SPEC.minimum, le=VAD_THRESHOLD_SPEC.maximum
    )
    input_gate_db: float | None = Field(
        default=None, ge=INPUT_GATE_DB_SPEC.minimum, le=INPUT_GATE_DB_SPEC.maximum
    )
    block_seconds: BlockSeconds | None = None
    test_mode: bool | None = None
    push_to_talk: bool | None = None

    @model_validator(mode="after")
    def reject_explicit_nulls(self) -> RealtimeSettingsPatch:
        null_fields = sorted(
            name for name in self.model_fields_set if getattr(self, name) is None
        )
        if null_fields:
            raise ValueError(f"realtime settings cannot be null: {null_fields}")
        return self


class SettingsUpdateCommand(Command):
    expected_revision: int = Field(ge=0)
    language: Literal["zh-CN", "en"] | None = None
    realtime: RealtimeSettingsPatch | None = None

    @model_validator(mode="after")
    def require_change(self) -> SettingsUpdateCommand:
        if self.language is None and self.realtime is None:
            raise ValueError("at least one setting must be provided")
        return self


class SettingsEventsCommand(Command):
    after_revision: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=500)


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


class ModelArchiveCommand(Command):
    model_id: NonEmpty
    archived: bool = True


class ModelVerifyCommand(Command):
    model_id: NonEmpty


class ModelMetadataUpdateCommand(Command):
    model_id: NonEmpty
    expected_revision: int = Field(default=0, ge=0)
    custom_name: str | None = Field(default=None, max_length=256)
    tags: list[str] | None = Field(default=None, max_length=100)
    favorite: bool | None = None
    notes: str | None = Field(default=None, max_length=5000)
    sample_path: AbsolutePath | None = None
    cover_path: AbsolutePath | None = None

    @model_validator(mode="after")
    def require_change(self) -> ModelMetadataUpdateCommand:
        if all(
            value is None
            for value in (
                self.custom_name,
                self.tags,
                self.favorite,
                self.notes,
                self.sample_path,
                self.cover_path,
            )
        ):
            raise ValueError("model metadata update requires at least one changed field")
        if self.tags is not None:
            normalized = [tag.strip() for tag in self.tags if tag.strip()]
            if len(set(tag.casefold() for tag in normalized)) != len(normalized):
                raise ValueError("model metadata tags must be unique")
            self.tags = normalized
        return self


class ModelRecommendedParameters(Command):
    pitch: int = Field(ge=PITCH_SPEC.minimum, le=PITCH_SPEC.maximum)
    f0: F0Method
    index_rate: float = Field(ge=INDEX_RATE_SPEC.minimum, le=INDEX_RATE_SPEC.maximum)
    rms_mix_rate: float = Field(
        ge=RMS_MIX_RATE_SPEC.minimum, le=RMS_MIX_RATE_SPEC.maximum
    )
    protect: float = Field(ge=PROTECT_SPEC.minimum, le=PROTECT_SPEC.maximum)
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
    kind: Literal["conversion", "realtime"] | None = None
    include_archived: bool = False


class AudioProcessingChain(Command):
    noise_reduction_db: float = Field(default=0, ge=0, le=30)
    dereverb_strength: float = Field(default=0, ge=0, le=1)
    highpass_hz: int = Field(default=0, ge=0, le=400)
    low_eq_db: float = Field(default=0, ge=-12, le=12)
    presence_eq_db: float = Field(default=0, ge=-12, le=12)
    compressor: bool = False
    deesser: bool = False
    target_lufs: float | None = Field(default=None, ge=-24, le=-9)
    limiter_dbfs: float = Field(default=-1, ge=-3, le=-0.1)
    trim_silence: bool = False


class ConversionParameters(Command):
    pitch: int | None = Field(default=None, ge=PITCH_SPEC.minimum, le=PITCH_SPEC.maximum)
    f0: F0Method | None = None
    index_rate: float | None = Field(
        default=None, ge=INDEX_RATE_SPEC.minimum, le=INDEX_RATE_SPEC.maximum
    )
    rms_mix_rate: float | None = Field(
        default=None, ge=RMS_MIX_RATE_SPEC.minimum, le=RMS_MIX_RATE_SPEC.maximum
    )
    protect: float | None = Field(
        default=None, ge=PROTECT_SPEC.minimum, le=PROTECT_SPEC.maximum
    )
    content_mode: Literal["clean", "mixed", "singing"] | None = None
    processing_chain: AudioProcessingChain = Field(default_factory=AudioProcessingChain)


class RealtimeVoiceParameters(Command):
    pitch: int | None = Field(default=None, ge=PITCH_SPEC.minimum, le=PITCH_SPEC.maximum)
    f0: F0Method | None = None
    index_rate: float | None = Field(
        default=None, ge=INDEX_RATE_SPEC.minimum, le=INDEX_RATE_SPEC.maximum
    )
    rms_mix_rate: float | None = Field(
        default=None, ge=RMS_MIX_RATE_SPEC.minimum, le=RMS_MIX_RATE_SPEC.maximum
    )
    vad_threshold: float | None = Field(
        default=None, ge=VAD_THRESHOLD_SPEC.minimum, le=VAD_THRESHOLD_SPEC.maximum
    )
    input_gate_db: float | None = Field(
        default=None, ge=INPUT_GATE_DB_SPEC.minimum, le=INPUT_GATE_DB_SPEC.maximum
    )
    block_seconds: BlockSeconds | None = None
    test_mode: bool | None = None


class PresetSaveCommand(Command):
    model: NonEmpty
    name: NonEmpty
    kind: Literal["conversion", "realtime"] = "conversion"
    parameters: ConversionParameters | RealtimeVoiceParameters

    @model_validator(mode="after")
    def validate_parameter_kind(self) -> PresetSaveCommand:
        realtime = isinstance(self.parameters, RealtimeVoiceParameters)
        if realtime != (self.kind == "realtime"):
            raise ValueError("preset parameters must match preset kind")
        return self


class PresetUpdateCommand(Command):
    preset_id: NonEmpty
    expected_revision: int = Field(ge=1)
    name: NonEmpty | None = None
    parameters: ConversionParameters | RealtimeVoiceParameters | None = None

    @model_validator(mode="after")
    def require_change(self) -> PresetUpdateCommand:
        if self.name is None and self.parameters is None:
            raise ValueError("preset update requires name or parameters")
        return self


class PresetArchiveCommand(Command):
    preset_id: NonEmpty
    expected_revision: int = Field(ge=1)
    archived: bool = True


class PresetCopyCommand(Command):
    preset_id: NonEmpty
    name: NonEmpty


class PresetExportCommand(Command):
    preset_ids: list[NonEmpty] = Field(min_length=1, max_length=1000)


class PresetBundleItem(Command):
    model_id: NonEmpty
    model_sha256: Sha256
    name: NonEmpty
    kind: Literal["conversion", "realtime"]
    parameters: dict[str, Any]


class PresetImportCommand(Command):
    protocol: Literal["voxweave-preset-bundle"]
    version: Literal[1]
    presets: list[PresetBundleItem] = Field(min_length=1, max_length=1000)


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
    pitch: int = Field(
        default=PITCH_SPEC.default, ge=PITCH_SPEC.minimum, le=PITCH_SPEC.maximum
    )
    f0: F0Method = F0_SPEC.default
    index_rate: float = Field(
        default=INDEX_RATE_SPEC.default,
        ge=INDEX_RATE_SPEC.minimum,
        le=INDEX_RATE_SPEC.maximum,
    )
    rms_mix_rate: float = Field(
        default=RMS_MIX_RATE_SPEC.default,
        ge=RMS_MIX_RATE_SPEC.minimum,
        le=RMS_MIX_RATE_SPEC.maximum,
    )
    vad_threshold: float = Field(
        default=VAD_THRESHOLD_SPEC.default,
        ge=VAD_THRESHOLD_SPEC.minimum,
        le=VAD_THRESHOLD_SPEC.maximum,
    )
    input_gate_db: float = Field(
        default=INPUT_GATE_DB_SPEC.default,
        ge=INPUT_GATE_DB_SPEC.minimum,
        le=INPUT_GATE_DB_SPEC.maximum,
    )
    block_seconds: BlockSeconds = BLOCK_SECONDS_SPEC.default
    test_mode: bool = TEST_MODE_SPEC.default
    push_to_talk: bool = False
    recording: bool = False
    recording_directory: AbsolutePath | None = None


class RealtimeControlCommand(Command):
    bypass: bool | None = None
    muted: bool | None = None
    recording: bool | None = None
    push_to_talk_enabled: bool | None = None
    push_to_talk_pressed: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> RealtimeControlCommand:
        if all(
            value is None
            for value in (
                self.bypass,
                self.muted,
                self.recording,
                self.push_to_talk_enabled,
                self.push_to_talk_pressed,
            )
        ):
            raise ValueError("realtime control requires at least one changed field")
        return self


class RealtimeSceneSettings(Command):
    model: NonEmpty
    hostapi: str = Field(default="", max_length=256)
    input_device: NonEmpty
    output_device: NonEmpty
    pitch: int = Field(
        default=PITCH_SPEC.default, ge=PITCH_SPEC.minimum, le=PITCH_SPEC.maximum
    )
    f0: F0Method = F0_SPEC.default
    index_rate: float = Field(
        default=INDEX_RATE_SPEC.default,
        ge=INDEX_RATE_SPEC.minimum,
        le=INDEX_RATE_SPEC.maximum,
    )
    rms_mix_rate: float = Field(
        default=RMS_MIX_RATE_SPEC.default,
        ge=RMS_MIX_RATE_SPEC.minimum,
        le=RMS_MIX_RATE_SPEC.maximum,
    )
    vad_threshold: float = Field(
        default=VAD_THRESHOLD_SPEC.default,
        ge=VAD_THRESHOLD_SPEC.minimum,
        le=VAD_THRESHOLD_SPEC.maximum,
    )
    input_gate_db: float = Field(
        default=INPUT_GATE_DB_SPEC.default,
        ge=INPUT_GATE_DB_SPEC.minimum,
        le=INPUT_GATE_DB_SPEC.maximum,
    )
    block_seconds: BlockSeconds = BLOCK_SECONDS_SPEC.default
    test_mode: bool = TEST_MODE_SPEC.default
    push_to_talk: bool = False
    recording: bool = False


class RealtimeSceneHotkeys(Command):
    start_stop: str = Field(default="Ctrl+Alt+F9", max_length=64)
    bypass: str = Field(default="Ctrl+Alt+F10", max_length=64)
    mute: str = Field(default="Ctrl+Alt+F11", max_length=64)
    push_to_talk: str = Field(default="Ctrl+Alt+F12", max_length=64)

    @model_validator(mode="after")
    def validate_hotkeys(self) -> RealtimeSceneHotkeys:
        values = [self.start_stop, self.bypass, self.mute, self.push_to_talk]
        for value in values:
            parse_windows_hotkey(value)
        if len({value.casefold() for value in values}) != len(values):
            raise ValueError("realtime scene hotkeys must be unique")
        return self


class RealtimeSceneCreateCommand(Command):
    name: NonEmpty
    settings: RealtimeSceneSettings
    hotkeys: RealtimeSceneHotkeys = Field(default_factory=RealtimeSceneHotkeys)


class RealtimeSceneUpdateCommand(Command):
    scene_id: NonEmpty
    expected_revision: int = Field(ge=1)
    name: NonEmpty | None = None
    settings: RealtimeSceneSettings | None = None
    hotkeys: RealtimeSceneHotkeys | None = None

    @model_validator(mode="after")
    def require_scene_change(self) -> RealtimeSceneUpdateCommand:
        if self.name is None and self.settings is None and self.hotkeys is None:
            raise ValueError("scene update requires a change")
        return self


class RealtimeSceneIdCommand(Command):
    scene_id: NonEmpty


class RealtimeSceneArchiveCommand(RealtimeSceneIdCommand):
    expected_revision: int = Field(ge=1)
    archived: bool = True


class RealtimeSceneListCommand(Command):
    include_archived: bool = False


class RealtimeSceneApplyCommand(RealtimeSceneIdCommand):
    start: bool = True
    recording: bool | None = None


class RealtimeAudioTestCommand(Command):
    mode: Literal["input", "output"]
    device: int = Field(ge=0)
    duration_seconds: float = Field(default=2.0, ge=0.5, le=5.0)


class RealtimeCalibrationCommand(Command):
    input_device: int = Field(ge=0)
    output_device: int = Field(ge=0)
    duration_seconds: float = Field(default=3.0, ge=1.0, le=10.0)
    model: NonEmpty | None = None


class RealtimeRoutingTestCommand(Command):
    input_device: int = Field(ge=0)
    output_device: int = Field(ge=0)
    duration_seconds: float = Field(default=1.5, ge=1.0, le=5.0)


class RealtimeRecordingPromoteCommand(Command):
    session_id: NonEmpty
    project_name: NonEmpty
    output: AbsolutePath | None = None


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


class ModelCompareCommand(Command):
    input: AbsolutePath
    input_sha256: Sha256 | None = None
    models: list[NonEmpty] = Field(min_length=2, max_length=8)
    parameters: ConversionParameters = Field(default_factory=ConversionParameters)
    start_seconds: float = Field(default=0, ge=0)
    duration_seconds: float = Field(default=15, ge=10, le=20)
    output_directory: AbsolutePath | None = None
    content_mode: Literal["clean", "mixed", "singing"] = "clean"

    @model_validator(mode="after")
    def validate_models(self) -> ModelCompareCommand:
        if len(set(model.casefold() for model in self.models)) != len(self.models):
            raise ValueError("model comparison requires unique models")
        return self


class VoiceAssignment(Command):
    segment_ids: list[NonEmpty] = Field(min_length=1, max_length=10000)
    model: NonEmpty
    parameters: ConversionParameters = Field(default_factory=ConversionParameters)

    @model_validator(mode="after")
    def validate_segments(self) -> VoiceAssignment:
        if len(set(self.segment_ids)) != len(self.segment_ids):
            raise ValueError("voice assignment segment_ids must be unique")
        return self


class ConversionRunCommand(Command):
    input: AbsolutePath
    input_sha256: Sha256 | None = None
    output: AbsolutePath
    model: NonEmpty | None = None
    pitch: int | None = Field(default=None, ge=PITCH_SPEC.minimum, le=PITCH_SPEC.maximum)
    f0: F0Method | None = None
    index_rate: float | None = Field(
        default=None, ge=INDEX_RATE_SPEC.minimum, le=INDEX_RATE_SPEC.maximum
    )
    rms_mix_rate: float | None = Field(
        default=None, ge=RMS_MIX_RATE_SPEC.minimum, le=RMS_MIX_RATE_SPEC.maximum
    )
    protect: float | None = Field(
        default=None, ge=PROTECT_SPEC.minimum, le=PROTECT_SPEC.maximum
    )
    content_mode: Literal["clean", "mixed", "singing"] = "clean"
    selected_speakers: list[NonEmpty] = Field(default_factory=list)
    assignments: list[VoiceAssignment] = Field(default_factory=list, max_length=10000)
    analysis_manifest: AbsolutePath | None = None
    overlap_policy: Literal["skip", "convert"] = "convert"
    overwrite: bool = False
    processing_chain: AudioProcessingChain = Field(default_factory=AudioProcessingChain)

    @model_validator(mode="after")
    def validate_voice_selection(self) -> ConversionRunCommand:
        if not self.model and not self.assignments:
            raise ValueError("conversion requires model or voice assignments")
        if self.assignments and self.selected_speakers:
            raise ValueError("assignments and selected_speakers cannot be combined")
        if self.assignments and not self.analysis_manifest:
            raise ValueError("voice assignments require analysis_manifest")
        assigned: set[str] = set()
        for assignment in self.assignments:
            overlap = assigned.intersection(assignment.segment_ids)
            if overlap:
                raise ValueError(
                    f"segments cannot be assigned more than once: {sorted(overlap)}"
                )
            assigned.update(assignment.segment_ids)
        return self


class ProjectSegment(Command):
    id: NonEmpty
    start_seconds: float = Field(ge=0)
    end_seconds: float = Field(gt=0)
    speaker: NonEmpty
    speaker_similarity: float | None = None
    overlap: bool | Literal["unknown", "unresolved"] = False
    enabled: bool = True
    model: NonEmpty | None = None
    parameters: ConversionParameters = Field(default_factory=ConversionParameters)
    label: str = Field(default="", max_length=256)
    notes: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def validate_interval(self) -> ProjectSegment:
        if self.end_seconds <= self.start_seconds:
            raise ValueError("project segment end_seconds must be after start_seconds")
        return self


class ProjectDocument(Command):
    version: Literal[1] = 1
    default_model: NonEmpty | None = None
    default_parameters: ConversionParameters = Field(default_factory=ConversionParameters)
    overlap_policy: Literal["skip", "convert"] = "convert"
    duration_seconds: float = Field(default=0, ge=0)
    waveform_peaks: list[float] = Field(default_factory=list, max_length=2048)
    segments: list[ProjectSegment] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def validate_segment_ids(self) -> ProjectDocument:
        identifiers = [segment.id for segment in self.segments]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("project segment ids must be unique")
        return self


class ProjectCreateCommand(Command):
    name: NonEmpty
    input: AbsolutePath
    output: AbsolutePath | None = None
    content_mode: Literal["clean", "mixed", "singing"] = "clean"
    document: ProjectDocument = Field(default_factory=ProjectDocument)


class ProjectUpdateCommand(Command):
    project_id: NonEmpty
    expected_revision: int = Field(ge=1)
    name: NonEmpty | None = None
    input: AbsolutePath | None = None
    output: AbsolutePath | None = None
    content_mode: Literal["clean", "mixed", "singing"] | None = None
    document: ProjectDocument | None = None

    @model_validator(mode="after")
    def require_change(self) -> ProjectUpdateCommand:
        if all(
            value is None
            for value in (
                self.name,
                self.input,
                self.output,
                self.content_mode,
                self.document,
            )
        ):
            raise ValueError("project update requires at least one changed field")
        return self


class ProjectIdCommand(Command):
    project_id: NonEmpty


class ProjectListCommand(Command):
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = None
    include_archived: bool = False


class ProjectArchiveCommand(ProjectIdCommand):
    expected_revision: int = Field(ge=1)
    archived: bool = True


class ProjectRestoreCommand(ProjectIdCommand):
    expected_revision: int = Field(ge=1)
    revision: int = Field(ge=1)


class ProjectAnalyzeCommand(ProjectIdCommand):
    expected_revision: int = Field(ge=1)


class ProjectRunCommand(ProjectIdCommand):
    expected_revision: int = Field(ge=1)
    overwrite: bool = False


class ProjectPreviewCommand(ProjectIdCommand):
    expected_revision: int = Field(ge=1)
    segment_id: NonEmpty


def _normalize_extension(value: str) -> str:
    value = value.strip().casefold()
    if not re.fullmatch(r"\.[a-z0-9]{1,12}", value):
        raise ValueError("extensions must use forms such as .wav or .mp4")
    return value


Extension = Annotated[str, AfterValidator(_normalize_extension)]


class BatchVariant(Command):
    name: NonEmpty
    model: NonEmpty
    preset: ConversionParameters = Field(default_factory=ConversionParameters)
    preset_name: NonEmpty = "default"
    output_format: Literal["auto", "wav", "flac", "mp3"] = "auto"
    extensions: list[Extension] = Field(default_factory=list)
    include_globs: list[str] = Field(default_factory=list, max_length=50)
    exclude_globs: list[str] = Field(default_factory=list, max_length=50)


class BatchCreateCommand(Command):
    input_root: AbsolutePath
    output_root: AbsolutePath
    variants: list[BatchVariant] = Field(default_factory=list, max_length=32)
    model: NonEmpty | None = None
    preset: ConversionParameters = Field(default_factory=ConversionParameters)
    preset_name: NonEmpty = "default"
    recursive: bool = True
    watch: bool = False
    extensions: list[Extension] = Field(default_factory=list)
    naming_template: str = Field(
        default="{stem}_{source_ext}_{model}_{preset}_{hash}", min_length=1, max_length=256
    )
    preserve_structure: bool = True
    collision_policy: Literal["skip", "version", "overwrite"] = "skip"
    output_format: Literal["auto", "wav", "flac", "mp3"] = "auto"
    include_globs: list[str] = Field(default_factory=list, max_length=50)
    exclude_globs: list[str] = Field(default_factory=list, max_length=50)

    @model_validator(mode="before")
    @classmethod
    def migrate_single_model(cls, value: Any) -> Any:
        if not isinstance(value, dict) or value.get("variants"):
            return value
        model = value.get("model")
        if model:
            migrated = dict(value)
            migrated["variants"] = [
                {
                    "name": "default",
                    "model": model,
                    "preset": value.get("preset") or {},
                    "preset_name": value.get("preset_name") or "default",
                    "output_format": value.get("output_format") or "auto",
                }
            ]
            return migrated
        return value

    @model_validator(mode="after")
    def validate_naming_template(self) -> BatchCreateCommand:
        if not self.variants:
            raise ValueError("batch requires at least one output variant")
        names = [variant.name.casefold() for variant in self.variants]
        if len(set(names)) != len(names):
            raise ValueError("batch variant names must be unique")
        allowed = {"stem", "source_ext", "model", "preset", "variant", "hash"}
        fields = set(re.findall(r"\{([A-Za-z0-9_]+)\}", self.naming_template))
        unknown = fields - allowed
        if unknown:
            raise ValueError(f"unsupported naming template fields: {sorted(unknown)}")
        remainder = re.sub(r"\{[A-Za-z0-9_]+\}", "", self.naming_template)
        if "{" in remainder or "}" in remainder:
            raise ValueError("invalid naming template")
        return self


class BatchUpdateCommand(BatchCreateCommand):
    batch_id: NonEmpty


class BatchArchiveCommand(Command):
    batch_id: NonEmpty
    archived: bool = True


class BatchIdCommand(Command):
    batch_id: NonEmpty


class BatchItemRetryCommand(Command):
    item_id: NonEmpty
    variant: BatchVariant
    output: AbsolutePath | None = None


class BatchWatchCommand(BatchIdCommand):
    enabled: bool


class BatchListCommand(Command):
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = None


class StorageArchiveCommand(Command):
    destination_root: AbsolutePath
    older_than_days: int = Field(default=30, ge=1)
    task_ids: list[NonEmpty] | None = None
    states: list[Literal["completed", "failed", "cancelled", "interrupted"]] = Field(
        default_factory=lambda: ["completed", "failed", "cancelled", "interrupted"]
    )
    confirm_source_removal: Literal[True]

    @model_validator(mode="after")
    def validate_task_ids(self) -> StorageArchiveCommand:
        if self.task_ids is not None and not self.task_ids:
            raise ValueError("task_ids must be a non-empty array when provided")
        return self


class StorageInspectCommand(Command):
    older_than_days: int = Field(default=30, ge=1, le=3650)


class StorageRestoreCommand(Command):
    task_ids: list[NonEmpty] = Field(min_length=1, max_length=1000)


class StorageMigrationPlanCommand(Command):
    target_root: AbsolutePath


class StorageMigrationPrepareCommand(StorageMigrationPlanCommand):
    plan_digest: Sha256


class UpdateCheckCommand(Command):
    include_prerelease: bool = False


class UpdateDownloadCommand(Command):
    version: NonEmpty


class UpdateInstallCommand(Command):
    version: NonEmpty


class UpdateActivateCommand(Command):
    version: NonEmpty


class UpdateRollbackCommand(Command):
    version: NonEmpty | None = None


class TaskIdCommand(Command):
    task_id: NonEmpty


class TaskListCommand(Command):
    limit: int = Field(default=200, ge=1, le=500)
    cursor: str | None = None


class TaskEventsCommand(Command):
    after_id: int = Field(default=0, ge=0)
    limit: int = Field(default=500, ge=1, le=2000)


class ResultVersionIdCommand(Command):
    version_id: NonEmpty


class ResultVersionListCommand(Command):
    input_sha256: Sha256 | None = None
    project_id: NonEmpty | None = None
    favorites_only: bool = False
    limit: int = Field(default=100, ge=1, le=500)
    cursor: str | None = None


class ResultVersionUpdateCommand(ResultVersionIdCommand):
    label: str | None = Field(default=None, max_length=256)
    favorite: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> ResultVersionUpdateCommand:
        if self.label is None and self.favorite is None:
            raise ValueError("result version update requires label or favorite")
        return self


class ResultVersionRerunCommand(ResultVersionIdCommand):
    output: AbsolutePath | None = None
    overwrite: bool = False


class ResultModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SettingsPayload(ResultModel):
    data_root: str
    revision: int
    rvc_root: str | None
    rvc_python: str | None
    ffmpeg: str | None
    ffprobe: str | None
    language: Literal["zh-CN", "en"]
    hardware_backend: str
    separation_backend: str
    separation_model_id: str
    wespeaker_model: str | None
    weight_roots: list[str]
    index_roots: list[str]
    catalog_urls: list[str]
    realtime: RealtimeSettings
    telemetry_enabled: bool


class SettingsUpdateResult(ResultModel):
    revision: int
    changed_fields: list[str]
    settings: SettingsPayload


class SettingsEvent(ResultModel):
    revision: int
    changed_fields: list[str]
    settings: SettingsPayload
    created_at: str


class SettingsEventPage(ResultModel):
    events: list[SettingsEvent]
    current_revision: int


class RuntimeReport(ResultModel):
    platform: str
    python: str
    data_root: str
    rvc_root: str | None
    rvc_python: str | None
    ffmpeg: str | None
    ffprobe: str | None
    hardware_backend: str
    ready: bool
    rvc_revision: str | None
    doctor: dict[str, Any] | None
    components: dict[str, dict[str, Any]]
    pinned_rvc_revision: str
    pinned_asset_revision: str
    rvc_revision_matches_pin: bool
    error: str | None = None


class DiagnosticsReport(ResultModel):
    protocol: Literal["voxweave-diagnostics"]
    version: Literal[1]
    settings: SettingsPayload
    runtime: RuntimeReport
    models: list[dict[str, Any]]
    realtime: dict[str, Any]
    tasks: list[dict[str, Any]]
    events: list[dict[str, Any]]
    storage: dict[str, dict[str, int]]
    logs: list[dict[str, Any]]


class TaskRecord(ResultModel):
    id: str
    task_id: str
    operation: str
    state: str
    progress: float
    stage: str | None
    arguments: dict[str, Any]
    result: Any
    error_type: str | None
    error: str | None
    cancel_requested: bool
    request_id: str | None
    actor: dict[str, Any] | None
    snapshot: dict[str, Any]
    worker_failures: int
    retry_of: str | None
    created_at: str
    updated_at: str


class TaskSummary(ResultModel):
    id: str
    task_id: str
    operation: str
    state: str
    progress: float
    stage: str | None
    error_type: str | None
    error: str | None
    cancel_requested: bool
    request_id: str | None
    worker_failures: int
    retry_of: str | None
    created_at: str
    updated_at: str


class ArtifactRecord(ResultModel):
    id: str
    task_id: str
    kind: str
    path: str
    sha256: str
    size_bytes: int
    state: Literal["active", "archived", "missing"]
    archive_path: str | None
    created_at: str
    updated_at: str


class TaskDetail(TaskRecord):
    artifacts: list[ArtifactRecord]


class TaskPage(ResultModel):
    items: list[TaskSummary]
    next_cursor: str | None
    event_cursor: int = 0


class ModelRecord(ResultModel):
    id: str
    display_name: str
    aliases: list[str]
    family: str
    checkpoint_epoch: int | None
    model_path: str
    model_sha256: str
    index_path: str | None
    index_sha256: str | None
    index_candidates: list[str]
    rvc_version: str | None
    sample_rate: int | None
    f0: bool | None
    source_kind: str
    license_spdx: str | None
    source_url: str | None
    recommended: ModelRecommendedParameters
    status: str
    archived: bool
    imported_at: str
    custom_name: str | None = None
    tags: list[str] = Field(default_factory=list)
    favorite: bool = False
    notes: str = ""
    sample_path: str | None = None
    cover_path: str | None = None
    usage_count: int = 0
    last_used_at: str | None = None
    duplicate_model_ids: list[str] = Field(default_factory=list)
    integrity_status: Literal["unchecked", "verified", "missing", "changed", "error"] = (
        "unchecked"
    )
    integrity_checked_at: str | None = None
    integrity_error: str | None = None
    metadata_revision: int = 0
    protocol: Literal["voxweave-rvc-model"]
    version: Literal[1]


class ModelList(RootModel[list[ModelRecord]]):
    pass


class CatalogModelRecord(ResultModel):
    id: str
    display_name: str
    gender: str
    recommended: ModelRecommendedParameters
    aliases: list[str]
    license_spdx: str
    source_url: str
    model_url: str
    model_size_bytes: int
    model_sha256: str
    index_url: str | None = None
    index_size_bytes: int | None = None
    index_sha256: str | None = None
    installed: bool
    registered: bool
    available: bool
    archived: bool
    status: str
    repairable: bool
    starter: bool
    download_size_bytes: int


class CatalogModelList(RootModel[list[CatalogModelRecord]]):
    pass


class PresetRecord(ResultModel):
    id: str
    model_id: str
    name: str
    kind: Literal["conversion", "realtime"]
    model_sha256: str
    parameters: dict[str, Any]
    archived: bool
    revision: int
    created_at: str
    updated_at: str


class PresetListRecord(PresetRecord):
    needs_reconfirmation: bool


class PresetList(RootModel[list[PresetListRecord]]):
    pass


class PresetBundle(ResultModel):
    protocol: Literal["voxweave-preset-bundle"]
    version: Literal[1]
    presets: list[PresetBundleItem]


class ProjectRecord(ResultModel):
    id: str
    name: str
    input_path: str
    input_sha256: str | None
    output_path: str | None
    content_mode: Literal["clean", "mixed", "singing"]
    analysis_manifest: str | None
    analysis_sha256: str | None
    document: ProjectDocument
    state: Literal["active", "archived"]
    revision: int
    created_at: str
    updated_at: str


class ProjectSummary(ResultModel):
    id: str
    name: str
    input_path: str
    output_path: str | None
    content_mode: Literal["clean", "mixed", "singing"]
    state: Literal["active", "archived"]
    revision: int
    segment_count: int
    assigned_segment_count: int
    created_at: str
    updated_at: str


class ProjectPage(ResultModel):
    items: list[ProjectSummary]
    next_cursor: str | None


class ProjectRevisionRecord(ResultModel):
    project_id: str
    revision: int
    snapshot: dict[str, Any]
    created_at: str


class ProjectRevisionList(RootModel[list[ProjectRevisionRecord]]):
    pass


class BatchRecord(ResultModel):
    id: str
    input_root: str
    output_root: str
    model_id: str
    model_sha256: str | None
    index_sha256: str | None
    preset: dict[str, Any]
    preset_name: str
    recursive: bool
    watch_enabled: bool
    extensions: list[str]
    naming_template: str = "{stem}_{source_ext}_{model}_{preset}_{hash}"
    preserve_structure: bool = True
    collision_policy: Literal["skip", "version", "overwrite"] = "skip"
    output_format: Literal["auto", "wav", "flac", "mp3"] = "auto"
    include_globs: list[str] = Field(default_factory=list)
    exclude_globs: list[str] = Field(default_factory=list)
    variants: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    last_error: str | None
    last_error_at: str | None
    state: str
    revision: int
    created_at: str
    updated_at: str


class BatchSummary(BatchRecord):
    item_counts: dict[str, int]


class BatchPage(ResultModel):
    items: list[BatchSummary]
    next_cursor: str | None


class TaskEventRecord(ResultModel):
    id: int
    task_id: str
    state: str
    progress: float
    stage: str | None
    detail: str | None
    created_at: str


class EventList(ResultModel):
    events: list[TaskEventRecord]


class MediaRecord(ResultModel):
    path: str
    sha256: str
    size_bytes: int
    media_type: Literal["audio", "video"]
    duration_seconds: float
    format_name: str | None
    audio_streams: list[dict[str, Any]]
    video_streams: list[dict[str, Any]]
    subtitle_streams: list[dict[str, Any]]


class ValidatedMediaRecord(MediaRecord):
    full_decode: Literal["passed"] | None = None
    audio_quality: list[dict[str, Any]] = Field(default_factory=list)


class MediaAnalysisResult(ResultModel):
    input: MediaRecord
    content_mode: Literal["clean", "mixed", "singing"]
    vocal_audio: str
    instrumental_audio: str | None
    separation: dict[str, Any] | None
    speaker_samples: list[dict[str, Any]]
    speaker_count: int
    duration_seconds: float = 0
    waveform_peaks: list[float] = Field(default_factory=list)
    segments: list[dict[str, Any]]
    manifest_path: str
    note: str | None = None


class ConversionPreviewResult(ResultModel):
    model: ModelRecord
    source: str
    content_mode: Literal["clean", "mixed", "singing"]
    separation: dict[str, Any] | None
    outputs: list[dict[str, Any]]


class ModelComparisonResult(ResultModel):
    source: str
    content_mode: Literal["clean", "mixed", "singing"]
    separation: dict[str, Any] | None
    outputs: list[dict[str, Any]]


class ConversionResult(ResultModel):
    protocol: Literal["voxweave-conversion-result"]
    version: Literal[1]
    input: MediaRecord
    output: ValidatedMediaRecord
    model: dict[str, Any]
    parameters: ConversionParameters
    selected_speakers: list[str]
    assignments: list[dict[str, Any]] = Field(default_factory=list)
    project: dict[str, Any] | None = None
    separation: dict[str, Any] | None
    loudness_match: dict[str, Any]
    processing_chain: dict[str, Any] = Field(default_factory=dict)
    segments: list[dict[str, Any]]
    manifest_path: str


class AudioHostRecord(ResultModel):
    id: int
    name: str
    default_input_device: int
    default_output_device: int


class AudioDeviceRecord(ResultModel):
    id: int
    name: str
    hostapi_id: int
    hostapi: str
    input_channels: int
    output_channels: int
    default_sample_rate: int
    default_input: bool
    default_output: bool


class AudioDevicesResult(ResultModel):
    hostapis: list[AudioHostRecord]
    devices: list[AudioDeviceRecord]
    default_input_device: int
    default_output_device: int


class AudioTestResult(ResultModel):
    ok: Literal[True]
    command: Literal["audio-test"]
    mode: Literal["input", "output"]
    device: int
    sample_rate: int
    peak: float | None = None
    rms: float | None = None
    noise_floor_db: float | None = None
    signal_db: float | None = None
    snr_db: float | None = None
    pitch_hz_min: float | None = None
    pitch_hz_median: float | None = None
    pitch_hz_max: float | None = None
    voiced_fraction: float | None = None
    clip_ratio: float | None = None
    captured_frames: int | None = None
    expected_frames: int | None = None
    device_stability: float | None = None


class RealtimeCalibrationResult(ResultModel):
    input_device: int
    output_device: int
    measured_peak: float
    measured_rms: float
    measured_input_db: float
    recommended_input_gate_db: float
    recommended_vad_threshold: float
    latency_options_ms: dict[str, int]
    recommended_block_seconds: float
    noise_floor_db: float
    signal_db: float
    snr_db: float
    pitch_hz_min: float | None
    pitch_hz_median: float | None
    pitch_hz_max: float | None
    device_stability: float
    recommended_pitch: int
    recommended_index_rate: float


class RealtimeWorkerStatus(ResultModel):
    state: str
    pid: int | None
    model_id: str | None
    model_ready: bool


class RealtimeStatusResult(ResultModel):
    session_id: str | None
    state: str
    stage: str | None
    metrics: dict[str, Any]
    worker: RealtimeWorkerStatus
    id: str | None = None
    model_id: str | None = None
    model_sha256: str | None = None
    index_sha256: str | None = None
    arguments: dict[str, Any] | None = None
    error_type: str | None = None
    error: str | None = None
    started_at: str | None = None
    stopped_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class RealtimeSceneRecord(ResultModel):
    id: str
    name: str
    settings: RealtimeSceneSettings
    hotkeys: RealtimeSceneHotkeys
    archived: bool
    revision: int
    created_at: str
    updated_at: str


class RealtimeSceneList(ResultModel):
    items: list[RealtimeSceneRecord]


class RealtimeRoutingRecord(ResultModel):
    kind: Literal["direct", "virtual_input", "virtual_output", "mixer"]
    device_id: int
    name: str
    hostapi: str
    input_channels: int
    output_channels: int


class RealtimeRoutingResult(ResultModel):
    virtual_audio_available: bool
    detected_products: list[str]
    routes: list[RealtimeRoutingRecord]
    recommended_input_device: int | None
    recommended_output_device: int | None
    instructions: list[str]


class RealtimeRoutingTestResult(ResultModel):
    passed: bool
    input_device: int
    output_device: int
    sample_rate: int
    correlation: float
    latency_ms: float | None
    received_db: float
    detail: str


class RealtimeRecordingPromotionResult(ResultModel):
    session_id: str
    recording_manifest_path: str
    dry_path: str
    wet_path: str
    project: ProjectRecord


class BatchExecutionResult(ResultModel):
    batch: BatchRecord | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[dict[str, Any]] = Field(default_factory=list)
    batch_id: str | None = None
    retried: int | None = None
    items: list[dict[str, Any]] = Field(default_factory=list)
    item_count: int | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    submission_failures: list[dict[str, Any]] = Field(default_factory=list)


class BatchPlanResult(ResultModel):
    batch_id: str
    file_count: int
    output_count: int
    total_bytes: int
    collisions: int
    examples: list[dict[str, Any]]


class StorageArchiveResult(ResultModel):
    destination_root: str
    candidate_count: int
    archived_count: int
    archives: list[dict[str, Any]]


class StorageInspectResult(ResultModel):
    data_root: str
    total_bytes: int
    free_bytes: int
    areas: dict[str, dict[str, int]]
    reclaimable_task_count: int
    reclaimable_bytes: int
    archive_count: int
    categories: dict[str, dict[str, int]] = Field(default_factory=dict)
    migrations: list[dict[str, Any]] = Field(default_factory=list)


class StorageRestoreResult(ResultModel):
    requested_count: int
    restored_count: int
    archives: list[dict[str, Any]]


class StorageMigrationPlanResult(ResultModel):
    source_root: str
    target_root: str
    file_count: int
    total_bytes: int
    free_bytes: int
    plan_digest: str
    conflicts: list[str]


class StorageMigrationPrepareResult(StorageMigrationPlanResult):
    migration_id: str
    state: str
    manifest_path: str
    bootstrap_command: list[str]


class UpdateReleaseResult(ResultModel):
    current_version: str
    latest_version: str
    update_available: bool
    prerelease: bool
    release_name: str
    release_url: str
    published_at: str | None
    notes: str
    download_size_bytes: int
    downloaded_path: str | None = None
    sha256: str | None = None


class UpdateInstallationResult(ResultModel):
    version: str
    state: Literal["installed", "pending", "active", "rolled_back", "failed"]
    install_path: str
    executable_path: str
    archive_path: str
    sha256: str
    previous_version: str | None = None
    bootstrap_command: list[str] = Field(default_factory=list)
    error: str | None = None


class ResultVersionRecord(ResultModel):
    id: str
    task_id: str
    project_id: str | None
    project_revision: int | None
    input_path: str
    input_sha256: str
    output_path: str
    output_sha256: str
    model: dict[str, Any]
    parameters: dict[str, Any]
    result: dict[str, Any]
    parent_id: str | None
    root_id: str
    generation: int
    rerun_arguments: dict[str, Any]
    differences: dict[str, Any]
    children: list[str] = Field(default_factory=list)
    label: str
    favorite: bool
    created_at: str


class ResultVersionPage(ResultModel):
    items: list[ResultVersionRecord]
    next_cursor: str | None


@dataclass(frozen=True, slots=True)
class OperationSpec:
    command: type[Command]
    result: type[BaseModel]
    long_running: bool = False
    mutating: bool = False


OPERATION_SPECS: dict[str, OperationSpec] = {
    "diagnostics.snapshot": OperationSpec(
        EmptyCommand, DiagnosticsReport, long_running=True
    ),
    "settings.get": OperationSpec(EmptyCommand, SettingsPayload),
    "settings.update": OperationSpec(
        SettingsUpdateCommand, SettingsUpdateResult, mutating=True
    ),
    "settings.events": OperationSpec(SettingsEventsCommand, SettingsEventPage),
    "runtime.inspect": OperationSpec(EmptyCommand, RuntimeReport, long_running=True),
    "runtime.install": OperationSpec(
        RuntimeInstallCommand, RuntimeReport, long_running=True
    ),
    "model.scan": OperationSpec(ModelScanCommand, ModelList, long_running=True),
    "model.list": OperationSpec(EmptyCommand, ModelList),
    "model.catalog.list": OperationSpec(EmptyCommand, CatalogModelList),
    "model.resolve": OperationSpec(ModelResolveCommand, ModelRecord),
    "model.archive": OperationSpec(ModelArchiveCommand, ModelRecord, mutating=True),
    "model.verify": OperationSpec(ModelVerifyCommand, ModelRecord, long_running=True),
    "model.metadata.update": OperationSpec(
        ModelMetadataUpdateCommand, ModelRecord, mutating=True
    ),
    "model.import": OperationSpec(ModelImportCommand, ModelRecord, long_running=True),
    "model.catalog.install": OperationSpec(
        ModelCatalogInstallCommand, ModelRecord, long_running=True
    ),
    "preset.list": OperationSpec(PresetListCommand, PresetList),
    "preset.save": OperationSpec(PresetSaveCommand, PresetRecord, mutating=True),
    "preset.update": OperationSpec(PresetUpdateCommand, PresetRecord, mutating=True),
    "preset.archive": OperationSpec(PresetArchiveCommand, PresetRecord, mutating=True),
    "preset.copy": OperationSpec(PresetCopyCommand, PresetRecord, mutating=True),
    "preset.export": OperationSpec(PresetExportCommand, PresetBundle),
    "preset.import": OperationSpec(PresetImportCommand, PresetList, mutating=True),
    "media.inspect": OperationSpec(MediaInspectCommand, MediaRecord, long_running=True),
    "media.analyze": OperationSpec(
        MediaAnalyzeCommand, MediaAnalysisResult, long_running=True
    ),
    "realtime.devices": OperationSpec(EmptyCommand, AudioDevicesResult),
    "realtime.audio_test": OperationSpec(
        RealtimeAudioTestCommand, AudioTestResult, mutating=True
    ),
    "realtime.calibrate": OperationSpec(
        RealtimeCalibrationCommand, RealtimeCalibrationResult, mutating=True
    ),
    "realtime.prepare": OperationSpec(
        RealtimeStartCommand, RealtimeStatusResult, mutating=True
    ),
    "realtime.start": OperationSpec(
        RealtimeStartCommand, RealtimeStatusResult, mutating=True
    ),
    "realtime.control": OperationSpec(
        RealtimeControlCommand, RealtimeStatusResult, mutating=True
    ),
    "realtime.status": OperationSpec(EmptyCommand, RealtimeStatusResult),
    "realtime.stop": OperationSpec(
        EmptyCommand, RealtimeStatusResult, mutating=True
    ),
    "realtime.release": OperationSpec(
        EmptyCommand, RealtimeStatusResult, mutating=True
    ),
    "realtime.routing.inspect": OperationSpec(EmptyCommand, RealtimeRoutingResult),
    "realtime.routing.test": OperationSpec(
        RealtimeRoutingTestCommand, RealtimeRoutingTestResult, mutating=True
    ),
    "realtime.recording.promote": OperationSpec(
        RealtimeRecordingPromoteCommand,
        RealtimeRecordingPromotionResult,
        mutating=True,
    ),
    "realtime.scene.create": OperationSpec(
        RealtimeSceneCreateCommand, RealtimeSceneRecord, mutating=True
    ),
    "realtime.scene.update": OperationSpec(
        RealtimeSceneUpdateCommand, RealtimeSceneRecord, mutating=True
    ),
    "realtime.scene.archive": OperationSpec(
        RealtimeSceneArchiveCommand, RealtimeSceneRecord, mutating=True
    ),
    "realtime.scene.get": OperationSpec(RealtimeSceneIdCommand, RealtimeSceneRecord),
    "realtime.scene.list": OperationSpec(RealtimeSceneListCommand, RealtimeSceneList),
    "realtime.scene.apply": OperationSpec(
        RealtimeSceneApplyCommand, RealtimeStatusResult, mutating=True
    ),
    "conversion.preview": OperationSpec(
        ConversionPreviewCommand, ConversionPreviewResult, long_running=True
    ),
    "model.compare": OperationSpec(
        ModelCompareCommand, ModelComparisonResult, long_running=True
    ),
    "conversion.run": OperationSpec(
        ConversionRunCommand, ConversionResult, long_running=True
    ),
    "project.create": OperationSpec(ProjectCreateCommand, ProjectRecord, mutating=True),
    "project.update": OperationSpec(ProjectUpdateCommand, ProjectRecord, mutating=True),
    "project.archive": OperationSpec(ProjectArchiveCommand, ProjectRecord, mutating=True),
    "project.get": OperationSpec(ProjectIdCommand, ProjectRecord),
    "project.list": OperationSpec(ProjectListCommand, ProjectPage),
    "project.history": OperationSpec(ProjectIdCommand, ProjectRevisionList),
    "project.restore": OperationSpec(ProjectRestoreCommand, ProjectRecord, mutating=True),
    "project.analyze": OperationSpec(
        ProjectAnalyzeCommand, ProjectRecord, long_running=True
    ),
    "project.run": OperationSpec(ProjectRunCommand, ConversionResult, long_running=True),
    "project.preview": OperationSpec(
        ProjectPreviewCommand, ConversionPreviewResult, long_running=True
    ),
    "batch.create": OperationSpec(BatchCreateCommand, BatchRecord, mutating=True),
    "batch.update": OperationSpec(BatchUpdateCommand, BatchRecord, mutating=True),
    "batch.archive": OperationSpec(BatchArchiveCommand, BatchRecord, mutating=True),
    "batch.get": OperationSpec(BatchIdCommand, BatchRecord),
    "batch.list": OperationSpec(BatchListCommand, BatchPage),
    "batch.run": OperationSpec(BatchIdCommand, BatchExecutionResult, long_running=True),
    "batch.plan": OperationSpec(BatchIdCommand, BatchPlanResult, long_running=True),
    "batch.retry": OperationSpec(BatchIdCommand, BatchExecutionResult, long_running=True),
    "batch.item.retry": OperationSpec(
        BatchItemRetryCommand, BatchExecutionResult, mutating=True
    ),
    "batch.watch": OperationSpec(BatchWatchCommand, BatchRecord, mutating=True),
    "storage.archive": OperationSpec(
        StorageArchiveCommand, StorageArchiveResult, long_running=True
    ),
    "storage.inspect": OperationSpec(
        StorageInspectCommand, StorageInspectResult, long_running=True
    ),
    "storage.restore": OperationSpec(
        StorageRestoreCommand, StorageRestoreResult, long_running=True
    ),
    "storage.migration.plan": OperationSpec(
        StorageMigrationPlanCommand, StorageMigrationPlanResult, long_running=True
    ),
    "storage.migration.prepare": OperationSpec(
        StorageMigrationPrepareCommand,
        StorageMigrationPrepareResult,
        mutating=True,
    ),
    "update.check": OperationSpec(
        UpdateCheckCommand, UpdateReleaseResult, long_running=True
    ),
    "update.download": OperationSpec(
        UpdateDownloadCommand, UpdateReleaseResult, long_running=True, mutating=True
    ),
    "update.install": OperationSpec(
        UpdateInstallCommand, UpdateInstallationResult, long_running=True
    ),
    "update.activate": OperationSpec(
        UpdateActivateCommand, UpdateInstallationResult, mutating=True
    ),
    "update.rollback": OperationSpec(
        UpdateRollbackCommand, UpdateInstallationResult, mutating=True
    ),
    "result.get": OperationSpec(ResultVersionIdCommand, ResultVersionRecord),
    "result.list": OperationSpec(ResultVersionListCommand, ResultVersionPage),
    "result.update": OperationSpec(
        ResultVersionUpdateCommand, ResultVersionRecord, mutating=True
    ),
    "result.rerun": OperationSpec(
        ResultVersionRerunCommand, ConversionResult, long_running=True
    ),
    "task.list": OperationSpec(TaskListCommand, TaskPage),
    "task.events": OperationSpec(TaskEventsCommand, EventList),
    "task.get": OperationSpec(TaskIdCommand, TaskDetail),
    "task.cancel": OperationSpec(TaskIdCommand, TaskRecord, mutating=True),
    "task.retry": OperationSpec(TaskIdCommand, TaskRecord, mutating=True),
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
        return spec.result.model_validate(result).model_dump(
            mode="json", exclude_unset=True
        )
    except ValidationError as exc:
        raise OperationError(
            "invalid_result", f"{operation} produced a result that violates its contract"
        ) from exc


def validate_execute_result(operation: str, result: Any) -> Any:
    spec = OPERATION_SPECS[operation]
    if spec.long_running:
        try:
            return TaskRecord.model_validate(result).model_dump(mode="json", exclude_unset=True)
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
            "mutating": spec.mutating or spec.long_running,
            "request_id_required": spec.mutating or spec.long_running,
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
