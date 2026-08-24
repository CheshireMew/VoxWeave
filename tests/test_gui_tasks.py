from __future__ import annotations

import os
import threading
import time
from copy import deepcopy

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from voxweave.config import Settings  # noqa: E402
from voxweave.gui_requests import MAX_QUEUED_REQUESTS, RequestCoordinator  # noqa: E402
from voxweave.gui_tasks import TaskFeed, TaskListViewModel  # noqa: E402


class Requests:
    def __init__(self) -> None:
        self.calls = []
        self.error_formatter = lambda _error_type, message: str(message)
        self.event_cursor = 7
        self.tasks = {
            "task-1": {
                "id": "task-1",
                "operation": "conversion.run",
                "state": "queued",
                "created_at": "2026-01-01T00:00:00+00:00",
            }
        }

    def submit(self, operation, arguments, callback, **_kwargs) -> None:
        self.calls.append((operation, arguments))
        if operation == "task.list":
            callback(
                {
                    "items": list(self.tasks.values()),
                    "next_cursor": None,
                    "event_cursor": self.event_cursor,
                }
            )
        elif operation == "task.get":
            callback(self.tasks[arguments["task_id"]])


def test_task_feed_uses_initial_page_then_updates_one_task_from_event(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    requests = Requests()
    feed = TaskFeed(
        Settings(data_root=str(tmp_path)),
        requests,  # type: ignore[arg-type]
    )
    feed.refresh()
    assert requests.calls == [("task.list", {"limit": 200})]
    assert feed.items[0]["state"] == "queued"
    requests.tasks["task-1"] = {**requests.tasks["task-1"], "state": "completed"}
    feed._event({"id": 7, "task_id": "task-1", "state": "completed"})
    assert requests.calls[-1] == ("task.get", {"task_id": "task-1"})
    assert feed.items[0]["state"] == "completed"
    assert [operation for operation, _arguments in requests.calls].count("task.list") == 1
    feed.shutdown()


def test_task_feed_applies_progress_events_without_refetching_task(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    requests = Requests()
    feed = TaskFeed(Settings(data_root=str(tmp_path)), requests)  # type: ignore[arg-type]
    feed.refresh()
    feed._event(
        {
            "id": 8,
            "task_id": "task-1",
            "state": "running",
            "stage": "converting",
            "progress": 0.5,
            "created_at": "2026-01-01T00:00:01+00:00",
        }
    )
    assert feed.items[0]["state"] == "running"
    assert feed.items[0]["progress"] == 0.5
    assert [operation for operation, _arguments in requests.calls].count("task.get") == 0
    feed.shutdown()


def test_task_feed_replays_events_arriving_during_page_refresh(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    requests = Requests()
    feed = TaskFeed(Settings(data_root=str(tmp_path)), requests)  # type: ignore[arg-type]
    feed.refresh()
    feed.refreshing = True

    feed._event(
        {
            "id": 9,
            "task_id": "task-1",
            "state": "running",
            "stage": "converting",
            "progress": 0.75,
        }
    )

    assert feed.items[0]["state"] == "queued"
    feed.refreshing = False
    feed._drain_pending_events()
    assert feed.items[0]["state"] == "running"
    assert feed.items[0]["progress"] == 0.75
    feed.shutdown()


def test_task_feed_coalesces_unknown_task_event_into_page_refresh(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    requests = Requests()
    feed = TaskFeed(Settings(data_root=str(tmp_path)), requests)  # type: ignore[arg-type]
    feed.refresh()
    requests.event_cursor = 10

    feed._event({"id": 10, "task_id": "new-task", "state": "queued"})

    operations = [operation for operation, _arguments in requests.calls]
    assert operations.count("task.list") == 2
    assert operations.count("task.get") == 0
    feed.shutdown()


def test_task_list_projects_loaded_arguments_and_results_as_visible_details(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    requests = Requests()
    feed = TaskFeed(Settings(data_root=str(tmp_path)), requests)  # type: ignore[arg-type]
    feed.accept(
        {
            "id": "detailed-task",
            "operation": "conversion.run",
            "state": "completed",
            "progress": 1.0,
            "stage": "completed",
            "arguments": {"input": r"D:\Media\input.wav", "model": "local.ready"},
            "result": {"output": {"path": r"D:\Media\output.wav"}},
            "error_type": None,
            "error": None,
            "worker_failures": 0,
            "retry_of": None,
            "created_at": "2026-01-01T00:00:00+00:00",
            "updated_at": "2026-01-01T00:01:00+00:00",
        }
    )
    view_model = TaskListViewModel(
        feed,
        requests,  # type: ignore[arg-type]
        lambda: ("zh-CN", {"zh-CN": {}, "en": {}}),
        lambda *_args: None,
    )

    item = view_model.items[0]

    assert item["detail_loaded"] is True
    assert '"arguments"' in item["details_text"]
    assert r"D:\\Media\\input.wav" in item["details_text"]
    assert r"D:\\Media\\output.wav" in item["details_text"]
    feed.shutdown()


def test_task_feed_keeps_page_errors_and_connection_state_for_the_ui(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])

    class FailingRequests(Requests):
        def submit(self, _operation, _arguments, _callback, **kwargs) -> None:
            kwargs["error_callback"]("local service unavailable")

    requests = FailingRequests()
    feed = TaskFeed(Settings(data_root=str(tmp_path)), requests)  # type: ignore[arg-type]
    feed.next_cursor = "next-page"

    feed.load_more()
    feed._connection_state_changed("reconnecting", "socket closed")

    assert feed.refreshing is False
    assert feed.error == "local service unavailable"
    assert feed.connection_state == "reconnecting"
    assert feed.connection_detail == "socket closed"
    feed.shutdown()


def test_request_coordinator_coalesces_same_key_to_latest_request(tmp_path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    entered = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def transport(_settings, _method, _route, payload):
        value = int(payload["arguments"]["value"])
        calls.append(value)
        if value == 0:
            entered.set()
            release.wait(timeout=2)
        return {"ok": True, "result": {"value": value}}

    coordinator = RequestCoordinator(
        Settings(data_root=str(tmp_path)), transport, lambda *_args: None
    )
    coordinator.submit("test", {"value": 0}, request_key="same")
    assert entered.wait(timeout=2)
    for value in range(1, 100):
        coordinator.submit("test", {"value": value}, request_key="same")
    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and (calls != [0, 99] or coordinator.generations):
        app.processEvents()
        time.sleep(0.01)
    assert calls == [0, 99]
    assert coordinator.generations == {}
    coordinator.shutdown()


def test_request_coordinator_ignores_work_after_shutdown(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    calls: list[str] = []
    coordinator = RequestCoordinator(
        Settings(data_root=str(tmp_path)),
        lambda *_args: calls.append("transport") or {"ok": True, "result": {}},
        lambda *_args: calls.append("status"),
    )

    coordinator.shutdown()
    coordinator.submit("test", {}, callback=lambda _payload: calls.append("callback"))
    coordinator._finish(
        {
            "request_id": "late",
            "show_status": True,
            "request_key": "late",
            "generation": 1,
            "payload": {},
            "callback": lambda _payload: calls.append("callback"),
        }
    )

    assert calls == []


def test_request_coordinator_keeps_raw_diagnostic_outside_display_text(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    statuses: list[tuple[object, str]] = []
    coordinator = RequestCoordinator(
        Settings(data_root=str(tmp_path)),
        lambda *_args: {"ok": True, "result": {}},
        lambda message, kind: statuses.append((message, kind)),
        error_formatter=lambda _code, _message: "Operation failed: audio unavailable",
    )
    diagnostic = "Traceback (most recent call last):\nRuntimeError: audio unavailable"

    coordinator._finish(
        {
            "request_id": "failed-request",
            "show_status": False,
            "request_key": None,
            "generation": 0,
            "error": diagnostic,
            "error_type": "operation_failed",
        }
    )

    assert str(statuses[0][0]) == "Operation failed: audio unavailable"
    assert statuses[0][0].detail == diagnostic
    assert statuses[0][1] == "danger"
    coordinator.shutdown()


def test_request_coordinator_has_a_bounded_executor_queue(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    release = threading.Event()
    errors: list[str] = []

    def transport(*_args):
        release.wait(timeout=2)
        return {"ok": True, "result": {}}

    coordinator = RequestCoordinator(
        Settings(data_root=str(tmp_path)), transport, lambda *_args: None
    )
    overflow = 44
    for index in range(MAX_QUEUED_REQUESTS + overflow):
        coordinator.submit(
            "test",
            {"value": index},
            show_status=False,
            error_callback=errors.append,
            request_key=f"request:{index}",
        )

    assert len(errors) == overflow
    release.set()
    coordinator.shutdown()


def test_request_coordinator_isolates_consumer_callback_failures(tmp_path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    statuses: list[tuple[str, str]] = []
    coordinator = RequestCoordinator(
        Settings(data_root=str(tmp_path)),
        lambda *_args: {"ok": True, "result": {"value": 1}},
        lambda message, kind: statuses.append((message, kind)),
    )

    def broken_callback(_payload) -> None:
        raise RuntimeError("consumer broke")

    coordinator.submit("test", {}, callback=broken_callback)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and (coordinator.active or not statuses):
        app.processEvents()
        time.sleep(0.01)

    assert any(kind == "danger" and "consumer broke" in message for message, kind in statuses)
    assert coordinator.active == {}
    coordinator.shutdown()


def test_settings_conflict_refreshes_revision_before_retry(tmp_path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    settings = Settings(data_root=str(tmp_path))
    service_settings = Settings(
        **{**settings.payload(), "revision": 7, "realtime": {**settings.realtime, "pitch": 3}}
    )
    updates: list[dict] = []
    completed: list[dict] = []

    def transport(_settings, _method, _route, payload):
        if payload["operation"] == "settings.get":
            return {"ok": True, "result": service_settings.payload()}
        updates.append(deepcopy(payload))
        if len(updates) == 1:
            return {
                "ok": False,
                "error_type": "revision_conflict",
                "error": "stale settings",
            }
        committed = Settings(**{**service_settings.payload(), "revision": 8, "language": "en"})
        return {
            "ok": True,
            "result": {
                "revision": 8,
                "changed_fields": ["language"],
                "settings": committed.payload(),
            },
        }

    coordinator = RequestCoordinator(settings, transport, lambda *_args: None)
    coordinator.submit("settings.update", {"language": "en"}, callback=completed.append)
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not completed:
        app.processEvents()
        time.sleep(0.01)

    assert len(updates) == 2
    assert updates[0]["arguments"]["expected_revision"] == 0
    assert updates[1]["arguments"]["expected_revision"] == 7
    assert updates[0]["request_id"] != updates[1]["request_id"]
    assert settings.revision == 8
    assert settings.realtime["pitch"] == 3
    assert completed[0]["settings"]["language"] == "en"
    coordinator.shutdown()
