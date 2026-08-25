from __future__ import annotations

import copy
import json
from typing import Any

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from .gui_activity import TaskActivity
from .gui_requests import RequestCoordinator
from .gui_support import local_path


class ProjectsViewModel(QObject):
    itemsChanged = Signal()
    currentChanged = Signal()
    loadingChanged = Signal()
    historyChanged = Signal()
    editorStateChanged = Signal()
    previewChanged = Signal()
    resultsChanged = Signal()

    def __init__(
        self,
        requests: RequestCoordinator,
        activity: TaskActivity,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.requests = requests
        self.activity = activity
        self._items: list[dict[str, Any]] = []
        self._current: dict[str, Any] = {}
        self._history: list[dict[str, Any]] = []
        self._loading = False
        self._loaded = False
        self._dirty = False
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._preview_source = QUrl()
        self._results: list[dict[str, Any]] = []

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> list[dict[str, Any]]:
        return list(self._items)

    @Property("QVariantMap", notify=currentChanged)
    def current(self) -> dict[str, Any]:
        return copy.deepcopy(self._current)

    @Property("QVariantList", notify=historyChanged)
    def history(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._history)

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return self._loading or not self._loaded

    @Property(bool, notify=editorStateChanged)
    def dirty(self) -> bool:
        return self._dirty

    @Property(bool, notify=editorStateChanged)
    def canUndo(self) -> bool:
        return bool(self._undo)

    @Property(bool, notify=editorStateChanged)
    def canRedo(self) -> bool:
        return bool(self._redo)

    @Property(QUrl, notify=previewChanged)
    def previewSource(self) -> QUrl:
        return self._preview_source

    @Property("QVariantList", notify=resultsChanged)
    def results(self) -> list[dict[str, Any]]:
        return copy.deepcopy(self._results)

    def _set_current(self, project: dict[str, Any]) -> None:
        self._current = copy.deepcopy(project)
        self._dirty = False
        self._undo.clear()
        self._redo.clear()
        self.currentChanged.emit()
        self.editorStateChanged.emit()

    def _push_document(self, document: dict[str, Any]) -> None:
        if not self._current:
            return
        self._undo.append(copy.deepcopy(self._current["document"]))
        if len(self._undo) > 100:
            self._undo.pop(0)
        self._redo.clear()
        self._current["document"] = document
        self._dirty = True
        self.currentChanged.emit()
        self.editorStateChanged.emit()

    @Slot()
    def refresh(self) -> None:
        self._loading = True
        self.loadingChanged.emit()

        def update(result: dict[str, Any]) -> None:
            self._items = list(result["items"])
            self._loading = False
            self._loaded = True
            self.itemsChanged.emit()
            self.loadingChanged.emit()

        def failed(message: str) -> None:
            self._loading = False
            self._loaded = True
            self.loadingChanged.emit()
            self.requests.status_callback(message, "danger")

        self.requests.submit(
            "project.list",
            {"limit": 200, "include_archived": True},
            update,
            show_status=False,
            error_callback=failed,
            request_key="projects",
        )

    @Slot("QVariantMap")
    def createProject(self, value: dict[str, Any]) -> None:
        payload = dict(value)
        payload["input"] = local_path(str(payload["input"]))
        if payload.get("output"):
            payload["output"] = local_path(str(payload["output"]))

        def created(result: dict[str, Any]) -> None:
            self._set_current(result)
            self.refresh()
            self.refreshHistory()

        self.requests.submit("project.create", payload, created)

    @Slot(str)
    def openProject(self, project_id: str) -> None:
        self.requests.submit(
            "project.get",
            {"project_id": project_id},
            lambda result: (
                self._set_current(result),
                self.refreshHistory(),
                self.refreshResults(),
            ),
            show_status=False,
            request_key="project-current",
        )

    @Slot()
    def closeProject(self) -> None:
        self._set_current({})
        self._history = []
        self._results = []
        self.historyChanged.emit()
        self.resultsChanged.emit()

    @Slot("QVariantMap")
    def saveProject(self, value: dict[str, Any]) -> None:
        if not self._current:
            return
        payload = dict(value)
        payload.update(
            project_id=self._current["id"],
            expected_revision=self._current["revision"],
            document=self._current["document"],
        )
        if payload.get("output"):
            payload["output"] = local_path(str(payload["output"]))
        self._persist(payload)

    def _persist(self, payload: dict[str, Any], completed: Any = None) -> None:
        def saved(result: dict[str, Any]) -> None:
            self._set_current(result)
            self.refresh()
            self.refreshHistory()
            if completed:
                completed(result)

        self.requests.submit(
            "project.update",
            payload,
            saved,
            request_key="project-save",
        )

    def _save_before(self, action: Any) -> None:
        if not self._current:
            return
        if not self._dirty:
            action(self._current)
            return
        self._persist(
            {
                "project_id": self._current["id"],
                "expected_revision": self._current["revision"],
                "document": self._current["document"],
            },
            action,
        )

    def _save_values_before(self, value: dict[str, Any], action: Any) -> None:
        if not self._current:
            return
        payload = dict(value)
        payload.update(
            project_id=self._current["id"],
            expected_revision=self._current["revision"],
            document=self._current["document"],
        )
        if payload.get("output"):
            payload["output"] = local_path(str(payload["output"]))
        self._persist(payload, action)

    @Slot()
    def analyze(self) -> None:
        def submit(project: dict[str, Any]) -> None:
            key = f"project-analyze:{project['id']}"
            self.activity.submit(
                "project.analyze",
                {"project_id": project["id"], "expected_revision": project["revision"]},
                action_key=key,
                completed=lambda result: (
                    self._set_current(result),
                    self.refresh(),
                    self.refreshHistory(),
                ),
            )

        self._save_before(submit)

    @Slot("QVariantMap")
    def analyzeProject(self, value: dict[str, Any]) -> None:
        def submit(project: dict[str, Any]) -> None:
            self.activity.submit(
                "project.analyze",
                {"project_id": project["id"], "expected_revision": project["revision"]},
                action_key=f"project-analyze:{project['id']}",
                completed=lambda result: (
                    self._set_current(result),
                    self.refresh(),
                    self.refreshHistory(),
                ),
            )

        self._save_values_before(value, submit)

    @Slot()
    def render(self) -> None:
        def submit(project: dict[str, Any]) -> None:
            self.activity.submit(
                "project.run",
                {
                    "project_id": project["id"],
                    "expected_revision": project["revision"],
                    "overwrite": False,
                },
                action_key=f"project-run:{project['id']}",
                completed=lambda _result: self.refreshResults(),
            )

        self._save_before(submit)

    @Slot("QVariantMap")
    def renderProject(self, value: dict[str, Any]) -> None:
        def submit(project: dict[str, Any]) -> None:
            self.activity.submit(
                "project.run",
                {
                    "project_id": project["id"],
                    "expected_revision": project["revision"],
                    "overwrite": False,
                },
                action_key=f"project-run:{project['id']}",
                completed=lambda _result: self.refreshResults(),
            )

        self._save_values_before(value, submit)

    @Slot(str)
    def previewSegment(self, segment_id: str) -> None:
        def submit(project: dict[str, Any]) -> None:
            def completed(result: dict[str, Any]) -> None:
                outputs = list(result.get("outputs") or [])
                if not outputs:
                    return
                self._preview_source = QUrl.fromLocalFile(outputs[0]["output_path"])
                self.previewChanged.emit()

            self.activity.submit(
                "project.preview",
                {
                    "project_id": project["id"],
                    "expected_revision": project["revision"],
                    "segment_id": segment_id,
                },
                action_key=f"project-preview:{project['id']}:{segment_id}",
                completed=completed,
            )

        self._save_before(submit)

    @Slot(str, bool)
    def setArchived(self, project_id: str, archived: bool) -> None:
        project = (
            self._current
            if self._current.get("id") == project_id
            else next((item for item in self._items if item["id"] == project_id), None)
        )
        if project is None:
            return

        def submit(stored: dict[str, Any]) -> None:
            self.requests.submit(
                "project.archive",
                {
                    "project_id": project_id,
                    "expected_revision": stored["revision"],
                    "archived": archived,
                },
                lambda result: (
                    self._set_current(result)
                    if self._current.get("id") == project_id
                    else None,
                    self.refresh(),
                ),
                request_key=f"project-archive:{project_id}",
            )

        if self._current.get("id") == project_id and self._dirty:
            self._persist(
                {
                    "project_id": project_id,
                    "expected_revision": project["revision"],
                    "document": project["document"],
                },
                submit,
            )
        else:
            submit(project)

    @Slot()
    def refreshHistory(self) -> None:
        if not self._current:
            return

        def update(result: list[dict[str, Any]]) -> None:
            self._history = list(result)
            self.historyChanged.emit()

        self.requests.submit(
            "project.history",
            {"project_id": self._current["id"]},
            update,
            show_status=False,
            request_key="project-history",
        )

    @Slot()
    def refreshResults(self) -> None:
        if not self._current:
            return

        def update(result: dict[str, Any]) -> None:
            self._results = []
            for item in result.get("items") or []:
                projected = dict(item)
                differences = projected.get("differences") or {}
                projected["differences_text"] = json.dumps(
                    differences, ensure_ascii=False, sort_keys=True
                )
                self._results.append(projected)
            self.resultsChanged.emit()

        self.requests.submit(
            "result.list",
            {"project_id": self._current["id"], "limit": 100},
            update,
            show_status=False,
            request_key="project-results",
        )

    @Slot(str, str, bool)
    def updateResult(self, version_id: str, label: str, favorite: bool) -> None:
        self.requests.submit(
            "result.update",
            {"version_id": version_id, "label": label, "favorite": favorite},
            lambda _result: self.refreshResults(),
            request_key=f"result-update:{version_id}",
        )

    @Slot(str)
    def rerunResult(self, version_id: str) -> None:
        self.activity.submit(
            "result.rerun",
            {"version_id": version_id, "overwrite": False},
            action_key=f"result-rerun:{version_id}",
            completed=lambda _result: self.refreshResults(),
        )

    @Slot(str)
    def openResult(self, path: str) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    @Slot(int)
    def restoreRevision(self, revision: int) -> None:
        if not self._current or revision == self._current["revision"]:
            return
        self.requests.submit(
            "project.restore",
            {
                "project_id": self._current["id"],
                "expected_revision": self._current["revision"],
                "revision": revision,
            },
            lambda result: (
                self._set_current(result),
                self.refresh(),
                self.refreshHistory(),
            ),
            request_key="project-restore",
        )

    @Slot(str)
    def setDefaultModel(self, model_id: str) -> None:
        if not self._current:
            return
        document = copy.deepcopy(self._current["document"])
        document["default_model"] = model_id or None
        self._push_document(document)

    @Slot("QVariantMap")
    def setDefaultProcessingChain(self, value: dict[str, Any]) -> None:
        if not self._current:
            return
        document = copy.deepcopy(self._current["document"])
        document.setdefault("default_parameters", {})["processing_chain"] = dict(value)
        self._push_document(document)

    def _segment(self, segment_id: str) -> tuple[dict[str, Any], int] | None:
        document = copy.deepcopy(self._current.get("document") or {})
        for index, segment in enumerate(document.get("segments") or []):
            if segment.get("id") == segment_id:
                return document, index
        return None

    @Slot(str, bool)
    def setSegmentEnabled(self, segment_id: str, enabled: bool) -> None:
        found = self._segment(segment_id)
        if found:
            document, index = found
            document["segments"][index]["enabled"] = enabled
            self._push_document(document)

    @Slot(str, str)
    def setSegmentModel(self, segment_id: str, model_id: str) -> None:
        found = self._segment(segment_id)
        if found:
            document, index = found
            document["segments"][index]["model"] = model_id or None
            self._push_document(document)

    @Slot(str, int)
    def setSegmentPitch(self, segment_id: str, pitch: int) -> None:
        found = self._segment(segment_id)
        if found:
            document, index = found
            document["segments"][index].setdefault("parameters", {})["pitch"] = pitch
            self._push_document(document)

    @Slot(str, float, float)
    def setSegmentBounds(self, segment_id: str, start: float, end: float) -> None:
        found = self._segment(segment_id)
        if found and start >= 0 and end > start:
            document, index = found
            document["segments"][index]["start_seconds"] = start
            document["segments"][index]["end_seconds"] = end
            self._push_document(document)

    @Slot(str)
    def splitSegment(self, segment_id: str) -> None:
        found = self._segment(segment_id)
        if not found:
            return
        document, index = found
        segment = document["segments"][index]
        start = float(segment["start_seconds"])
        end = float(segment["end_seconds"])
        if end - start < 0.5:
            return
        middle = round((start + end) / 2, 3)
        left = {**segment, "id": f"{segment_id}-a", "end_seconds": middle}
        right = {**segment, "id": f"{segment_id}-b", "start_seconds": middle}
        document["segments"][index : index + 1] = [left, right]
        self._push_document(document)

    @Slot(str)
    def mergeWithNext(self, segment_id: str) -> None:
        found = self._segment(segment_id)
        if not found:
            return
        document, index = found
        segments = document["segments"]
        if index + 1 >= len(segments):
            return
        current = segments[index]
        following = segments[index + 1]
        current["end_seconds"] = max(
            float(current["end_seconds"]), float(following["end_seconds"])
        )
        current["id"] = f"{current['id']}-merged"
        segments[index : index + 2] = [current]
        self._push_document(document)

    @Slot()
    def undo(self) -> None:
        if not self._current or not self._undo:
            return
        self._redo.append(copy.deepcopy(self._current["document"]))
        self._current["document"] = self._undo.pop()
        self._dirty = True
        self.currentChanged.emit()
        self.editorStateChanged.emit()

    @Slot()
    def redo(self) -> None:
        if not self._current or not self._redo:
            return
        self._undo.append(copy.deepcopy(self._current["document"]))
        self._current["document"] = self._redo.pop()
        self._dirty = True
        self.currentChanged.emit()
        self.editorStateChanged.emit()
