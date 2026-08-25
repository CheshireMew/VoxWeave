from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .artifacts import ArtifactStore
from .config import Settings
from .conversion_runner import ConversionRunner
from .media_inputs import MediaInputResolver
from .media_io import (
    clip_audio,
    extract_audio,
    match_loudness,
    mix_stems,
    process_audio_chain,
    validate_output,
    verify_media_snapshot,
)
from .media_processing import (
    align_audio_file,
    analyze_audio,
    create_speaker_samples,
    separate_audio,
)
from .model_registry import ModelRegistry
from .rvc_engine import RvcEngine
from .task_manager import TaskContext

Progress = Callable[[float, str, str | None], None]


def _processing_chain_active(chain: dict[str, Any]) -> bool:
    return any(
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


def _public_model(model: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in model.items() if not key.startswith("__")}


class MediaPipeline:
    def __init__(
        self, settings: Settings, registry: ModelRegistry, artifacts: ArtifactStore
    ):
        self.settings = settings
        self.registry = registry
        self.artifacts = artifacts
        self.engine = RvcEngine(settings)
        self.inputs = MediaInputResolver(settings, registry)
        self.conversion = ConversionRunner(
            settings,
            registry,
            artifacts,
            self.engine,
            self.inputs,
        )

    def release_engine(self) -> None:
        self.engine.release_offline()

    def shutdown(self) -> None:
        self.engine.shutdown()

    def inspect(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        return self.inputs.inspect(Path(arguments["input"]), arguments, context)

    def analyze(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        task_id = context.task_id
        work_dir = self.settings.artifacts_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=False)
        source = Path(arguments["input"]).expanduser().resolve()
        source_media = self.inputs.inspect(source, arguments, context)
        content_mode = arguments.get("content_mode", "clean")
        context.progress(0.1, "analyzing", "extracting audio")
        audio = work_dir / "source.wav"
        extract_audio(self.settings, source, audio, cancelled=context.cancelled)
        if content_mode in {"mixed", "singing"}:
            context.progress(0.25, "analyzing", "separating vocals")
            vocal, instrumental, separation = separate_audio(
                self.settings, audio, work_dir / "stems", context.cancelled
            )
        else:
            vocal, instrumental, separation = audio, None, None
        context.progress(0.55, "analyzing", "detecting speech and speakers")
        analysis = (
            analyze_audio(self.settings, vocal, work_dir, context.cancelled)
            if content_mode != "singing"
            else {
                "speaker_count": 1,
                "segments": [],
                "note": "speaker clustering is disabled for singing",
            }
        )
        speaker_samples = (
            create_speaker_samples(
                self.settings, vocal, analysis["segments"], work_dir, context.cancelled
            )
            if analysis.get("segments")
            else []
        )
        result = {
            "input": source_media,
            "content_mode": content_mode,
            "vocal_audio": str(vocal),
            "instrumental_audio": str(instrumental) if instrumental else None,
            "separation": separation,
            "speaker_samples": speaker_samples,
            **analysis,
        }
        manifest = work_dir / "analysis.json"
        verify_media_snapshot(source, source_media["sha256"])
        manifest.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        result["manifest_path"] = str(manifest)
        self.artifacts.register(task_id, "analysis-manifest", manifest)
        for sample in speaker_samples:
            self.artifacts.register(task_id, "speaker-sample", Path(sample["sample_audio"]))
        context.progress(0.95, "validating", "analysis complete")
        return result

    def preview(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        variants = arguments.get("variants") or [{}]
        if not 1 <= len(variants) <= 4:
            raise ValueError("preview requires one to four variants")
        duration = float(arguments.get("duration_seconds", 15))
        if not 10 <= duration <= 20:
            raise ValueError("preview duration must be between 10 and 20 seconds")
        model = self.inputs.model(arguments, context)
        input_path = Path(arguments["input"])
        input_media = self.inputs.inspect(input_path, arguments, context)
        task_id = context.task_id
        work_dir = self.settings.artifacts_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=False)
        output_directory = (
            Path(arguments.get("output_directory") or (work_dir / "outputs")).expanduser().resolve()
        )
        output_directory.mkdir(parents=True, exist_ok=True)
        source = work_dir / "preview-source.wav"
        clip_audio(
            self.settings,
            input_path,
            source,
            float(arguments.get("start_seconds", 0)),
            duration,
            context.cancelled,
        )
        content_mode = arguments.get("content_mode", "clean")
        separation = None
        instrumental = None
        if content_mode in {"mixed", "singing"}:
            context.progress(0.1, "analyzing", "separating preview vocals and instrumental")
            vocal, instrumental, separation = separate_audio(
                self.settings, source, work_dir / "stems", context.cancelled
            )
        else:
            vocal = source
        outputs = []
        for index, variant in enumerate(variants):
            parameters = {**model["recommended"], **variant, "overwrite": False}
            chain = dict(parameters.get("processing_chain") or {})
            pitch = int(parameters.get("pitch", 0))
            output = output_directory / f"preview-{index + 1:02d}-{model['family']}-p{pitch:+d}.wav"
            variant_dir = work_dir / f"variant-{index + 1:02d}"
            variant_dir.mkdir(parents=True, exist_ok=False)
            converted_raw = variant_dir / "converted-raw.wav"
            engine_result = self.engine.convert(
                vocal, converted_raw, model, parameters, cancelled=context.cancelled
            )
            converted = variant_dir / "converted.wav"
            aligned = align_audio_file(converted_raw, vocal, converted)
            converted_mix = converted
            if instrumental:
                converted_mix = variant_dir / "converted-mix.wav"
                mix_stems(
                    self.settings, converted, instrumental, converted_mix, context.cancelled
                )
            loudness = match_loudness(
                self.settings,
                source,
                source,
                converted_mix,
                output if not _processing_chain_active(chain) else variant_dir / "loudness.wav",
                variant_dir,
                context.cancelled,
            )
            chain_metadata = {"enabled": False, "settings": chain}
            if _processing_chain_active(chain):
                chain_metadata = process_audio_chain(
                    self.settings,
                    Path(loudness["output_path"]),
                    output,
                    chain,
                    context.cancelled,
                )
            outputs.append(
                {
                    **engine_result,
                    "aligned_output": aligned,
                    "separation": separation,
                    "loudness_match": loudness,
                    "processing_chain": chain_metadata,
                    "output_path": str(output),
                    "media": validate_output(self.settings, output, context.cancelled),
                }
            )
            self.artifacts.register(task_id, "preview-output", output)
            context.progress(
                0.15 + 0.75 * ((index + 1) / len(variants)),
                "converting",
                f"variant {index + 1}/{len(variants)}",
            )
        verify_media_snapshot(input_path, input_media["sha256"])
        return {
            "model": _public_model(model),
            "source": str(source),
            "content_mode": content_mode,
            "separation": separation,
            "outputs": outputs,
        }

    def compare(self, arguments: dict[str, Any], context: TaskContext) -> dict[str, Any]:
        models = self.inputs.model_list(arguments, context)
        if not 2 <= len(models) <= 8:
            raise ValueError("model comparison requires two to eight models")
        input_path = Path(arguments["input"])
        input_media = self.inputs.inspect(input_path, arguments, context)
        task_id = context.task_id
        work_dir = self.settings.artifacts_dir / task_id
        work_dir.mkdir(parents=True, exist_ok=False)
        output_directory = Path(
            arguments.get("output_directory") or (work_dir / "outputs")
        ).expanduser().resolve()
        output_directory.mkdir(parents=True, exist_ok=True)
        source = work_dir / "comparison-source.wav"
        clip_audio(
            self.settings,
            input_path,
            source,
            float(arguments.get("start_seconds", 0)),
            float(arguments.get("duration_seconds", 15)),
            context.cancelled,
        )
        content_mode = arguments.get("content_mode", "clean")
        separation = None
        instrumental = None
        if content_mode in {"mixed", "singing"}:
            context.progress(0.08, "analyzing", "separating comparison source")
            vocal, instrumental, separation = separate_audio(
                self.settings, source, work_dir / "stems", context.cancelled
            )
        else:
            vocal = source
        requested_parameters = dict(arguments.get("parameters") or {})
        outputs = []
        for index, model in enumerate(models):
            parameters = {
                **model["recommended"],
                **requested_parameters,
                "overwrite": False,
            }
            chain = dict(parameters.get("processing_chain") or {})
            model_dir = work_dir / f"model-{index + 1:02d}"
            model_dir.mkdir(parents=True, exist_ok=False)
            converted_raw = model_dir / "converted-raw.wav"
            engine_result = self.engine.convert(
                vocal,
                converted_raw,
                model,
                parameters,
                cancelled=context.cancelled,
            )
            converted = model_dir / "converted.wav"
            aligned = align_audio_file(
                converted_raw,
                vocal,
                converted,
                self.settings,
                context.cancelled,
            )
            converted_mix = converted
            if instrumental:
                converted_mix = model_dir / "converted-mix.wav"
                mix_stems(
                    self.settings,
                    converted,
                    instrumental,
                    converted_mix,
                    context.cancelled,
                )
            output = output_directory / f"compare-{index + 1:02d}-{model['family']}.wav"
            loudness = match_loudness(
                self.settings,
                source,
                source,
                converted_mix,
                output if not _processing_chain_active(chain) else model_dir / "loudness.wav",
                model_dir,
                context.cancelled,
            )
            chain_metadata = {"enabled": False, "settings": chain}
            if _processing_chain_active(chain):
                chain_metadata = process_audio_chain(
                    self.settings,
                    Path(loudness["output_path"]),
                    output,
                    chain,
                    context.cancelled,
                )
            outputs.append(
                {
                    "model": _public_model(model),
                    "parameters": parameters,
                    **engine_result,
                    "aligned_output": aligned,
                    "loudness_match": loudness,
                    "processing_chain": chain_metadata,
                    "output_path": str(output),
                    "media": validate_output(self.settings, output, context.cancelled),
                }
            )
            self.artifacts.register(task_id, "comparison-output", output)
            context.progress(
                0.12 + 0.78 * ((index + 1) / len(models)),
                "converting",
                f"model {index + 1}/{len(models)}",
            )
        verify_media_snapshot(input_path, input_media["sha256"])
        for model in models:
            self.registry.verify_snapshot(model)
        return {
            "source": str(source),
            "content_mode": content_mode,
            "separation": separation,
            "outputs": outputs,
        }
