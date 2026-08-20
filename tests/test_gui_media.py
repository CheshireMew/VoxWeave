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


def test_conversion_paths_are_suggested_and_validated_before_submission(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    source = tmp_path / "voice.wav"
    source.write_bytes(b"RIFF")
    feed = TaskFeedStub()
    media = MediaViewModel(None, None, feed)  # type: ignore[arg-type]

    suggested = media.suggestOutput(str(source))
    assert suggested.endswith("voice-voxweave.wav")
    assert media.validateConversion(str(source), suggested)["valid"] is True

    output = tmp_path / "voice-voxweave.wav"
    output.write_bytes(b"existing")
    validation = media.validateConversion(str(source), str(output))
    assert validation["code"] == "output_exists"
    assert validation["suggestion"].endswith("voice-voxweave-2.wav")
