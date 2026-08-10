from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from voxweave.config import Settings  # noqa: E402
from voxweave.gui_tasks import TaskFeed  # noqa: E402


class Requests:
    def __init__(self) -> None:
        self.calls = []
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
            callback({"items": list(self.tasks.values()), "next_cursor": None})
        elif operation == "task.get":
            callback(self.tasks[arguments["task_id"]])


def test_task_feed_uses_initial_page_then_updates_one_task_from_event(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    requests = Requests()
    feed = TaskFeed(
        Settings(data_root=str(tmp_path)), requests,  # type: ignore[arg-type]
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
