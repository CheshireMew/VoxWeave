from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from voxweave.gui_media import MediaViewModel  # noqa: E402


class TaskFeedStub(QObject):
    taskUpdated = Signal(object)


class RequestStub:
    def __init__(self) -> None:
        self.calls = []
        self.status_callback = lambda *_args: None

    def submit(self, operation, arguments, **_kwargs) -> None:
        self.calls.append((operation, arguments))


class ActivityStub:
    def __init__(self) -> None:
        self.abandoned = []

    def abandon(self, key) -> None:
        self.abandoned.append(key)


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


def test_only_the_current_conversion_publishes_its_result(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    video = tmp_path / "converted.mp4"
    video.write_bytes(b"video")
    feed = TaskFeedStub()
    media = MediaViewModel(None, None, feed)  # type: ignore[arg-type]
    media._conversion_task_id = "current-conversion"

    feed.taskUpdated.emit(
        {
            "id": "unrelated-batch-child",
            "state": "completed",
            "created_at": "2026-08-10T15:00:00+00:00",
            "result": {"output": {"path": str(tmp_path / "other.wav")}},
        }
    )
    assert media.resultPath == ""

    feed.taskUpdated.emit(
        {
            "id": "current-conversion",
            "state": "completed",
            "created_at": "2026-08-10T15:00:01+00:00",
            "result": {"output": {"path": str(video)}},
        }
    )

    assert media.resultPath == str(video)
    assert media.resultIsAudio is False
    assert media.resultAudio == ""


def test_select_audio_can_request_immediate_audition(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    sample = tmp_path / "speaker.wav"
    sample.write_bytes(b"RIFF")
    feed = TaskFeedStub()
    media = MediaViewModel(None, None, feed)  # type: ignore[arg-type]
    playback = []
    media.playbackRequested.connect(lambda: playback.append(True))

    media.selectAudio(str(sample), True)

    assert media.resultAudioPath == str(sample)
    assert playback == [True]


def test_result_invalidation_cancels_active_work_and_clears_stale_output(tmp_path) -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])
    output = tmp_path / "old.wav"
    output.write_bytes(b"RIFF")
    requests = RequestStub()
    activity = ActivityStub()
    feed = TaskFeedStub()
    media = MediaViewModel(requests, activity, feed)  # type: ignore[arg-type]
    media._preview_task_id = "preview"
    media._conversion_task_id = "conversion"
    media._result_audio = str(output)
    media._result_path = str(output)
    media._preview_outputs = [{"output_path": str(output)}]

    media.invalidateResults()

    assert requests.calls == [
        ("task.cancel", {"task_id": "preview"}),
        ("task.cancel", {"task_id": "conversion"}),
    ]
    assert activity.abandoned == ["preview", "conversion"]
    assert media.resultAudio == ""
    assert media.resultPath == ""
    assert media.previewOutputs == []
