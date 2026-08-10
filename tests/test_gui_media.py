from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from voxweave.gui_media import MediaViewModel  # noqa: E402


class TaskFeedStub(QObject):
    taskUpdated = Signal(object)


def test_completed_preview_selects_generated_audio_and_requests_playback(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    output = tmp_path / "preview.wav"
    output.write_bytes(b"RIFF")
    feed = TaskFeedStub()
    media = MediaViewModel(None, None, feed)  # type: ignore[arg-type]
    playback_requests: list[bool] = []
    media.playbackRequested.connect(lambda: playback_requests.append(True))
    media._preview_task_id = "preview-task"

    feed.taskUpdated.emit(
        {
            "id": "preview-task",
            "operation": "conversion.preview",
            "state": "completed",
            "created_at": "2026-08-10T15:00:00+00:00",
            "result": {
                "outputs": [
                    {
                        "output_path": str(output),
                        "parameters": {"pitch": 0},
                    }
                ]
            },
        }
    )

    assert media.resultAudio.endswith("preview.wav")
    assert media.previewOutputs[0]["output_path"] == str(output)
    assert playback_requests == [True]
