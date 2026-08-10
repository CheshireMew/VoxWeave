from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from .gui_activity import TASK_TERMINAL_STATES, TaskActivity
from .gui_requests import RequestCoordinator
from .gui_support import local_path
from .gui_tasks import TaskFeed

AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".m4a", ".aac"}


class MediaViewModel(QObject):
    resultAudioChanged = Signal()
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
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.activity = activity
        self._result_audio = ""
        self._speakers: list[dict[str, Any]] = []
        self._preview_outputs: list[dict[str, Any]] = []
        self._presets: list[dict[str, Any]] = []
        self._analysis_task_id: str | None = None
        self._analysis_manifest: str | None = None
        self._analysis_input_path: str | None = None
        self._analysis_mode: str | None = None
        self._analysis_sha256: str | None = None
        self._preview_task_id: str | None = None
        self._handled_audio_tasks: set[str] = set()
        self._latest_audio_task_created = ""
        task_feed.taskUpdated.connect(self._consume_task)

    @Property(str, notify=resultAudioChanged)
    def resultAudio(self) -> str:
        return QUrl.fromLocalFile(self._result_audio).toString() if self._result_audio else ""

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

    def _known_input_sha256(self, input_path: str, mode: str) -> str | None:
        resolved = str(Path(input_path).expanduser().resolve())
        return (
            self._analysis_sha256
            if self._analysis_input_path == resolved and self._analysis_mode == mode
            else None
        )

    @Slot(str, str, str, int, str, float, float, float, str, object, str)
    def convert(
        self,
        input_value: str,
        output_value: str,
        model: str,
        pitch: int,
        f0: str,
        index_rate: float,
        rms_mix_rate: float,
        protect: float,
        mode: str,
        selected_speakers_value: object,
        overlap_policy: str,
    ) -> None:
        selected_speakers = list(selected_speakers_value or [])
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
        self.activity.submit(
            "conversion.run", arguments, action_key="conversion"
        )

    @Slot(str, str, int, str, float, float, float, str)
    def preview(
        self,
        input_value: str,
        model: str,
        pitch: int,
        f0: str,
        index_rate: float,
        rms_mix_rate: float,
        protect: float,
        mode: str,
    ) -> None:
        input_path = local_path(input_value)

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
                    "pitch": pitch,
                    "f0": f0,
                    "index_rate": index_rate,
                    "rms_mix_rate": rms_mix_rate,
                    "protect": protect,
                },
                {
                    "pitch": pitch + 3,
                    "f0": f0,
                    "index_rate": index_rate,
                    "rms_mix_rate": rms_mix_rate,
                    "protect": protect,
                },
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

    @Slot(str, str, int, str, float, float, float, str)
    def savePreset(
        self,
        model: str,
        name: str,
        pitch: int,
        f0: str,
        index_rate: float,
        rms_mix_rate: float,
        protect: float,
        mode: str,
    ) -> None:
        parameters = {
            "pitch": pitch,
            "f0": f0,
            "index_rate": index_rate,
            "rms_mix_rate": rms_mix_rate,
            "protect": protect,
            "content_mode": mode,
        }
        self.requests.submit(
            "preset.save",
            {"model": model, "name": name, "parameters": parameters},
            lambda _result: self.refreshPresets(model),
        )

    @Slot(str)
    def selectAudio(self, path: str) -> None:
        selected = local_path(path)
        if Path(selected).is_file():
            self._result_audio = selected
            self.resultAudioChanged.emit()

    @Slot(object)
    def _consume_task(self, value: object) -> None:
        task = dict(value)
        task_id = str(task["id"])
        state = task.get("state")
        is_preview = task_id == self._preview_task_id
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
        if state != "completed" or not isinstance(task.get("result"), dict):
            return
        payload = task["result"]
        output = payload.get("output", {}).get("path")
        if not output and payload.get("outputs"):
            output = payload["outputs"][0].get("output_path")
        if (
            output
            and Path(output).suffix.casefold() in AUDIO_SUFFIXES
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
