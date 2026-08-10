from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMetaObject, QObject, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

from voxweave import gui as gui_module  # noqa: E402
from voxweave.config import Settings  # noqa: E402
from voxweave.gui import Bridge  # noqa: E402
from voxweave.gui_presenters import (  # noqa: E402
    localized_model_name,
    localized_task_title,
)


def test_main_qml_loads(tmp_path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    engine.setInitialProperties({"bridge": bridge})
    qml = Path(__file__).parents[1] / "src" / "voxweave" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    assert root.flags() & Qt.WindowType.FramelessWindowHint
    for object_name in [
        "windowTitleBar",
        "minimizeButton",
        "maximizeButton",
        "closeButton",
    ]:
        assert root.findChild(QObject, object_name) is not None
    stack = root.findChild(QObject, "pageStack")
    assert stack is not None
    sidebar = root.findChild(QObject, "appSidebar")
    assert sidebar is not None
    assert root.width() == 560
    assert root.minimumWidth() == 540
    assert sidebar.property("width") == 64.0
    for index, object_name in enumerate(
        [
            "conversionPage",
            "realtimePage",
            "modelsPage",
            "batchPage",
            "tasksPage",
            "settingsPage",
        ]
    ):
        nav_button = root.findChild(QObject, f"navButton{index}")
        assert nav_button is not None
        assert QMetaObject.invokeMethod(nav_button, "click")
        app.processEvents()
        assert root.property("currentPage") == index
        assert stack.property("currentIndex") == index
        assert root.findChild(QObject, object_name) is not None
    assert root.findChild(QObject, "realtimeStartButton") is not None
    assert root.findChild(QObject, "realtimeStopButton") is not None
    test_mode = root.findChild(QObject, "realtimeTestMode")
    assert test_mode is not None
    assert test_mode.property("checked") is False
    slider_specs = {
        "conversionPitchSlider": (-24.0, 24.0, 1.0, 9.0),
        "conversionIndexRateSlider": (0.0, 1.0, 0.01, 0.72),
        "conversionRmsMixSlider": (0.0, 1.0, 0.01, 0.25),
        "conversionProtectSlider": (0.0, 0.5, 0.01, 0.33),
        "realtimePitchSlider": (-36.0, 36.0, 1.0, 0.0),
        "realtimeVadThresholdSlider": (10.0, 90.0, 1.0, 35.0),
        "realtimeIndexRateSlider": (0.0, 100.0, 1.0, 72.0),
        "realtimeRmsMixSlider": (0.0, 100.0, 1.0, 25.0),
    }
    for object_name, (minimum, maximum, step, initial) in slider_specs.items():
        slider = root.findChild(QObject, object_name)
        assert slider is not None
        assert slider.property("from") == minimum
        assert slider.property("to") == maximum
        assert slider.property("stepSize") == step
        assert slider.property("value") == initial
    realtime_page = root.findChild(QObject, "realtimePage")
    warmup_status = root.findChild(QObject, "realtimeWarmupStatus")
    vad_status = root.findChild(QObject, "realtimeVadStatus")
    voice_status = root.findChild(QObject, "realtimeVoiceStatus")
    assert realtime_page is not None
    assert warmup_status is not None
    assert vad_status is not None
    assert voice_status is not None
    realtime_page.setProperty(
        "session",
        {
            "state": "stopped",
            "stage": "stopped",
            "metrics": {},
            "worker": {"state": "ready", "model_ready": True},
        },
    )
    app.processEvents()
    assert warmup_status.property("text") == bridge.text("realtime.worker.ready")
    realtime_page.setProperty(
        "session",
        {
            "state": "running",
            "stage": "streaming",
            "metrics": {
                "speech_detected": True,
                "rvc_inference_active": True,
                "peak_in": 0.2,
                "peak_out": 0.1,
                "input_db": -14.0,
                "vad_probability": 0.91,
            },
            "worker": {"state": "ready", "model_ready": True},
        },
    )
    app.processEvents()
    assert vad_status.property("text") == bridge.text("realtime.voice.detected")
    assert voice_status.property("text") == bridge.text("realtime.voice.converting")
    models_page = root.findChild(QObject, "modelsPage")
    model_import_stack = root.findChild(QObject, "modelImportStack")
    assert root.findChild(QObject, "libraryModelSelector") is not None
    assert root.findChild(QObject, "computerModelTab") is not None
    assert root.findChild(QObject, "linkModelTab") is not None
    assert models_page is not None
    assert model_import_stack is not None
    link_tab = root.findChild(QObject, "linkModelTab")
    assert link_tab is not None
    assert QMetaObject.invokeMethod(link_tab, "click")
    app.processEvents()
    assert models_page.property("importTab") == 1
    assert model_import_stack.property("currentIndex") == 1
    bridge.shutdown()
    app.processEvents()


def test_main_qml_translation_keys_are_complete() -> None:
    root = Path(__file__).parents[1] / "src" / "voxweave"
    qml = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((root / "qml").glob("*.qml"))
    )
    keys = set(re.findall(r'bridge\.text\("([^"]+)"\)', qml))
    translations = json.loads(
        (root / "resources" / "translations.json").read_text(encoding="utf-8")
    )
    for language, messages in translations.items():
        assert keys <= messages.keys(), f"{language} missing: {sorted(keys - messages.keys())}"
    assert "JSON.parse" not in qml
    assert "JSON.stringify" not in qml


