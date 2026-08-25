from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .capabilities import VIDEO_EXTENSIONS
from .config import Settings
from .hashing import FileVerificationLedger, VerifiedFile
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
    audio_geometry,
    extract_audio,
    match_loudness,
    measure_audio_quality,
    mix_stems,
    mux_video,
    process_audio_chain,
    transcode_audio,
    validate_output_verified,
    verify_media_snapshot,
)
from .media_processing import (
    align_audio_file,
    analyze_audio,
    convert_assigned_segments,
    convert_long_audio,
    convert_selected_segments,
    separate_audio,
)
from .model_registry import ModelRegistry
from .result_versions import ResultVersionRepository
from .rvc_engine import RvcEngine
from .task_manager import TaskContext


def output_keeps_video(source_media: dict[str, Any], output: Path) -> bool:
    return (
        source_media.get("media_type") == "video" and output.suffix.casefold() in VIDEO_EXTENSIONS
    )


@dataclass(slots=True)
class ConversionState:
    arguments: dict[str, Any]
    context: TaskContext
    source: Path
    output: Path
    overwrite: bool
    model: dict[str, Any] | None
    assignments: list[dict[str, Any]]
    parameters: dict[str, Any]
    work_dir: Path
    source_media: dict[str, Any]
    source_verified: VerifiedFile
    files: FileVerificationLedger
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
        self.results = ResultVersionRepository(artifacts.database)

    def _create_state(
        self,
        arguments: dict[str, Any],
        context: TaskContext,
    ) -> ConversionState:
        source = Path(arguments["input"]).expanduser().resolve()
        output = Path(arguments["output"]).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        files = FileVerificationLedger()
        model = self.inputs.model(arguments, context) if arguments.get("model") else None
        assignments = self.inputs.assigned_models(arguments, context)
        parameter_model = model or (assignments[0]["model"] if assignments else None)
        if parameter_model is None:
            raise MediaPipelineError("conversion has no executable voice model")
        parameters = {**parameter_model["recommended"], **arguments, "overwrite": False}
        work_dir = self.settings.artifacts_dir / context.task_id
        work_dir.mkdir(parents=True, exist_ok=False)
        source_media, source_verified = self.inputs.inspect_verified(
            source, arguments, context, files
        )
        content_mode = arguments.get("content_mode", "clean")
        selected_speakers = set(arguments.get("selected_speakers") or [])
        manifest_value = arguments.get("analysis_manifest")
        analysis_manifest = Path(manifest_value) if manifest_value else None
        analysis_hash = (
            files.verify(analysis_manifest, cancelled=context.cancelled).sha256
            if analysis_manifest and analysis_manifest.is_file()
            else None
        )
        signature = {
            "input_sha256": source_media["sha256"],
            "model_sha256": model["model_sha256"] if model else None,
            "index_sha256": model.get("index_sha256") if model else None,
            "assignments": [
                {
                    "segment_ids": sorted(assignment["segment_ids"]),
                    "model_id": assignment["model"]["id"],
                    "model_sha256": assignment["model"]["model_sha256"],
                    "index_sha256": assignment["model"].get("index_sha256"),
                    "parameters": assignment["parameters"],
                }
                for assignment in assignments
            ],
            "parameters": {
                key: parameters.get(key)
                for key in ("pitch", "f0", "index_rate", "rms_mix_rate", "protect")
            },
            "content_mode": content_mode,
            "selected_speakers": sorted(selected_speakers),
            "analysis_sha256": analysis_hash,
            "overlap_policy": arguments.get("overlap_policy", "convert"),
            "output_path": str(output),
            "processing_chain": arguments.get("processing_chain") or {},
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
            assignments=assignments,
            parameters=parameters,
            work_dir=work_dir,
            source_media=source_media,
            source_verified=source_verified,
            files=files,
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
            verify_media_snapshot(state.source, state.source_media["sha256"], state.source_verified)
        state.checkpoint["stages"]["source_audio"] = _file_record(source_audio, state.files)
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
            "vocal": _file_record(vocal, state.files),
            "instrumental": _file_record(instrumental, state.files),
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
        elif state.assignments:
            converted_vocal = state.work_dir / "converted-vocal.wav"
            analysis = self._speaker_analysis(state, vocal)
            state.segment_results = convert_assigned_segments(
                self.engine,
                vocal,
                converted_vocal,
                state.assignments,
                analysis["segments"],
                state.work_dir,
                state.context.progress,
                state.context.cancelled,
                state.arguments.get("overlap_policy", "convert"),
                self.settings,
                state.files,
            )
        elif state.selected_speakers:
            converted_vocal = state.work_dir / "converted-vocal.wav"
            analysis = self._speaker_analysis(state, vocal)
            if state.model is None:
                raise MediaPipelineError("selected-speaker conversion requires a model")
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
                self.settings,
                state.files,
            )
        else:
            converted_vocal = self._convert_all_audio(state, vocal)
        state.checkpoint["stages"]["conversion"] = {
            "converted_vocal": _file_record(converted_vocal, state.files),
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
        if state.model is None:
            raise MediaPipelineError("full-audio conversion requires a model")
        converted_vocal = state.work_dir / "converted-vocal.wav"
        if audio_geometry(self.settings, vocal, state.context.cancelled)["duration_seconds"] > 90:
            state.segment_results = convert_long_audio(
                self.engine,
                vocal,
                converted_vocal,
                state.model,
                state.parameters,
                state.work_dir,
                state.context.progress,
                state.context.cancelled,
                self.settings,
                state.files,
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
            ledger=state.files,
        )
        engine_result["aligned_output"] = align_audio_file(
            converted_raw,
            vocal,
            converted_vocal,
            self.settings,
            state.context.cancelled,
        )
        state.files.accept_record(engine_result["aligned_output"])
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
        elif state.selected_speakers or state.assignments:
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
                "output_sha256": state.files.verify(
                    converted_mix, cancelled=state.context.cancelled
                ).sha256,
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
                state.files,
            )
            converted_mix = matched
        state.checkpoint["stages"]["loudness"] = {
            "output": _file_record(converted_mix, state.files),
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
        processing_chain: dict[str, Any],
    ) -> tuple[Path, dict[str, Any], VerifiedFile]:
        state.context.progress(0.88, "muxing", "writing final media")
        prepared_output = state.output.parent / (
            f".{state.output.stem}.{state.context.task_id}.publishing{state.output.suffix}"
        )
        if prepared_output.exists():
            raise FileExistsError(prepared_output)
        if output_keeps_video(state.source_media, state.output):
            mux_video(
                self.settings,
                state.source,
                converted_mix,
                prepared_output,
                False,
                state.context.cancelled,
                state.source_media,
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
        output_media, output_verified = validate_output_verified(
            self.settings,
            prepared_output,
            state.context.cancelled,
            state.files,
        )
        output_media["path"] = str(state.output)
        result = {
            "protocol": "voxweave-conversion-result",
            "version": 1,
            "input": state.source_media,
            "output": output_media,
            "model": self._result_model(state),
            "parameters": {
                key: state.parameters.get(key)
                for key in (
                    "pitch",
                    "f0",
                    "index_rate",
                    "rms_mix_rate",
                    "protect",
                    "content_mode",
                    "processing_chain",
                )
            },
            "selected_speakers": sorted(state.selected_speakers),
            "assignments": [
                {
                    "segment_ids": list(assignment["segment_ids"]),
                    "model": {
                        "id": assignment["model"]["id"],
                        "display_name": assignment["model"]["display_name"],
                        "model_sha256": assignment["model"]["model_sha256"],
                        "index_sha256": assignment["model"].get("index_sha256"),
                    },
                    "parameters": assignment["parameters"],
                }
                for assignment in state.assignments
            ],
            "project": state.arguments.get("project"),
            "separation": separation,
            "loudness_match": loudness,
            "processing_chain": processing_chain,
            "segments": state.segment_results,
        }
        return prepared_output, self._write_result_manifest(state, result), output_verified

    def _process_output_chain(
        self, state: ConversionState, converted_mix: Path
    ) -> tuple[Path, dict[str, Any]]:
        chain = dict(state.arguments.get("processing_chain") or {})
        active = any(
            (
                chain.get("noise_reduction_db"),
                chain.get("dereverb_strength"),
                chain.get("highpass_hz"),
                chain.get("low_eq_db"),
                chain.get("presence_eq_db"),
                chain.get("compressor"),
                chain.get("deesser"),
                chain.get("target_lufs") is not None,
                chain.get("trim_silence"),
                float(chain.get("limiter_dbfs", -1)) != -1,
            )
        )
        if not active:
            return converted_mix, {"enabled": False, "settings": chain, "filters": []}
        state.context.progress(0.89, "mastering", "applying output processing chain")
        processed = state.work_dir / "processed-output.wav"
        metadata = process_audio_chain(
            self.settings,
            converted_mix,
            processed,
            chain,
            state.context.cancelled,
        )
        state.files.verify(processed, cancelled=state.context.cancelled)
        state.checkpoint["stages"]["processing_chain"] = {
            "output": _file_record(processed, state.files),
            "metadata": metadata,
        }
        _write_checkpoint(state.checkpoint_path, state.checkpoint)
        return processed, metadata

    @staticmethod
    def _result_model(state: ConversionState) -> dict[str, Any]:
        if state.model is not None:
            return {
                "id": state.model["id"],
                "display_name": state.model["display_name"],
                "model_sha256": state.model["model_sha256"],
                "index_sha256": state.model.get("index_sha256"),
            }
        unique: dict[str, dict[str, Any]] = {}
        for assignment in state.assignments:
            model = assignment["model"]
            unique[model["id"]] = {
                "id": model["id"],
                "display_name": model["display_name"],
                "model_sha256": model["model_sha256"],
                "index_sha256": model.get("index_sha256"),
            }
        return {
            "id": "multiple",
            "display_name": "Multiple voices",
            "models": list(unique.values()),
        }

    def _register_artifacts(self, state: ConversionState, result: dict[str, Any]) -> None:
        self.artifacts.register(
            state.context.task_id,
            "conversion-output",
            state.output,
            state.files.verify(state.output),
        )
        self.artifacts.register(
            state.context.task_id,
            "conversion-manifest",
            Path(result["manifest_path"]),
            state.files.verify(Path(result["manifest_path"])),
        )

    def run(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        state = self._create_state(arguments, context)
        resumed = self._resume_publication(state)
        if resumed is not None:
            self._register_artifacts(state, resumed)
            self.results.record(
                state.context.task_id,
                resumed,
                state.arguments,
            )
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
        converted_mix, processing_chain = self._process_output_chain(state, converted_mix)
        prepared_output, result, output_verified = self._prepare_result(
            state,
            converted_mix,
            separation,
            loudness,
            processing_chain,
        )
        verify_media_snapshot(state.source, state.source_media["sha256"], state.source_verified)
        if state.model is not None:
            self.models.verify_snapshot(state.model)
        for assignment in state.assignments:
            self.models.verify_snapshot(assignment["model"])
        _publish_prepared_output(
            prepared_output,
            state.output,
            overwrite=state.overwrite,
            cancelled=context.cancelled,
            checkpoint=state.checkpoint,
            checkpoint_path=state.checkpoint_path,
            result=result,
            verified=output_verified,
            ledger=state.files,
        )
        self._register_artifacts(state, result)
        self.results.record(
            state.context.task_id,
            result,
            state.arguments,
        )
        return result

    def rerun(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        version = self.results.get(str(arguments["version_id"]))
        original = dict(version.get("rerun_arguments") or {})
        if not original:
            raise MediaPipelineError(
                "this legacy result does not contain an exact rerun configuration"
            )
        source = Path(str(original.get("input") or "")).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(source)
        if original.get("input_sha256") and original["input_sha256"] != version["input_sha256"]:
            raise MediaPipelineError("stored rerun input identity is inconsistent")
        stored_model = dict(version.get("model") or {})
        model_snapshots = list(stored_model.get("models") or [stored_model])
        for snapshot in model_snapshots:
            model_id = str(snapshot.get("id") or "")
            expected_hash = str(snapshot.get("model_sha256") or "")
            if not model_id or not expected_hash:
                raise MediaPipelineError(
                    "this result does not contain a complete model snapshot for exact rerun"
                )
            current = self.models.resolve(model_id)
            if (
                current.get("archived")
                or current.get("status") != "ready"
                or current.get("model_sha256") != expected_hash
                or current.get("index_sha256") != snapshot.get("index_sha256")
            ):
                raise MediaPipelineError(
                    f"the exact model revision is no longer available: {model_id}"
                )
            self.models.verify_snapshot(current)
        requested_output = arguments.get("output")
        if requested_output:
            output = Path(str(requested_output)).expanduser().resolve()
        else:
            previous = Path(version["output_path"])
            output = previous.with_name(
                f"{previous.stem}-v{int(version['generation']) + 1}-"
                f"{context.task_id[:8]}{previous.suffix}"
            )
        command = {
            **original,
            "input_sha256": version["input_sha256"],
            "output": str(output),
            "overwrite": bool(arguments.get("overwrite", False)),
            "parent_version_id": version["id"],
        }
        return self.run(command, context)
