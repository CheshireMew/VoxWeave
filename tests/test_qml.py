from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fastapi.testclient import TestClient  # noqa: E402
from PySide6.QtCore import QMetaObject, QObject, Qt, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from voxweave import gui as gui_module  # noqa: E402
from voxweave.config import Settings  # noqa: E402
from voxweave.gui import Bridge  # noqa: E402
from voxweave.gui_maintenance import MaintenanceViewModel  # noqa: E402
from voxweave.gui_models import ModelCatalogViewModel  # noqa: E402
from voxweave.gui_presenters import (  # noqa: E402
    localized_model_name,
    localized_task_title,
)
from voxweave.onboarding import RuntimeCandidate  # noqa: E402
from voxweave.service import create_app  # noqa: E402


def test_first_run_requires_twelve_gibibytes_of_free_space() -> None:
    assert gui_module.MINIMUM_INITIAL_FREE_BYTES == 12 * 1024**3


class _ModelFeedStub(QObject):
    taskUpdated = Signal(object)


class _ModelRequestStub:
    def __init__(self, models: list[dict]) -> None:
        self.models = models

    def submit(self, operation, _arguments, completed, **_kwargs) -> None:
        completed(self.models if operation == "model.list" else [])


class _ModelActivityStub:
    def __init__(self) -> None:
        self.submissions = []
        self.completions = []

    def submit(self, operation, arguments, *, action_key, **_kwargs) -> None:
        self.submissions.append((operation, arguments, action_key))
        self.completions.append(_kwargs.get("completed"))


def _model_view_model(models: list[dict]) -> tuple[ModelCatalogViewModel, _ModelActivityStub]:
    activity = _ModelActivityStub()
    view_model = ModelCatalogViewModel(
        _ModelRequestStub(models),
        activity,
        _ModelFeedStub(),
        lambda: ("zh-CN", {"zh-CN": {}, "en": {}}),
    )
    return view_model, activity


def test_startup_discovers_configured_local_models_when_library_is_empty() -> None:
    view_model, activity = _model_view_model([])

    view_model.discover()

    assert activity.submissions == [("model.scan", {}, "model-scan")]


def test_startup_does_not_rescan_a_populated_model_library() -> None:
    view_model, activity = _model_view_model([{"id": "local.voice.default"}])

    view_model.discover()

    assert activity.submissions == []


def test_empty_model_library_waits_for_confirmation_before_starter_download() -> None:
    view_model, activity = _model_view_model([])
    requested = []
    view_model.starterInstallRequested.connect(requested.append)

    view_model.provision()
    assert activity.submissions == [("model.scan", {}, "model-scan")]

    activity.completions[-1]([])
    assert requested == [["community.zh-male-young", "community.zh-female-senior"]]
    assert all(operation != "model.catalog.install" for operation, _, _ in activity.submissions)

    view_model.confirmStarterInstall()
    assert activity.submissions[-1] == (
        "model.catalog.install",
        {"model_id": "community.zh-male-young"},
        "catalog-model:community.zh-male-young",
    )


def test_incomplete_runtime_waits_for_confirmation_before_install(tmp_path) -> None:
    class ImmediateActivity:
        def __init__(self) -> None:
            self.submissions = []

        def submit(self, operation, _arguments, *, action_key, completed, **_kwargs):
            self.submissions.append((operation, action_key))
            if operation == "runtime.inspect":
                completed({"ready": False})

    activity = ImmediateActivity()
    view_model = MaintenanceViewModel(
        Settings(data_root=str(tmp_path)), activity, lambda *_args: None
    )
    requested = []
    view_model.runtimeInstallRequested.connect(lambda: requested.append(True))

    view_model.ensureRuntime()

    assert requested == [True]
    assert activity.submissions == [("runtime.inspect", "runtime-inspect")]


