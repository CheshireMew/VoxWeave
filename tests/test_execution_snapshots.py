from __future__ import annotations

import time

from voxweave.config import Settings
from voxweave.controller import Controller


def _wait_terminal(controller: Controller, task_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        task = controller.tasks.get(task_id)
        if task["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task did not finish: {task_id}")


def test_queued_media_task_rejects_input_replaced_after_submission(tmp_path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"first revision")
    controller = Controller(Settings(data_root=str(tmp_path / "data")))
    try:
        assert controller.tasks.pause_dispatch("test") is True
        task = controller.execute(
            "media.inspect",
            {"input": str(source)},
            request_id="inspect-original-revision",
        )
        source.write_bytes(b"second revision is different")
        controller.tasks.resume_dispatch("test")
        failed = _wait_terminal(controller, task["id"])
        assert failed["state"] == "failed"
        assert failed["error_type"] == "input_changed"
        assert failed["snapshot"]["input"]["path"] == str(source.resolve())
    finally:
        controller.tasks.resume_dispatch("test")
        controller.shutdown()


def test_queued_conversion_rejects_model_replaced_after_submission(tmp_path) -> None:
    source = tmp_path / "source.wav"
    source.write_bytes(b"input")
    model_path = tmp_path / "voice.pth"
    model_path.write_bytes(b"original model")
    controller = Controller(Settings(data_root=str(tmp_path / "data")))
    try:
        controller.models.register(
            model_path,
            model_id="voice",
            display_name="Voice",
            inspection={"status": "ready"},
        )
        assert controller.tasks.pause_dispatch("test") is True
        task = controller.execute(
            "conversion.run",
            {
                "input": str(source),
                "output": str(tmp_path / "output.wav"),
                "model": "Voice",
            },
            request_id="convert-original-model",
        )
        model_path.write_bytes(b"replacement model")
        controller.tasks.resume_dispatch("test")
        failed = _wait_terminal(controller, task["id"])
        assert failed["state"] == "failed"
        assert failed["error_type"] == "model_changed"
        assert failed["snapshot"]["model"]["id"] == "voice"
    finally:
        controller.tasks.resume_dispatch("test")
        controller.shutdown()
