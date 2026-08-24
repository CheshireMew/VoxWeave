from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from .bounded_ids import BoundedIdSet
from .capabilities import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
from .gui_activity import TASK_TERMINAL_STATES, TaskActivity
from .gui_requests import RequestCoordinator
from .gui_support import local_path
from .gui_tasks import TaskFeed

AUDIO_SUFFIXES = frozenset(AUDIO_EXTENSIONS)
VIDEO_SUFFIXES = frozenset(VIDEO_EXTENSIONS)
MEDIA_SUFFIXES = frozenset(MEDIA_EXTENSIONS)


class MediaViewModel(QObject):
    resultAudioChanged = Signal()
    resultChanged = Signal()
    playbackRequested = Signal()
    speakersChanged = Signal()
    previewOutputsChanged = Signal()
    presetsChanged = Signal()

    def __init__(
        self,
        requests: RequestCoordinator,
        activity: TaskActivity,
        task_feed: TaskFeed,
        parent: QObject | None = None,
        *,
        status_callback: Callable[[str, str], None] | None = None,
        text_callback: Callable[[str], str] | None = None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.activity = activity
        self._status_callback = status_callback or getattr(
            requests, "status_callback", lambda *_args: None
        )
        self._text_callback = text_callback or (lambda key: key)
        self._result_audio = ""
        self._result_path = ""
        self._speakers: list[dict[str, Any]] = []
        self._preview_outputs: list[dict[str, Any]] = []
        self._presets: list[dict[str, Any]] = []
        self._analysis_task_id: str | None = None
        self._analysis_manifest: str | None = None
        self._analysis_input_path: str | None = None
        self._analysis_mode: str | None = None
        self._analysis_sha256: str | None = None
        self._preview_task_id: str | None = None
        self._conversion_task_id: str | None = None
        self._handled_audio_tasks = BoundedIdSet()
        self._latest_audio_task_created = ""
        task_feed.taskUpdated.connect(self._consume_task)

    @Property(str, notify=resultAudioChanged)
    def resultAudio(self) -> str:
        return QUrl.fromLocalFile(self._result_audio).toString() if self._result_audio else ""

    @Property(str, notify=resultAudioChanged)
    def resultAudioPath(self) -> str:
        return self._result_audio

    @Property(str, notify=resultChanged)
    def resultPath(self) -> str:
        return self._result_path

    @Property(bool, notify=resultChanged)
    def resultIsAudio(self) -> bool:
        return Path(self._result_path).suffix.casefold() in AUDIO_SUFFIXES

    @Slot(str, result=str)
    def localPath(self, value: str) -> str:
        return local_path(value)

    @Slot(str, result=str)
    def suggestOutput(self, input_value: str) -> str:
        input_path = Path(local_path(input_value)).expanduser()
        if not input_path.name:
            return ""
        suffix = input_path.suffix.casefold()
        if suffix not in MEDIA_SUFFIXES:
            suffix = ".wav"
        candidate = input_path.with_name(f"{input_path.stem}-voxweave{suffix}")
        number = 2
        while candidate.exists() or candidate == input_path:
            candidate = input_path.with_name(f"{input_path.stem}-voxweave-{number}{suffix}")
            number += 1
        return str(candidate)

    @Slot(str, str, result="QVariantMap")
    def validateConversion(self, input_value: str, output_value: str) -> dict[str, Any]:
        input_validation = self.validateInput(input_value)
        if not input_validation["valid"]:
            return {**input_validation, "suggestion": ""}
        input_path = Path(local_path(input_value)).expanduser()
        suggestion = self.suggestOutput(input_value)
        if not output_value.strip():
            return {"valid": False, "code": "output_required", "suggestion": suggestion}
        output_path = Path(local_path(output_value)).expanduser()
        if output_path.suffix.casefold() not in MEDIA_SUFFIXES:
            return {"valid": False, "code": "output_unsupported", "suggestion": suggestion}
        if output_path.resolve() == input_path.resolve():
            return {"valid": False, "code": "same_path", "suggestion": suggestion}
        if not output_path.parent.is_dir():
            return {"valid": False, "code": "output_parent_missing", "suggestion": suggestion}
        if output_path.exists():
            return {"valid": False, "code": "output_exists", "suggestion": suggestion}
        return {"valid": True, "code": "ready", "suggestion": suggestion}

    @Slot(str, result="QVariantMap")
    def validateInput(self, input_value: str) -> dict[str, Any]:
        if not input_value.strip():
            return {"valid": False, "code": "input_required"}
        input_path = Path(local_path(input_value)).expanduser()
        if not input_path.is_file():
            return {"valid": False, "code": "input_missing"}
        if input_path.suffix.casefold() not in MEDIA_SUFFIXES:
            return {"valid": False, "code": "input_unsupported"}
        return {"valid": True, "code": "ready"}

    @Property("QVariantList", notify=speakersChanged)
    def speakers(self) -> list[dict[str, Any]]:
        return self._speakers

    @Property("QVariantList", notify=previewOutputsChanged)
    def previewOutputs(self) -> list[dict[str, Any]]:
        return self._preview_outputs

    @Property("QVariantList", notify=presetsChanged)
    def presets(self) -> list[dict[str, Any]]:
        return self._presets

    @Slot(str, str)
    def analyze(self, input_value: str, mode: str) -> None:
        self._reset_analysis(cancel_active=True)
        input_path = local_path(input_value)

        def submitted(task: dict[str, Any]) -> None:
            self._analysis_task_id = task["task_id"]

        self.activity.submit(
            "media.analyze",
            {"input": input_path, "content_mode": mode},
            action_key="analysis",
            submitted=submitted,
        )

    def _reset_analysis(self, *, cancel_active: bool) -> None:
        if cancel_active and self._analysis_task_id:
            self.requests.submit(
                "task.cancel",
                {"task_id": self._analysis_task_id},
                show_status=False,
            )
        self.activity.abandon("analysis")
        self._analysis_task_id = None
        self._speakers = []
        self._analysis_manifest = None
        self._analysis_input_path = None
        self._analysis_mode = None
        self._analysis_sha256 = None
        self.speakersChanged.emit()

    @Slot()
    def invalidateAnalysis(self) -> None:
        self._reset_analysis(cancel_active=True)

    def _reset_results(self, *, cancel_active: bool) -> None:
        for task_id, request_key in (
            (self._preview_task_id, "preview"),
            (self._conversion_task_id, "conversion"),
        ):
            if cancel_active and task_id:
                self.requests.submit(
                    "task.cancel",
                    {"task_id": task_id},
                    show_status=False,
                )
            self.activity.abandon(request_key)
        self._preview_task_id = None
        self._conversion_task_id = None
        had_audio = bool(self._result_audio)
        had_result = bool(self._result_path)
        had_previews = bool(self._preview_outputs)
        self._result_audio = ""
        self._result_path = ""
        self._preview_outputs = []
        if had_audio:
            self.resultAudioChanged.emit()
        if had_result:
            self.resultChanged.emit()
        if had_previews:
            self.previewOutputsChanged.emit()

    @Slot()
    def invalidateResults(self) -> None:
        self._reset_results(cancel_active=True)

    def _known_input_sha256(self, input_path: str, mode: str) -> str | None:
        resolved = str(Path(input_path).expanduser().resolve())
        return (
            self._analysis_sha256
            if self._analysis_input_path == resolved and self._analysis_mode == mode
            else None
        )

    @Slot("QVariantMap")
    def convert(self, value: dict[str, Any]) -> None:
        self._reset_results(cancel_active=True)
        command = dict(value)
        input_value = str(command["input"])
        output_value = str(command["output"])
        model = str(command["model"])
        pitch = int(command["pitch"])
        f0 = str(command["f0"])
        index_rate = float(command["index_rate"])
        rms_mix_rate = float(command["rms_mix_rate"])
        protect = float(command["protect"])
        mode = str(command["content_mode"])
        selected_speakers = list(command.get("selected_speakers") or [])
        overlap_policy = str(command["overlap_policy"])
        input_path = local_path(input_value)
        input_sha256 = self._known_input_sha256(input_path, mode)
        arguments = {
            "input": input_path,
            "output": local_path(output_value),
            "model": model,
            "pitch": pitch,
            "f0": f0,
            "index_rate": index_rate,
            "rms_mix_rate": rms_mix_rate,
            "protect": protect,
            "content_mode": mode,
            "selected_speakers": selected_speakers,
            "analysis_manifest": self._analysis_manifest if selected_speakers else None,
            "overlap_policy": overlap_policy,
            "overwrite": False,
        }
        if input_sha256:
            arguments["input_sha256"] = input_sha256

        def submitted(task: dict[str, Any]) -> None:
            self._conversion_task_id = str(task["task_id"])

        self.activity.submit(
            "conversion.run",
            arguments,
            action_key="conversion",
            submitted=submitted,
        )

    @Slot("QVariantMap")
    def previewWithOptions(self, value: dict[str, Any]) -> None:
        command = dict(value)
        if self._preview_task_id:
            self.requests.submit(
                "task.cancel",
                {"task_id": self._preview_task_id},
                show_status=False,
            )
            self.activity.abandon("preview")
            self._preview_task_id = None
        self._submit_preview(
            str(command["input"]),
            str(command["model"]),
            int(command["pitch"]),
            str(command["f0"]),
            float(command["index_rate"]),
            float(command["rms_mix_rate"]),
            float(command["protect"]),
            str(command["content_mode"]),
            int(command.get("variant_count", 2)),
            int(command.get("pitch_step", 3)),
        )

    def _submit_preview(
        self,
        input_value: str,
        model: str,
        pitch: int,
        f0: str,
        index_rate: float,
        rms_mix_rate: float,
        protect: float,
        mode: str,
        variant_count: int,
        pitch_step: int,
    ) -> None:
        input_path = local_path(input_value)
        count = max(1, min(4, variant_count))
        step = pitch_step or 3
        if not -36 <= pitch + (count - 1) * step <= 36:
            reversed_step = -step
            if -36 <= pitch + (count - 1) * reversed_step <= 36:
                step = reversed_step
        pitches = [max(-36, min(36, pitch + index * step)) for index in range(count)]

        def submitted(task: dict[str, Any]) -> None:
            self._preview_task_id = task["task_id"]
            self._preview_outputs = []
            self.previewOutputsChanged.emit()

        arguments = {
            "input": input_path,
            "model": model,
            "start_seconds": 0,
            "duration_seconds": 15,
            "content_mode": mode,
            "variants": [
                {
                    "pitch": variant_pitch,
                    "f0": f0,
                    "index_rate": index_rate,
                    "rms_mix_rate": rms_mix_rate,
                    "protect": protect,
                }
                for variant_pitch in pitches
            ],
        }
        input_sha256 = self._known_input_sha256(input_path, mode)
        if input_sha256:
            arguments["input_sha256"] = input_sha256
        self.activity.submit(
            "conversion.preview",
            arguments,
            action_key="preview",
            submitted=submitted,
        )

    @Slot(str)
    def refreshPresets(self, model: str) -> None:
        if not model:
            self._presets = []
            self.presetsChanged.emit()
            return

        def update(result: list[dict[str, Any]]) -> None:
            self._presets = result
            self.presetsChanged.emit()

        self.requests.submit(
            "preset.list",
            {"model": model},
            update,
            show_status=False,
            request_key="presets",
        )

    @Slot("QVariantMap")
    def savePreset(self, value: dict[str, Any]) -> None:
        command = dict(value)
        model = str(command["model"])
        name = str(command["name"])
        parameters = {
            "pitch": int(command["pitch"]),
            "f0": str(command["f0"]),
            "index_rate": float(command["index_rate"]),
            "rms_mix_rate": float(command["rms_mix_rate"]),
            "protect": float(command["protect"]),
            "content_mode": str(command["content_mode"]),
        }
        self.requests.submit(
            "preset.save",
            {"model": model, "name": name, "parameters": parameters},
            lambda _result: self.refreshPresets(model),
        )

    @Slot(str)
    @Slot(str, bool)
    def selectAudio(self, path: str, autoplay: bool = False) -> None:
        selected = local_path(path)
        if Path(selected).is_file():
            self._result_audio = selected
            self.resultAudioChanged.emit()
            if autoplay:
                self.playbackRequested.emit()

    @Slot()
    def openResult(self) -> None:
        self._open_path(self._result_path, folder=False)

    @Slot()
    def openResultFolder(self) -> None:
        self._open_path(self._result_path, folder=True)

    def _open_path(self, value: str, *, folder: bool) -> None:
        if not value:
            return
        selected = Path(local_path(value)).expanduser().resolve()
        target = selected.parent if folder and not selected.is_dir() else selected
        if not target.exists() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            key = "error.folder_open_failed" if folder else "error.file_open_failed"
            self._status_callback(
                self._text_callback(key).format(path=target),
                "danger",
            )

    @Slot(object)
    def _consume_task(self, value: object) -> None:
        task = dict(value)
        task_id = str(task["id"])
        state = task.get("state")
        is_preview = task_id == self._preview_task_id
        is_conversion = task_id == self._conversion_task_id
        if task_id == self._analysis_task_id and state in TASK_TERMINAL_STATES:
            self._analysis_task_id = None
            if state == "completed" and task.get("result"):
                self._accept_analysis(task["result"])
        if task_id == self._preview_task_id and state in TASK_TERMINAL_STATES:
            self._preview_task_id = None
            if state == "completed" and task.get("result"):
                self._preview_outputs = task["result"].get("outputs", [])
                for item in self._preview_outputs:
                    item["url"] = QUrl.fromLocalFile(item["output_path"]).toString()
                self.previewOutputsChanged.emit()
        if task_id == self._conversion_task_id and state in TASK_TERMINAL_STATES:
            self._conversion_task_id = None
        if state != "completed" or not isinstance(task.get("result"), dict):
            return
        payload = task["result"]
        output = payload.get("output", {}).get("path")
        if not output and payload.get("outputs"):
            output = payload["outputs"][0].get("output_path")
        if is_conversion and output:
            self._result_path = str(output)
            self.resultChanged.emit()
        if (
            output
            and Path(output).suffix.casefold() in AUDIO_SUFFIXES
            and (is_preview or is_conversion)
            and task_id not in self._handled_audio_tasks
            and str(task.get("created_at", "")) >= self._latest_audio_task_created
        ):
            self._handled_audio_tasks.add(task_id)
            self._latest_audio_task_created = str(task.get("created_at", ""))
            self._result_audio = output
            self.resultAudioChanged.emit()
            if is_preview:
                self.playbackRequested.emit()

    def _accept_analysis(self, result: dict[str, Any]) -> None:
        samples = {item["id"]: item for item in result.get("speaker_samples", [])}
        speakers: dict[str, dict[str, Any]] = {}
        for segment in result.get("segments", []):
            speaker = segment["speaker"]
            item = speakers.setdefault(
                speaker, {"id": speaker, "duration_seconds": 0.0, "segments": 0}
            )
            item["duration_seconds"] += segment["end_seconds"] - segment["start_seconds"]
            item["segments"] += 1
            if speaker in samples:
                item["sample_audio"] = samples[speaker]["sample_audio"]
        self._speakers = list(speakers.values())
        self._analysis_manifest = result.get("manifest_path")
        self._analysis_input_path = result.get("input", {}).get("path")
        self._analysis_sha256 = result.get("input", {}).get("sha256")
        self._analysis_mode = result.get("content_mode")
        self.speakersChanged.emit()
