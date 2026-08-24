from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from voxweave.gui_batches import BatchRulesViewModel  # noqa: E402


class TaskFeedStub(QObject):
    taskUpdated = Signal(object)


class RequestStub:
    def __init__(self) -> None:
        self.pending_create = None
        self.calls = []
        self.status_callback = lambda *_args: None

    def submit(self, operation, arguments, callback=None, **_kwargs) -> None:
        self.calls.append((operation, arguments))
        if operation == "batch.create":
            self.pending_create = callback
        elif operation == "batch.list":
            callback({"items": []})


class ActivityStub:
    def __init__(self) -> None:
        self.submissions = []

    def submit(self, operation, arguments, **kwargs) -> None:
        self.submissions.append((operation, arguments, kwargs))


def test_creating_a_batch_rule_waits_for_success_and_does_not_run_implicitly() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    requests = RequestStub()
    activity = ActivityStub()
    view_model = BatchRulesViewModel(
        requests, activity, TaskFeedStub()  # type: ignore[arg-type]
    )
    saved = []
    view_model.ruleSaved.connect(saved.append)

    view_model.saveRule(
        {
            "input_root": r"D:\Media\Input",
            "output_root": r"D:\Media\Output",
            "model": "local.ready",
            "watch": False,
            "preset": {"pitch": 0},
        }
    )

    assert saved == []
    assert activity.submissions == []
    assert requests.pending_create is not None

    requests.pending_create({"id": "batch-1"})

    assert saved == ["batch-1"]
    assert activity.submissions == []
    assert [operation for operation, _arguments in requests.calls] == [
        "batch.create",
        "batch.list",
    ]
