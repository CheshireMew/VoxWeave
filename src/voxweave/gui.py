from __future__ import annotations

import json
import sys
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from .client import request_json
from .config import PACKAGE_ROOT, Settings, configure_process_environment, load_settings


class Bridge(QObject):
    modelsChanged = Signal()
    tasksChanged = Signal()
    statusChanged = Signal()
    resultAudioChanged = Signal()
    runtimeChanged = Signal()
    languageChanged = Signal()
    speakersChanged = Signal()
    previewOutputsChanged = Signal()
    presetsChanged = Signal()
    diagnosticPathChanged = Signal()
    requestCompleted = Signal(object, object, bool)

    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self._models: list[dict[str, Any]] = []
        self._tasks: list[dict[str, Any]] = []
        self._status = "Ready"
        self._result_audio = ""
        self._runtime: dict[str, Any] = {}
        self._speakers: list[dict[str, Any]] = []
        self._analysis_task_id: str | None = None
        self._analysis_manifest: str | None = None
        self._preview_task_id: str | None = None
        self._preview_outputs: list[dict[str, Any]] = []
        self._presets: list[dict[str, Any]] = []
        self._diagnostic_path = ""
        self._handled_model_tasks: set[str] = set()
        self._handled_audio_tasks: set[str] = set()
        self._tasks_refreshing = False
        self._language = settings.language
        translations_path = PACKAGE_ROOT / "resources" / "translations.json"
        self.translations = json.loads(translations_path.read_text(encoding="utf-8"))
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refreshTasks)
        self.requestCompleted.connect(self._finish_request)
        self.timer.start()
        QTimer.singleShot(0, self.refreshModels)

    @Property(str, notify=modelsChanged)
    def modelsJson(self) -> str:
        return json.dumps(self._models, ensure_ascii=False)

    @Property(str, notify=tasksChanged)
    def tasksJson(self) -> str:
        return json.dumps(self._tasks, ensure_ascii=False)

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=resultAudioChanged)
    def resultAudio(self) -> str:
        return QUrl.fromLocalFile(self._result_audio).toString() if self._result_audio else ""

    @Property(str, notify=runtimeChanged)
    def runtimeJson(self) -> str:
        return json.dumps(self._runtime, ensure_ascii=False, indent=2)

    @Property(str, notify=speakersChanged)
    def speakersJson(self) -> str:
        return json.dumps(self._speakers, ensure_ascii=False)

    @Property(str, notify=previewOutputsChanged)
    def previewOutputsJson(self) -> str:
        return json.dumps(self._preview_outputs, ensure_ascii=False)

    @Property(str, notify=presetsChanged)
    def presetsJson(self) -> str:
        return json.dumps(self._presets, ensure_ascii=False)

    @Property(str, constant=True)
    def dataRootUrl(self) -> str:
        return QUrl.fromLocalFile(str(self.settings.root)).toString()

    @Property(str, constant=True)
    def dataRoot(self) -> str:
        return str(self.settings.root)

    @Property(str, notify=diagnosticPathChanged)
    def diagnosticPath(self) -> str:
        return self._diagnostic_path

    @Property(str, notify=languageChanged)
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        if value not in self.translations or value == self._language:
            return
        self._language = value
        self.settings.language = value
        self.settings.save()
        self.languageChanged.emit()

    @Slot(str, result=str)
    def text(self, key: str) -> str:
        return self.translations.get(self._language, {}).get(key, key)

    @staticmethod
    def _local_path(value: str) -> str:
        url = QUrl(value)
        return url.toLocalFile() if url.isLocalFile() else value

    def _set_status(self, value: str) -> None:
        self._status = value
        self.statusChanged.emit()

    @Slot(object, object, bool)
    def _finish_request(self, payload: object, callback: object, show_status: bool) -> None:
        if show_status:
            self._set_status("Ready")
        if callback:
            callback(payload)

    def _request_async(
        self,
        operation: str,
        arguments: dict[str, Any],
        callback: Any = None,
        *,
        show_status: bool = True,
        error_callback: Any = None,
    ) -> None:
        if show_status:
            self._set_status(f"{operation} …")

        def work() -> None:
            request = {
                "protocol": "voxweave-control",
                "version": 1,
                "operation": operation,
                "arguments": arguments,
                "request_id": str(uuid.uuid4()),
                "actor": {"kind": "desktop", "name": "VoxWeave GUI"},
            }
            try:
                payload = request_json(self.settings, "POST", "/v1/execute", request)
            except Exception as exc:  # noqa: BLE001 - UI boundary
                self.requestCompleted.emit(
                    None,
                    lambda _result, message=str(exc): (
                        error_callback(message) if error_callback else self._set_status(message)
                    ),
                    False,
                )
                return
            if not payload.get("ok"):
                message = payload.get("error", "operation failed")
                self.requestCompleted.emit(
                    None,
                    lambda _result, message=message: (
                        error_callback(message) if error_callback else self._set_status(message)
                    ),
                    False,
                )
                return
            self.requestCompleted.emit(payload["result"], callback, show_status)

        threading.Thread(target=work, daemon=True).start()

    @Slot()
    def refreshModels(self) -> None:
        def update(result: list[dict[str, Any]]) -> None:
            self._models = result
            self.modelsChanged.emit()

        self._request_async("model.list", {}, update)

    @Slot()
    def scanModels(self) -> None:
        def update(result: list[dict[str, Any]]) -> None:
            self._models = result
            self.modelsChanged.emit()

        self._request_async("model.scan", {}, update)

    @Slot(str)
    def scanModelRoot(self, root_value: str) -> None:
        root = self._local_path(root_value)

        def update(result: list[dict[str, Any]]) -> None:
            if root not in self.settings.model_roots:
                self.settings.model_roots.append(root)
                self.settings.save()
            self._models = result
            self.modelsChanged.emit()

        self._request_async("model.scan", {"weight_roots": [root], "index_roots": [root]}, update)

    @Slot()
    def refreshTasks(self) -> None:
        if self._tasks_refreshing:
            return
        self._tasks_refreshing = True

        def update(result: list[dict[str, Any]]) -> None:
            self._tasks_refreshing = False
            self._tasks = result
            self.tasksChanged.emit()
            newest_audio: str | None = None
            for task in result:
                if task.get("id") == self._analysis_task_id:
                    if task.get("state") == "completed" and task.get("result"):
                        segments = task["result"].get("segments", [])
                        samples = {
                            item["id"]: item for item in task["result"].get("speaker_samples", [])
                        }
                        speakers: dict[str, dict[str, Any]] = {}
                        for segment in segments:
                            speaker = segment["speaker"]
                            item = speakers.setdefault(
                                speaker, {"id": speaker, "duration_seconds": 0.0, "segments": 0}
                            )
                            item["duration_seconds"] += (
                                segment["end_seconds"] - segment["start_seconds"]
                            )
                            item["segments"] += 1
                            if speaker in samples:
                                item["sample_audio"] = samples[speaker]["sample_audio"]
                        self._speakers = list(speakers.values())
                        self._analysis_manifest = task["result"].get("manifest_path")
                        self._analysis_task_id = None
                        self.speakersChanged.emit()
                    elif task.get("state") == "failed":
                        self._analysis_task_id = None
                        self._set_status(task.get("error") or "analysis failed")
                if task.get("id") == self._preview_task_id:
                    if task.get("state") == "completed" and task.get("result"):
                        self._preview_outputs = task["result"].get("outputs", [])
                        for item in self._preview_outputs:
                            item["url"] = QUrl.fromLocalFile(item["output_path"]).toString()
                        self._preview_task_id = None
                        self.previewOutputsChanged.emit()
                    elif task.get("state") == "failed":
                        self._preview_task_id = None
                        self._set_status(task.get("error") or "preview failed")
                if task.get("state") != "completed" or not task.get("result"):
                    continue
                if (
                    task.get("operation") in {"model.import", "model.catalog.install"}
                    and task["id"] not in self._handled_model_tasks
                ):
                    self._handled_model_tasks.add(task["id"])
                    self.refreshModels()
                payload = task["result"]
                output = payload.get("output", {}).get("path")
                if not output and payload.get("outputs"):
                    output = payload["outputs"][0].get("output_path")
                if (
                    output
                    and Path(output).suffix.casefold() in {".wav", ".flac", ".mp3", ".m4a", ".aac"}
                    and task["id"] not in self._handled_audio_tasks
                ):
                    if newest_audio is None:
                        newest_audio = output
                    self._handled_audio_tasks.add(task["id"])
            if newest_audio:
                self._result_audio = newest_audio
                self.resultAudioChanged.emit()

        self._request_async(
            "task.list",
            {},
            update,
            show_status=False,
            error_callback=lambda message: (
                setattr(self, "_tasks_refreshing", False),
                self._set_status(message),
            ),
        )

    @Slot(str, str)
    def analyze(self, input_value: str, mode: str) -> None:
        self._speakers = []
        self._analysis_manifest = None
        self.speakersChanged.emit()

        def submitted(result: dict[str, Any]) -> None:
            self._analysis_task_id = result["task_id"]
            self.refreshTasks()

        self._request_async(
            "media.analyze",
            {"input": self._local_path(input_value), "content_mode": mode},
            submitted,
        )

    @Slot(str, str, str, int, str, float, float, float, str, str, str)
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
        selected_speakers_json: str,
        overlap_policy: str,
    ) -> None:
        selected_speakers = json.loads(selected_speakers_json or "[]")
        self._request_async(
            "conversion.run",
            {
                "input": self._local_path(input_value),
                "output": self._local_path(output_value),
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
            },
            lambda _result: self.refreshTasks(),
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
        output_directory = str(self.settings.artifacts_dir / "gui-previews" / uuid.uuid4().hex)

        def submitted(result: dict[str, Any]) -> None:
            self._preview_task_id = result["task_id"]
            self._preview_outputs = []
            self.previewOutputsChanged.emit()
            self.refreshTasks()

        self._request_async(
            "conversion.preview",
            {
                "input": self._local_path(input_value),
                "model": model,
                "start_seconds": 0,
                "duration_seconds": 15,
                "output_directory": output_directory,
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
            },
            submitted,
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

        self._request_async("preset.list", {"model": model}, update, show_status=False)

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
        self._request_async(
            "preset.save",
            {"model": model, "name": name, "parameters": parameters},
            lambda _result: self.refreshPresets(model),
        )

    @Slot(str, str, str, str, str, str)
    def importLocalModel(
        self,
        model_value: str,
        index_value: str,
        model_id: str,
        display_name: str,
        license_spdx: str,
        source_url: str,
    ) -> None:
        arguments: dict[str, Any] = {"model": self._local_path(model_value)}
        optional = {
            "index": self._local_path(index_value) if index_value else "",
            "id": model_id.strip(),
            "display_name": display_name.strip(),
            "license_spdx": license_spdx.strip(),
            "source_url": source_url.strip(),
        }
        arguments.update({key: value for key, value in optional.items() if value})
        self._request_async("model.import", arguments, lambda _result: self.refreshTasks())

    @Slot(str, str, str, str, str, int, str)
    def importUrlModel(
        self,
        model_url: str,
        model_id: str,
        display_name: str,
        license_spdx: str,
        model_sha256: str,
        download_size_bytes: int,
        source_url: str,
    ) -> None:
        arguments = {
            "model": model_url.strip(),
            "id": model_id.strip(),
            "display_name": display_name.strip(),
            "license_spdx": license_spdx.strip(),
            "model_sha256": model_sha256.strip(),
            "download_size_bytes": download_size_bytes,
        }
        if source_url.strip():
            arguments["source_url"] = source_url.strip()
        self._request_async("model.import", arguments, lambda _result: self.refreshTasks())

    @Slot(str)
    def selectAudio(self, path: str) -> None:
        local_path = self._local_path(path)
        if Path(local_path).is_file():
            self._result_audio = local_path
            self.resultAudioChanged.emit()

    @Slot(str)
    def cancelTask(self, task_id: str) -> None:
        self._request_async(
            "task.cancel", {"task_id": task_id}, lambda _result: self.refreshTasks()
        )

    @Slot(str)
    def retryTask(self, task_id: str) -> None:
        self._request_async("task.retry", {"task_id": task_id}, lambda _result: self.refreshTasks())

    @Slot(str, str, str, bool)
    def createBatch(self, input_root: str, output_root: str, model: str, watch: bool) -> None:
        def created(result: dict[str, Any]) -> None:
            if not watch:
                self._request_async("batch.run", {"batch_id": result["id"]})

        self._request_async(
            "batch.create",
            {
                "input_root": self._local_path(input_root),
                "output_root": self._local_path(output_root),
                "model": model,
                "preset": {"content_mode": "clean"},
                "preset_name": "default",
                "recursive": True,
                "watch": watch,
            },
            created,
        )

    @Slot()
    def inspectRuntime(self) -> None:
        def update(result: dict[str, Any]) -> None:
            self._runtime = result
            self.runtimeChanged.emit()

        self._request_async("runtime.inspect", {}, update)

    @Slot(str)
    def exportDiagnostics(self, path_value: str) -> None:
        target = Path(self._local_path(path_value)).expanduser().resolve()
        if target.suffix.casefold() != ".json":
            target = target.with_suffix(".json")
        if target.exists():
            self._set_status(f"diagnostic file already exists: {target}")
            return
        payload = {
            "protocol": "voxweave-diagnostics",
            "version": 1,
            "settings": asdict(self.settings),
            "runtime": self._runtime,
            "models": self._models,
            "tasks": self._tasks,
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        self._diagnostic_path = str(target)
        self.diagnosticPathChanged.emit()
        self._set_status(f"diagnostics exported: {target}")


def main() -> int:
    settings = load_settings()
    configure_process_environment(settings)
    app = QGuiApplication(sys.argv)
    app.setApplicationName("VoxWeave")
    app.setOrganizationName("CheshireMew")
    engine = QQmlApplicationEngine()
    bridge = Bridge(settings)
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(PACKAGE_ROOT / "qml" / "Main.qml")))
    if not engine.rootObjects():
        return 1
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