def test_model_and_task_titles_follow_interface_language() -> None:
    translations = json.loads(
        (
            Path(__file__).parents[1] / "src" / "voxweave" / "resources" / "translations.json"
        ).read_text(encoding="utf-8")
    )
    model = {
        "family": "guaiguai_v2",
        "display_name": "Guaiguai V2",
        "checkpoint_epoch": None,
    }
    task = {
        "id": "3c5739bb-ab38-4068-90b3-9b5865d614c6",
        "operation": "conversion.run",
        "arguments": {"input": r"D:\media\sample.wav"},
    }
    assert localized_model_name(model, "zh-CN", translations) == "乖乖 V2"
    assert localized_model_name(model, "en", translations) == "Guaiguai V2"
    assert localized_task_title(task, "zh-CN", translations) == "转换文件 · sample.wav"
    assert localized_task_title(task, "en", translations) == "Convert file · sample.wav"


def test_latest_model_refresh_wins_when_responses_finish_out_of_order(
    tmp_path, monkeypatch
) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    call_lock = threading.Lock()
    call_count = 0

    def request_json(_settings, _method, _route, _payload):
        nonlocal call_count
        with call_lock:
            call_count += 1
            current = call_count
        time.sleep(0.15 if current == 1 else 0.01)
        return {
            "ok": True,
            "result": [
                {
                    "id": f"model-{current}",
                    "family": f"model-{current}",
                    "display_name": f"Model {current}",
                    "checkpoint_epoch": None,
                    "status": "ready",
                }
            ],
        }

    monkeypatch.setattr(gui_module, "request_json", request_json)
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    bridge.modelCatalog.refresh()
    bridge.modelCatalog.refresh()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        app.processEvents()
        models = bridge.modelCatalog.items
        if call_count == 2 and models:
            time.sleep(0.02)
        if call_count == 2 and models and models[0]["id"] == "model-2":
            time.sleep(0.2)
            app.processEvents()
            break
        time.sleep(0.01)
    assert bridge.modelCatalog.items[0]["id"] == "model-2"
    bridge.shutdown()


def test_task_projection_exposes_safe_error_and_result_actions(tmp_path) -> None:
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    bridge.taskList.feed.items = [
        {
            "id": "failed",
            "operation": "conversion.run",
            "arguments": {"input": str(tmp_path / "input.wav")},
            "state": "failed",
            "stage": "failed",
            "progress": 0,
            "error": "Readable failure\nTraceback (most recent call last): ...",
            "result": None,
            "updated_at": "2026-08-10T10:00:00+00:00",
        },
        {
            "id": "completed",
            "operation": "conversion.run",
            "arguments": {"input": str(tmp_path / "input.wav")},
            "state": "completed",
            "stage": "completed",
            "progress": 1,
            "error": None,
            "result": {"output": {"path": str(tmp_path / "output.wav")}},
            "updated_at": "2026-08-10T10:01:00+00:00",
        },
    ]
    projected = bridge.taskList.items
    assert projected[0]["error_summary"] == "Readable failure"
    assert "Traceback" not in projected[0]["error_summary"]
    assert projected[1]["result_path"].endswith("output.wav")
    bridge.shutdown()
