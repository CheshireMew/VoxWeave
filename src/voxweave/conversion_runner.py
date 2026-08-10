from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import soundfile as sf

from .artifacts import ArtifactStore
from .config import Settings
from .hashing import sha256_file
from .media_checkpoint import (
    _file_matches_record,
    _file_record,
    _load_resume_checkpoint,
    _publish_prepared_output,
    _verified_checkpoint_file,
    _write_checkpoint,
)
from .media_errors import MediaPipelineError
from .media_inputs import MediaInputResolver
from .media_io import (
    extract_audio,
    match_loudness,
    measure_audio_quality,
    mix_stems,
    mux_video,
    transcode_audio,
    validate_output,
    verify_media_snapshot,
)
from .media_processing import (
    align_audio_file,
    analyze_audio,
    convert_long_audio,
    convert_selected_segments,
    separate_audio,
)
from .model_registry import ModelRegistry
from .rvc_engine import RvcEngine
from .task_manager import TaskContext


@dataclass(slots=True)
class ConversionState:
    arguments: dict[str, Any]
    context: TaskContext
    source: Path
    output: Path
    overwrite: bool
    model: dict[str, Any]
    parameters: dict[str, Any]
    work_dir: Path
    source_media: dict[str, Any]
    content_mode: str
    selected_speakers: set[str]
    analysis_manifest: Path | None
    previous_checkpoint: dict[str, Any] | None
    checkpoint: dict[str, Any]
    checkpoint_path: Path
    segment_results: list[dict[str, Any]] = field(default_factory=list)