def test_verified_runtime_skips_repeated_startup_inspection(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("voxweave.config.SOURCE_ROOT", tmp_path / "app")
    class ImmediateActivity:
        def __init__(self) -> None:
            self.submissions = []

        def submit(self, operation, _arguments, *, action_key, completed, **_kwargs):
            self.submissions.append((operation, action_key))
            completed(
                {
                    "ready": True,
                    "rvc_root": None,
                    "rvc_python": None,
                    "ffmpeg": None,
                    "ffprobe": None,
                    "hardware_backend": "auto",
                }
            )

    settings = Settings(data_root=str(tmp_path))
    first_activity = ImmediateActivity()
    first = MaintenanceViewModel(settings, first_activity, lambda *_args: None)
    first.ensureRuntime()
    assert first_activity.submissions == [("runtime.inspect", "runtime-inspect")]

    second_activity = ImmediateActivity()
    second = MaintenanceViewModel(settings, second_activity, lambda *_args: None)
    available = []
    second.runtimeAvailable.connect(lambda: available.append(True))
    second.ensureRuntime()

    assert second.runtimeReady is True
    assert second_activity.submissions == []
    assert available == [True]


def test_runtime_confirmation_dialog_controls_install(tmp_path, monkeypatch) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    installs = []
    monkeypatch.setattr(
        MaintenanceViewModel,
        "installRuntime",
        lambda _self: installs.append("runtime"),
    )
    answers = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(
        gui_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: next(answers),
    )

    bridge._confirm_runtime_install()
    assert installs == []
    bridge._confirm_runtime_install()
    assert installs == ["runtime"]

    bridge.shutdown()
    app.processEvents()


def test_starter_model_confirmation_dialog_controls_download(tmp_path, monkeypatch) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    decisions = []
    monkeypatch.setattr(
        ModelCatalogViewModel,
        "confirmStarterInstall",
        lambda _self: decisions.append("confirmed"),
    )
    monkeypatch.setattr(
        ModelCatalogViewModel,
        "declineStarterInstall",
        lambda _self: decisions.append("declined"),
    )
    answers = iter([QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes])
    monkeypatch.setattr(
        gui_module.QMessageBox,
        "question",
        lambda *_args, **_kwargs: next(answers),
    )
    starter_ids = ["community.zh-male-young", "community.zh-female-senior"]

    bridge._confirm_starter_install(starter_ids)
    bridge._confirm_starter_install(starter_ids)

    assert decisions == ["declined", "confirmed"]
    bridge.shutdown()
    app.processEvents()


def test_community_catalog_shows_unknown_license_and_total_download_size() -> None:
    view_model, _activity = _model_view_model([])
    view_model._catalog_items = [
        {
            "id": "community.zh-male-young",
            "display_name": "青年男声",
            "license_spdx": "LicenseRef-Unknown",
            "model_size_bytes": 20 * 1024 * 1024,
            "index_size_bytes": 30 * 1024 * 1024,
        }
    ]

    item = view_model.catalogItems[0]

    assert item["license_label"] == "Unknown license"
    assert item["download_megabytes"] == 50.0


def test_catalog_item_exposes_live_download_progress() -> None:
    view_model, _activity = _model_view_model([])
    view_model._catalog_items = [
        {
            "id": "community.zh-male-young",
            "display_name": "青年男声",
            "license_spdx": "LicenseRef-Unknown",
            "model_size_bytes": 20 * 1024 * 1024,
            "index_size_bytes": 30 * 1024 * 1024,
            "installed": False,
        }
    ]

    view_model._consume_task(
        {
            "id": "download-1",
            "operation": "model.catalog.install",
            "arguments": {"model_id": "community.zh-male-young"},
            "state": "running",
            "progress": 0.42,
        }
    )

    assert view_model.catalogItems[0]["downloading"] is True
    assert view_model.catalogItems[0]["download_progress"] == 0.42

    view_model._consume_task(
        {
            "id": "download-1",
            "operation": "model.catalog.install",
            "arguments": {"model_id": "community.zh-male-young"},
            "state": "failed",
            "progress": 0.0,
        }
    )
    assert view_model.catalogItems[0]["downloading"] is False
    assert view_model.catalogItems[0]["download_progress"] == 0.0


def test_configured_data_root_still_reconciles_existing_runtime(tmp_path, monkeypatch) -> None:
    data_root = tmp_path / "selected-data"
    rvc_root = tmp_path / "existing-rvc"
    runtime = RuntimeCandidate(
        rvc_root=rvc_root,
        rvc_python=rvc_root / ".venv" / "Scripts" / "python.exe",
        ffmpeg=tmp_path / "ffmpeg.exe",
        ffprobe=tmp_path / "ffprobe.exe",
    )
    persisted = []
    monkeypatch.setattr(gui_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui_module, "data_root_is_configured", lambda: True)
    monkeypatch.setattr(gui_module, "resolve_data_root", lambda: data_root)
    monkeypatch.setattr(
        gui_module,
        "discover_runtime_for_data_root",
        lambda target, *, application_root: runtime,
    )
    monkeypatch.setattr(gui_module, "_persist_detected_runtime", persisted.append)

    assert gui_module._initialize_application() is True
    assert len(persisted) == 1
    assert persisted[0].data_root == data_root
    assert persisted[0].runtime == runtime


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
    assert root.property("currentPage") == 0
    assert root.findChild(QObject, "navButton0").property("iconName") == "realtime"
    assert root.findChild(QObject, "navButton1").property("iconName") == "convert"
    for index, object_name in enumerate(
        [
            "realtimePage",
            "conversionPage",
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
    primary_controls = root.findChild(QObject, "realtimePrimaryControls")
    action_row = root.findChild(QObject, "realtimeActionRow")
    test_mode = root.findChild(QObject, "realtimeTestMode")
    realtime_scroll = root.findChild(QObject, "realtimeScroll")
    voice_panel = root.findChild(QObject, "realtimeVoicePanel")
    status_panel = root.findChild(QObject, "realtimeStatusPanel")
    latency_mode = root.findChild(QObject, "realtimeLatencyMode")
    f0_method = root.findChild(QObject, "realtimeF0Method")
    pitch_slider = root.findChild(QObject, "realtimePitchSlider")
    realtime_page = root.findChild(QObject, "realtimePage")
    settings_page = root.findChild(QObject, "settingsPage")
    settings_audio_panel = settings_page.findChild(QObject, "settingsAudioPanel")
    assert primary_controls is not None
    assert action_row is not None
    assert test_mode is not None
    assert realtime_scroll is not None
    assert voice_panel is not None
    assert status_panel is not None
    assert latency_mode is not None
    assert f0_method is not None
    assert pitch_slider is not None
    assert settings_audio_panel is not None
    assert realtime_page.findChild(QObject, "settingsAudioPanel") is None
    assert primary_controls.property("y") < realtime_scroll.property("y")
    assert action_row.property("y") < test_mode.property("y")
    assert action_row.property("width") <= primary_controls.property("width")
    for object_name in (
        "realtimeStartButton",
        "realtimeStopButton",
        "realtimeModelSelector",
    ):
        button = root.findChild(QObject, object_name)
        assert button.property("x") + button.property("width") <= action_row.property("width")
    assert voice_panel.property("y") < status_panel.property("y")
    assert latency_mode.parent().property("y") == f0_method.parent().property("y")
    assert latency_mode.parent().property("x") < f0_method.parent().property("x")
    assert pitch_slider.parent().property("y") > latency_mode.parent().property("y")
    assert test_mode.property("checked") is False
    slider_specs = {
        "conversionPitchSlider": (-24.0, 24.0, 1.0, 9.0),
        "conversionIndexRateSlider": (0.0, 1.0, 0.01, 0.72),
        "conversionRmsMixSlider": (0.0, 1.0, 0.01, 0.25),
        "conversionProtectSlider": (0.0, 0.5, 0.01, 0.33),
        "realtimePitchSlider": (-36.0, 36.0, 1.0, 0.0),
        "realtimeVadThresholdSlider": (10.0, 90.0, 1.0, 35.0),
        "realtimeInputGateSlider": (-60.0, -20.0, 1.0, -30.0),
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


def test_realtime_preferences_survive_restart_and_device_id_changes(tmp_path, monkeypatch) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    settings = Settings(data_root=str(tmp_path))
    ready_models = [
        {
            "id": "local.other.default",
            "localized_name": "Other",
            "status": "ready",
            "recommended": {
                "pitch": 0,
                "f0": "rmvpe",
                "index_rate": 0.72,
                "rms_mix_rate": 0.25,
            },
        },
        {
            "id": "local.voice.default",
            "localized_name": "Voice",
            "status": "ready",
            "recommended": {
                "pitch": 9,
                "f0": "rmvpe",
                "index_rate": 0.72,
                "rms_mix_rate": 0.25,
            },
        },
    ]

    def devices(input_id: int, output_id: int) -> dict:
        return {
            "hostapis": [
                {"id": 0, "name": "MME"},
                {"id": 1, "name": "Windows WASAPI"},
            ],
            "devices": [
                {
                    "id": input_id,
                    "name": "Microphone",
                    "hostapi": "Windows WASAPI",
                    "hostapi_id": 1,
                    "input_channels": 2,
                    "output_channels": 0,
                },
                {
                    "id": output_id,
                    "name": "Speakers",
                    "hostapi": "Windows WASAPI",
                    "hostapi_id": 1,
                    "input_channels": 0,
                    "output_channels": 2,
                },
            ],
            "default_input_device": input_id,
            "default_output_device": output_id,
        }

    current_device_ids = [7, 8]
    prepare_requests: list[dict] = []

    with TestClient(create_app(settings, token="secret")) as client:

        def request_json(_settings, _method, route, payload):
            if payload.get("operation") == "realtime.devices":
                return {
                    "ok": True,
                    "result": devices(*current_device_ids),
                }
            if payload.get("operation") == "realtime.prepare":
                prepare_requests.append(dict(payload["arguments"]))
                return {
                    "ok": True,
                    "result": {
                        "session_id": None,
                        "state": "idle",
                        "stage": "idle",
                        "metrics": {},
                        "worker": {
                            "state": "warming",
                            "pid": 123,
                            "model_id": payload["arguments"]["model"],
                            "model_ready": False,
                        },
                    },
                }
            return client.post(
                route,
                headers={"Authorization": "Bearer secret"},
                json=payload,
            ).json()

        monkeypatch.setattr(gui_module, "request_json", request_json)
        bridge = Bridge(settings, start_background=False)
        engine = QQmlApplicationEngine()
        engine.setInitialProperties({"bridge": bridge})
        qml = Path(__file__).parents[1] / "src" / "voxweave" / "qml" / "Main.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        root = engine.rootObjects()[0]
        page = root.findChild(QObject, "realtimePage")
        page.setProperty("readyModels", ready_models)
        bridge.realtime.refreshDevices()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            app.processEvents()
            if root.findChild(QObject, "settingsAudioInputDevice").property("currentValue") == 7:
                break
            time.sleep(0.01)

        root.findChild(QObject, "realtimeModelSelector").setProperty("currentIndex", 1)
        assert QMetaObject.invokeMethod(page, "applySelectedModelRecommendations")
        assert root.findChild(QObject, "realtimePitchSlider").property("value") == 9
        root.findChild(QObject, "realtimePitchSlider").setProperty("value", 8)
        root.findChild(QObject, "realtimeF0Method").setProperty("currentIndex", 1)
        root.findChild(QObject, "realtimeVadThresholdSlider").setProperty("value", 41)
        root.findChild(QObject, "realtimeInputGateSlider").setProperty("value", -34)
        root.findChild(QObject, "realtimeIndexRateSlider").setProperty("value", 66)
        root.findChild(QObject, "realtimeRmsMixSlider").setProperty("value", 18)
        root.findChild(QObject, "realtimeLatencyMode").setProperty("currentIndex", 2)
        root.findChild(QObject, "realtimeTestMode").setProperty("checked", True)
        assert QMetaObject.invokeMethod(page, "saveCurrentPreferences")
        assert bridge.realtime.preferences["model"] == "local.voice.default"
        assert bridge.realtime.preferences["pitch"] == 8

        prepare_deadline = time.monotonic() + 3
        while time.monotonic() < prepare_deadline:
            app.processEvents()
            if prepare_requests and prepare_requests[-1]["model"] == "local.voice.default":
                break
            time.sleep(0.01)
        assert prepare_requests[-1]["pitch"] == 8
        assert prepare_requests[-1]["input_device"] == 7
        assert prepare_requests[-1]["output_device"] == 8

        deadline = time.monotonic() + 3
        persisted = {}
        while time.monotonic() < deadline:
            app.processEvents()
            if settings.config_path.exists():
                persisted = json.loads(settings.config_path.read_text(encoding="utf-8"))
                if persisted.get("realtime", {}).get("model") == "local.voice.default":
                    break
            time.sleep(0.01)
        profile = persisted["realtime"]
        assert profile == {
            "model": "local.voice.default",
            "hostapi": "Windows WASAPI",
            "input_device": "Microphone",
            "output_device": "Speakers",
            "pitch": 8,
            "f0": "fcpe",
            "index_rate": 0.66,
            "rms_mix_rate": 0.18,
            "vad_threshold": 0.41,
            "input_gate_db": -34.0,
            "block_seconds": 1.0,
            "test_mode": True,
        }
        bridge.shutdown()
        root.close()
        app.processEvents()

    restarted = Settings(**persisted)
    restarted_bridge = Bridge(restarted, start_background=False)
    restarted_engine = QQmlApplicationEngine()
    restarted_engine.setInitialProperties({"bridge": restarted_bridge})
    restarted_engine.load(QUrl.fromLocalFile(str(qml)))
    restarted_root = restarted_engine.rootObjects()[0]
    restarted_page = restarted_root.findChild(QObject, "realtimePage")
    restarted_page.setProperty("readyModels", ready_models)
    current_device_ids[:] = [70, 80]
    restarted_bridge.realtime.refreshDevices()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        app.processEvents()
        if (
            restarted_root.findChild(QObject, "settingsAudioInputDevice").property("currentValue")
            == 70
        ):
            break
        time.sleep(0.01)

    assert (
        restarted_root.findChild(QObject, "realtimeModelSelector").property("currentValue")
        == "local.voice.default"
    )
    assert (
        restarted_root.findChild(QObject, "settingsAudioInputDevice").property("currentValue") == 70
    )
    assert (
        restarted_root.findChild(QObject, "settingsAudioOutputDevice").property("currentValue")
        == 80
    )
    assert restarted_root.findChild(QObject, "realtimePitchSlider").property("value") == 8
    assert restarted_root.findChild(QObject, "realtimeF0Method").property("currentValue") == "fcpe"
    assert restarted_root.findChild(QObject, "realtimeVadThresholdSlider").property("value") == 41
    assert restarted_root.findChild(QObject, "realtimeInputGateSlider").property("value") == -34
    assert restarted_root.findChild(QObject, "realtimeIndexRateSlider").property("value") == 66
    assert restarted_root.findChild(QObject, "realtimeRmsMixSlider").property("value") == 18
    assert restarted_root.findChild(QObject, "realtimeLatencyMode").property("currentValue") == 1.0
    assert restarted_root.findChild(QObject, "realtimeTestMode").property("checked") is True
    restarted_bridge.shutdown()
    restarted_root.close()
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
    additional_models = {
        "official.vctk-p226.male": ("VCTK p226 男声", "VCTK p226"),
        "official.vctk-p274.male": ("VCTK p274 男声", "VCTK p274"),
        "official.vctk-p311.male": ("VCTK p311 男声", "VCTK p311"),
        "thirdparty.forsen.streamer": ("Forsen · 主播", "Forsen · Streamer"),
        "thirdparty.megaman-exe.game": (
            "洛克人 EXE · 游戏角色",
            "MegaMan.EXE · Game character",
        ),
        "thirdparty.samurai-jack.animation": (
            "武士杰克 · 动画角色",
            "Samurai Jack · Animation character",
        ),
    }
    for model_id, (zh_name, en_name) in additional_models.items():
        translated_model = {
            "id": model_id,
            "family": "third_party",
            "display_name": "Database fallback name",
            "checkpoint_epoch": None,
        }
        assert localized_model_name(translated_model, "zh-CN", translations) == zh_name
        assert localized_model_name(translated_model, "en", translations) == en_name
    assert localized_task_title(task, "zh-CN", translations) == "转换文件 · sample.wav"
    assert localized_task_title(task, "en", translations) == "Convert file · sample.wav"


def test_latest_model_refresh_wins_when_responses_finish_out_of_order(
    tmp_path, monkeypatch
) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    call_lock = threading.Lock()
    call_count = 0

    def request_json(_settings, _method, _route, payload):
        nonlocal call_count
        if payload["operation"] == "model.catalog.list":
            return {"ok": True, "result": []}
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