class ConversionRunner:
    def __init__(
        self,
        settings: Settings,
        models: ModelRegistry,
        artifacts: ArtifactStore,
        engine: RvcEngine,
        inputs: MediaInputResolver,
    ) -> None:
        self.settings = settings
        self.models = models
        self.artifacts = artifacts
        self.engine = engine
        self.inputs = inputs

    def _create_state(
        self,
        arguments: dict[str, Any],
        context: TaskContext,
    ) -> ConversionState:
        source = Path(arguments["input"]).expanduser().resolve()
        output = Path(arguments["output"]).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        model = self.inputs.model(arguments, context)
        parameters = {**model["recommended"], **arguments, "overwrite": False}
        work_dir = self.settings.artifacts_dir / context.task_id
        work_dir.mkdir(parents=True, exist_ok=False)
        source_media = self.inputs.inspect(source, arguments, context)
        content_mode = arguments.get("content_mode", "clean")
        selected_speakers = set(arguments.get("selected_speakers") or [])
        manifest_value = arguments.get("analysis_manifest")
        analysis_manifest = Path(manifest_value) if manifest_value else None
        analysis_hash = (
            sha256_file(analysis_manifest)
            if analysis_manifest and analysis_manifest.is_file()
            else None
        )
        signature = {
            "input_sha256": source_media["sha256"],
            "model_sha256": model["model_sha256"],
            "index_sha256": model.get("index_sha256"),
            "parameters": {
                key: parameters.get(key)
                for key in ("pitch", "f0", "index_rate", "rms_mix_rate", "protect")
            },
            "content_mode": content_mode,
            "selected_speakers": sorted(selected_speakers),
            "analysis_sha256": analysis_hash,
            "overlap_policy": arguments.get("overlap_policy", "convert"),
            "output_path": str(output),
        }
        previous = _load_resume_checkpoint(self.settings, context.retry_of, signature)
        checkpoint = {
            "protocol": "voxweave-conversion-checkpoint",
            "version": 1,
            "task_id": context.task_id,
            "resumed_from_task_id": context.retry_of,
            "signature": signature,
            "stages": {},
        }
        return ConversionState(
            arguments=arguments,
            context=context,
            source=source,
            output=output,
            overwrite=bool(arguments.get("overwrite", False)),
            model=model,
            parameters=parameters,
            work_dir=work_dir,
            source_media=source_media,
            content_mode=content_mode,
            selected_speakers=selected_speakers,
            analysis_manifest=analysis_manifest,
            previous_checkpoint=previous,
            checkpoint=checkpoint,
            checkpoint_path=work_dir / "checkpoint.json",
        )

    @staticmethod
    def _write_result_manifest(state: ConversionState, result: dict[str, Any]) -> dict[str, Any]:
        manifest = state.work_dir / "conversion-result.json"
        manifest.write_text(
            json.dumps(
                {key: value for key, value in result.items() if key != "manifest_path"},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        restored = dict(result)
        restored["manifest_path"] = str(manifest)
        return restored

    def _resume_publication(self, state: ConversionState) -> dict[str, Any] | None:
        publication = (state.previous_checkpoint or {}).get("stages", {}).get("publication")
        if not publication or not publication.get("result"):
            return None
        prepared = publication.get("prepared_output")
        if _file_matches_record(state.output, prepared):
            return self._write_result_manifest(state, publication["result"])
        prepared_path = _verified_checkpoint_file(prepared)
        if not prepared_path:
            return None
        if state.output.exists() and not state.overwrite:
            raise FileExistsError(state.output)
        prepared_path.replace(state.output)
        return self._write_result_manifest(state, publication["result"])

    def _prepare_source(self, state: ConversionState) -> Path:
        if state.output.exists() and not state.overwrite:
            raise FileExistsError(state.output)
        state.context.progress(0.05, "analyzing", "preparing source audio")
        previous_source = _verified_checkpoint_file(
            (state.previous_checkpoint or {}).get("stages", {}).get("source_audio")
        )
        if previous_source:
            source_audio = previous_source
            state.context.progress(0.08, "analyzing", "resumed verified source extraction")
        else:
            source_audio = state.work_dir / "source.wav"
            extract_audio(
                self.settings,
                state.source,
                source_audio,
                cancelled=state.context.cancelled,
            )
            verify_media_snapshot(state.source, state.source_media["sha256"])
        state.checkpoint["stages"]["source_audio"] = _file_record(source_audio)
        _write_checkpoint(state.checkpoint_path, state.checkpoint)
        return source_audio

    def _prepare_vocals(
        self,
        state: ConversionState,
        source_audio: Path,
    ) -> tuple[Path, Path | None, dict[str, Any] | None]:
        if state.content_mode not in {"mixed", "singing"}:
            return source_audio, None, None
        previous = (state.previous_checkpoint or {}).get("stages", {}).get("separation", {})
        vocal = _verified_checkpoint_file(previous.get("vocal"))
        instrumental = _verified_checkpoint_file(previous.get("instrumental"))
        if vocal and instrumental:
            metadata = previous.get("metadata")
            state.context.progress(0.18, "analyzing", "resumed verified source separation")
        else:
            state.context.progress(0.15, "analyzing", "separating vocals and instrumental")
            vocal, instrumental, metadata = separate_audio(
                self.settings,
                source_audio,
                state.work_dir / "stems",
                state.context.cancelled,
            )
        state.checkpoint["stages"]["separation"] = {
            "vocal": _file_record(vocal),
            "instrumental": _file_record(instrumental),
            "metadata": metadata,
        }
        _write_checkpoint(state.checkpoint_path, state.checkpoint)
        return vocal, instrumental, metadata

    def _convert_vocal(self, state: ConversionState, vocal: Path) -> Path:
        previous = (state.previous_checkpoint or {}).get("stages", {}).get("conversion", {})
        converted_vocal = _verified_checkpoint_file(previous.get("converted_vocal"))
        if converted_vocal:
            state.segment_results = previous.get("segments", [])
            state.context.progress(0.8, "converting", "resumed verified RVC conversion")
        elif state.selected_speakers:
            converted_vocal = state.work_dir / "converted-vocal.wav"
            analysis = self._speaker_analysis(state, vocal)
            state.segment_results = convert_selected_segments(
                self.engine,
                vocal,
                converted_vocal,
                state.model,
                state.parameters,
                analysis["segments"],
                state.selected_speakers,
                state.work_dir,
                state.context.progress,
                state.context.cancelled,
                state.arguments.get("overlap_policy", "convert"),
            )
        else:
            converted_vocal = self._convert_all_audio(state, vocal)
        state.checkpoint["stages"]["conversion"] = {
            "converted_vocal": _file_record(converted_vocal),
            "segments": state.segment_results,
        }
        _write_checkpoint(state.checkpoint_path, state.checkpoint)
        return converted_vocal

    def _speaker_analysis(self, state: ConversionState, vocal: Path) -> dict[str, Any]:
        if state.analysis_manifest:
            analysis = json.loads(state.analysis_manifest.read_text(encoding="utf-8"))
            if analysis.get("input", {}).get("sha256") != state.source_media["sha256"]:
                raise MediaPipelineError("analysis manifest does not match conversion input")
            if analysis.get("content_mode") != state.content_mode:
                raise MediaPipelineError("analysis manifest content mode does not match conversion")
            return analysis
        return analyze_audio(self.settings, vocal, state.work_dir, state.context.cancelled)

    def _convert_all_audio(self, state: ConversionState, vocal: Path) -> Path:
        converted_vocal = state.work_dir / "converted-vocal.wav"
        if sf.info(vocal).duration > 90:
            state.segment_results = convert_long_audio(
                self.engine,
                vocal,
                converted_vocal,
                state.model,
                state.parameters,
                state.work_dir,
                state.context.progress,
                state.context.cancelled,
            )
            return converted_vocal
        converted_raw = state.work_dir / "converted-vocal-raw.wav"
        engine_result = self.engine.convert(
            vocal,
            converted_raw,
            state.model,
            state.parameters,
            state.context.progress,
            cancelled=state.context.cancelled,
        )
        engine_result["aligned_output"] = align_audio_file(
            converted_raw,
            vocal,
            converted_vocal,
        )
        state.segment_results = [{"segment": "full", "conversion": engine_result}]
        return converted_vocal

    def _mix_and_match(
        self,
        state: ConversionState,
        source_audio: Path,
        converted_vocal: Path,
        instrumental: Path | None,
    ) -> tuple[Path, dict[str, Any]]:
        converted_mix = converted_vocal
        if instrumental:
            state.context.progress(0.82, "muxing", "mixing converted vocal with instrumental")
            converted_mix = state.work_dir / "converted-mix.wav"
            mix_stems(
                self.settings,
                converted_vocal,
                instrumental,
                converted_mix,
                state.context.cancelled,
            )
        previous = (state.previous_checkpoint or {}).get("stages", {}).get("loudness", {})
        previous_output = _verified_checkpoint_file(previous.get("output"))
        if previous_output:
            converted_mix = previous_output
            loudness = previous.get("metadata")
            state.context.progress(0.87, "muxing", "resumed verified loudness match")
        elif state.selected_speakers:
            quality = measure_audio_quality(
                self.settings,
                converted_mix,
                cancelled=state.context.cancelled,
            )
            loudness = {
                "mode": "selected-segments-preserved",
                "reference": measure_audio_quality(
                    self.settings,
                    state.source,
                    cancelled=state.context.cancelled,
                ),
                "before": quality,
                "after": quality,
                "output_path": str(converted_mix),
                "output_sha256": sha256_file(converted_mix),
            }
            state.context.progress(0.87, "muxing", "preserving unselected speaker intervals")
        else:
            state.context.progress(0.85, "muxing", "matching source loudness")
            matched = state.work_dir / "loudness-matched.wav"
            loudness = match_loudness(
                self.settings,
                state.source,
                source_audio,
                converted_mix,
                matched,
                state.work_dir,
                state.context.cancelled,
            )
            converted_mix = matched
        state.checkpoint["stages"]["loudness"] = {
            "output": _file_record(converted_mix),
            "metadata": loudness,
        }
        _write_checkpoint(state.checkpoint_path, state.checkpoint)
        return converted_mix, loudness

    def _prepare_result(
        self,
        state: ConversionState,
        converted_mix: Path,
        separation: dict[str, Any] | None,
        loudness: dict[str, Any],
    ) -> tuple[Path, dict[str, Any]]:
        state.context.progress(0.88, "muxing", "writing final media")
        prepared_output = state.output.parent / (
            f".{state.output.stem}.{state.context.task_id}.publishing{state.output.suffix}"
        )
        if prepared_output.exists():
            raise FileExistsError(prepared_output)
        if state.source_media["media_type"] == "video":
            mux_video(
                self.settings,
                state.source,
                converted_mix,
                prepared_output,
                False,
                state.context.cancelled,
            )
        else:
            transcode_audio(
                self.settings,
                converted_mix,
                prepared_output,
                False,
                state.context.cancelled,
            )
        state.context.progress(0.95, "validating", "fully decoding final output")
        output_media = validate_output(self.settings, prepared_output, state.context.cancelled)
        output_media["path"] = str(state.output)
        result = {
            "protocol": "voxweave-conversion-result",
            "version": 1,
            "input": state.source_media,
            "output": output_media,
            "model": {
                "id": state.model["id"],
                "display_name": state.model["display_name"],
                "model_sha256": state.model["model_sha256"],
                "index_sha256": state.model.get("index_sha256"),
            },
            "parameters": {
                key: state.parameters.get(key)
                for key in (
                    "pitch",
                    "f0",
                    "index_rate",
                    "rms_mix_rate",
                    "protect",
                    "content_mode",
                )
            },
            "selected_speakers": sorted(state.selected_speakers),
            "separation": separation,
            "loudness_match": loudness,
            "segments": state.segment_results,
        }
        return prepared_output, self._write_result_manifest(state, result)

    def _register_artifacts(self, state: ConversionState, result: dict[str, Any]) -> None:
        self.artifacts.register(state.context.task_id, "conversion-output", state.output)
        self.artifacts.register(
            state.context.task_id,
            "conversion-manifest",
            Path(result["manifest_path"]),
        )

    def run(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        state = self._create_state(arguments, context)
        resumed = self._resume_publication(state)
        if resumed is not None:
            self._register_artifacts(state, resumed)
            return resumed
        source_audio = self._prepare_source(state)
        vocal, instrumental, separation = self._prepare_vocals(state, source_audio)
        converted_vocal = self._convert_vocal(state, vocal)
        converted_mix, loudness = self._mix_and_match(
            state,
            source_audio,
            converted_vocal,
            instrumental,
        )
        prepared_output, result = self._prepare_result(
            state,
            converted_mix,
            separation,
            loudness,
        )
        verify_media_snapshot(state.source, state.source_media["sha256"])
        self.models.verify_snapshot(state.model)
        _publish_prepared_output(
            prepared_output,
            state.output,
            overwrite=state.overwrite,
            cancelled=context.cancelled,
            checkpoint=state.checkpoint,
            checkpoint_path=state.checkpoint_path,
            result=result,
        )
        self._register_artifacts(state, result)
        return result
