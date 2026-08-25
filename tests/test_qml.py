from __future__ import annotations

import json
import os
import re
import threading
import time
import wave
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from fastapi.testclient import TestClient  # noqa: E402
from PySide6.QtCore import QMetaObject, QObject, Qt, QUrl, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from voxweave import gui as gui_module  # noqa: E402
from voxweave import service as service_module  # noqa: E402
from voxweave.config import Settings  # noqa: E402
from voxweave.gui import Bridge  # noqa: E402
from voxweave.gui_maintenance import MaintenanceViewModel  # noqa: E402
from voxweave.gui_models import ModelCatalogViewModel  # noqa: E402
from voxweave.gui_presenters import (  # noqa: E402
    error_summary,
    localized_model_name,
    localized_task_title,
)
from voxweave.gui_realtime import RealtimeViewModel  # noqa: E402
from voxweave.onboarding import RuntimeCandidate  # noqa: E402
from voxweave.release_smoke import run_smoke  # noqa: E402
from voxweave.service import create_app  # noqa: E402
from voxweave.settings_file_store import SettingsFileStore  # noqa: E402


def test_first_run_requires_twelve_gibibytes_of_free_space() -> None:
    assert gui_module.MINIMUM_INITIAL_FREE_BYTES == 12 * 1024**3


def test_error_summary_prefers_public_context_or_final_exception() -> None:
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "worker.py", line 2, in <module>\n'
        "    raise RuntimeError('audio failed')\n"
        "RuntimeError: audio failed"
    )
    assert error_summary(traceback) == "RuntimeError: audio failed"
    assert error_summary(f"Readable failure\n{traceback}") == "Readable failure"
    assert error_summary(f"操作失败：{traceback}") == "操作失败：RuntimeError: audio failed"


class _ModelFeedStub(QObject):
    taskUpdated = Signal(object)


class _ModelRequestStub:
    def __init__(self, models: list[dict], catalog: list[dict]) -> None:
        self.models = models
        self.catalog = catalog
        self.calls = []

    def submit(self, operation, _arguments, completed, **_kwargs) -> None:
        self.calls.append(operation)
        completed(self.models if operation == "model.list" else self.catalog)


class _ModelActivityStub:
    def __init__(self) -> None:
        self.submissions = []
        self.completions = []

    def submit(self, operation, arguments, *, action_key, **_kwargs) -> None:
        self.submissions.append((operation, arguments, action_key))
        self.completions.append(_kwargs.get("completed"))


def _starter_catalog() -> list[dict]:
    return [
        {
            "id": model_id,
            "display_name": model_id,
            "starter": True,
            "download_size_bytes": 1024,
            "license_spdx": "CC-BY-4.0",
            "source_url": f"https://example.test/{model_id}",
        }
        for model_id in ("community.zh-male-young", "community.zh-female-senior")
    ]


def _model_view_model(
    models: list[dict], catalog: list[dict] | None = None
) -> tuple[ModelCatalogViewModel, _ModelActivityStub]:
    activity = _ModelActivityStub()
    view_model = ModelCatalogViewModel(
        _ModelRequestStub(models, _starter_catalog() if catalog is None else catalog),
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


def test_startup_uses_one_model_and_catalog_request_when_runtime_is_ready() -> None:
    view_model, _activity = _model_view_model([{"id": "local.voice.default", "status": "ready"}])

    view_model.discover()
    view_model.provision()

    assert view_model.requests.calls.count("model.list") == 1
    assert view_model.requests.calls.count("model.catalog.list") == 1


def test_usable_model_never_triggers_starter_download_prompt() -> None:
    model_sets = [
        [{"id": "local.voice.default", "status": "ready", "archived": False}],
        [
            {
                "id": "community.zh-male-young",
                "status": "ready",
                "archived": False,
            }
        ],
        [
            {
                "id": "community.zh-male-young",
                "status": "ready",
                "archived": False,
            },
            {
                "id": "community.zh-female-senior",
                "status": "ready",
                "archived": False,
            },
        ],
    ]

    for models in model_sets:
        view_model, activity = _model_view_model(models)
        requested = []
        view_model.starterInstallRequested.connect(requested.append)

        view_model.discover()
        view_model.provision()

        assert requested == []
        assert all(
            operation != "model.catalog.install"
            for operation, _arguments, _action_key in activity.submissions
        )
        assert view_model._startup_provisioned is True


def test_empty_starter_queue_finishes_without_emitting_install_prompt() -> None:
    view_model, _activity = _model_view_model([])
    requested = []
    view_model.starterInstallRequested.connect(requested.append)
    view_model._automatic_provisioning = True

    view_model._request_starter_install()

    assert requested == []
    assert view_model._automatic_provisioning is False
    assert view_model._startup_provisioned is True


def test_archived_ready_model_requires_restore_instead_of_claiming_startup_ready() -> None:
    statuses = []
    activity = _ModelActivityStub()
    view_model = ModelCatalogViewModel(
        _ModelRequestStub(
            [{"id": "local.archived", "status": "ready", "archived": True}],
            _starter_catalog(),
        ),
        activity,
        _ModelFeedStub(),
        lambda: ("zh-CN", {"zh-CN": {"models.auto.archived": "restore"}, "en": {}}),
        status_callback=lambda message, kind: statuses.append((message, kind)),
    )
    requested = []
    view_model.starterInstallRequested.connect(requested.append)

    view_model.discover()
    view_model.provision()

    assert requested == []
    assert view_model._startup_provisioned is True
    assert statuses[-1] == ("restore", "warning")


def test_use_in_conversion_emits_the_selected_ready_model() -> None:
    view_model, _activity = _model_view_model(
        [{"id": "local.ready", "status": "ready", "archived": False}]
    )
    selected = []
    view_model.conversionModelRequested.connect(selected.append)
    view_model._set_items([{"id": "local.ready", "status": "ready", "archived": False}])

    view_model.useInConversion("local.ready")

    assert selected == ["local.ready"]


def test_realtime_polling_runs_only_while_session_or_worker_is_active(tmp_path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    settings = Settings(data_root=str(tmp_path))

    class ImmediateRequests:
        def submit(self, operation, _arguments, callback, **_kwargs) -> None:
            assert operation == "realtime.status"
            callback(
                {
                    "session_id": None,
                    "state": "idle",
                    "stage": "idle",
                    "metrics": {},
                    "worker": {"state": "not_started"},
                }
            )

    view_model = RealtimeViewModel(
        settings,
        ImmediateRequests(),  # type: ignore[arg-type]
        lambda *_args: None,
        lambda key: key,
    )
    view_model.start()
    app.processEvents()
    assert view_model.timer.isActive() is False

    view_model._apply_status({"state": "running", "worker": {"state": "ready"}})
    assert view_model.timer.isActive() is True
    assert view_model.timer.interval() == 250

    view_model._apply_status({"state": "stopped", "worker": {"state": "not_started"}})
    assert view_model.timer.isActive() is False


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
    bridge.modelCatalog._starter_details = {item["id"]: item for item in _starter_catalog()}

    bridge._confirm_starter_install(starter_ids)
    bridge._confirm_starter_install(starter_ids)

    assert decisions == ["declined", "confirmed"]
    bridge.shutdown()
    app.processEvents()


def test_empty_starter_confirmation_payload_never_opens_dialog(tmp_path, monkeypatch) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    decisions = []
    monkeypatch.setattr(
        ModelCatalogViewModel,
        "confirmStarterInstall",
        lambda _self: decisions.append("finished"),
    )

    def unexpected_dialog(*_args, **_kwargs):
        raise AssertionError("an empty starter list must not open an install dialog")

    monkeypatch.setattr(gui_module.QMessageBox, "question", unexpected_dialog)

    bridge._confirm_starter_install([])

    assert decisions == ["finished"]
    bridge.shutdown()
    app.processEvents()


def test_danger_status_remains_visible_until_the_user_dismisses_it(tmp_path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)

    bridge._set_status("conversion failed", "danger")
    bridge._set_status("Ready", "success")

    assert bridge.status == "conversion failed"
    assert bridge.statusKind == "danger"

    bridge.dismissStatus()
    assert bridge.status == "Ready"
    bridge._set_status("conversion completed", "success")
    assert bridge.status == "conversion completed"
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
    monkeypatch.setattr(gui_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui_module, "data_root_is_configured", lambda: True)
    monkeypatch.setattr(gui_module, "resolve_data_root", lambda: data_root)

    assert gui_module._initialize_application() is True
    settings = Settings(data_root=str(data_root))
    service_module._adopt_detected_runtime(settings, SettingsFileStore(settings), runtime)
    persisted = json.loads(settings.config_path.read_text(encoding="utf-8"))
    assert persisted["rvc_root"] == str(runtime.rvc_root)
    assert persisted["rvc_python"] == str(runtime.rvc_python)


def test_main_qml_loads(tmp_path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    engine.setInitialProperties({"bridge": bridge})
    qml = Path(__file__).parents[1] / "src" / "voxweave" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    assert not root.flags() & Qt.WindowType.FramelessWindowHint
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
    available_width = app.primaryScreen().availableGeometry().width()
    expected_width = round(max(540, min(840, available_width * 0.84)))
    assert root.width() == expected_width
    assert root.minimumWidth() == 540
    assert sidebar.property("width") == (156.0 if expected_width >= 840 else 64.0)
    assert root.property("currentPage") == 0
    assert root.findChild(QObject, "navButton0").property("iconName") == "realtime"
    assert root.findChild(QObject, "navButton1").property("iconName") == "convert"
    audio_path = tmp_path / "result.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8000)
        audio.writeframes(b"\0\0" * 80)
    bridge.media.selectAudio(str(audio_path))
    app.processEvents()
    result_player = root.findChild(QObject, "resultPlayer")
    assert result_player is not None
    assert result_player.property("source").isEmpty()
    for index, (nav_name, object_name) in enumerate(
        [
            ("navButton0", "realtimePage"),
            ("navButton1", "conversionPage"),
            ("navButtonProjects", "projectsPage"),
            ("navButton2", "modelsPage"),
            ("navButton3", "batchPage"),
            ("navButton4", "tasksPage"),
            ("navButton5", "settingsPage"),
        ]
    ):
        nav_button = root.findChild(QObject, nav_name)
        assert nav_button is not None
        assert QMetaObject.invokeMethod(nav_button, "click")
        app.processEvents()
        assert root.property("currentPage") == index
        assert stack.property("currentIndex") == index
        assert root.findChild(QObject, object_name) is not None
        if index == 1:
            assert not result_player.property("source").isEmpty()
    assert result_player.property("source").isEmpty()
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
        "realtimeInputGateSlider": (-60.0, -20.0, 1.0, -40.0),
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
    traceback = (
        "Traceback (most recent call last):\n"
        '  File "worker.py", line 2, in <module>\n'
        "RuntimeError: audio failed"
    )
    realtime_page.setProperty(
        "session",
        {
            "state": "failed",
            "stage": "failed",
            "error": traceback,
            "metrics": {},
            "worker": {"state": "failed", "model_ready": False},
        },
    )
    app.processEvents()
    realtime_error = root.findChild(QObject, "realtimeErrorSummary")
    assert realtime_error is not None
    assert realtime_error.property("text") == "RuntimeError: audio failed"

    status_banner = root.findChild(QObject, "statusBanner")
    status_text = root.findChild(QObject, "statusBannerText")
    sidebar_status_text = root.findChild(QObject, "sidebarStatusText")
    assert status_banner is not None
    assert status_text is not None
    assert sidebar_status_text is not None
    width_before_error = root.width()
    bridge._set_status(f"操作失败：{traceback}", "danger")
    app.processEvents()
    assert bridge.status == "操作失败：RuntimeError: audio failed"
    assert bridge.statusDetail == f"操作失败：{traceback}"
    assert root.width() == width_before_error
    assert status_text.property("maximumLineCount") == 1
    assert sidebar_status_text.property("maximumLineCount") == 1
    assert status_banner.property("width") <= 720
    assert status_text.property("width") <= status_banner.property("width")
    bridge.copyStatus()
    clipboard = app.clipboard().text()
    assert clipboard.startswith("操作失败：RuntimeError: audio failed")
    assert "Traceback (most recent call last)" in clipboard
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


def test_qml_translation_bindings_refresh_when_language_changes(tmp_path, monkeypatch) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    monkeypatch.setattr(bridge.requests, "submit", lambda *_args, **_kwargs: None)
    engine.setInitialProperties({"bridge": bridge})
    qml = Path(__file__).parents[1] / "src" / "voxweave" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    assert engine.rootObjects()
    root = engine.rootObjects()[0]
    task_button = root.findChild(QObject, "navButton4")
    assert task_button is not None
    assert task_button.property("text") == "任务中心"

    bridge.language = "en"
    app.processEvents()

    assert task_button.property("text") == "Task Center"
    bridge.shutdown()
    app.processEvents()


def test_model_library_navigation_selects_the_requested_conversion_model(tmp_path) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    bridge = Bridge(Settings(data_root=str(tmp_path)), start_background=False)
    engine.setInitialProperties({"bridge": bridge})
    qml = Path(__file__).parents[1] / "src" / "voxweave" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    root = engine.rootObjects()[0]
    bridge.modelCatalog._set_items(
        [
            {
                "id": "local.selected",
                "display_name": "Selected",
                "family": "selected",
                "status": "ready",
                "archived": False,
                "recommended": {
                    "pitch": 0,
                    "f0": "rmvpe",
                    "index_rate": 0.72,
                    "rms_mix_rate": 0.25,
                    "protect": 0.33,
                },
            }
        ]
    )
    app.processEvents()

    bridge.modelCatalog.useInConversion("local.selected")
    app.processEvents()

    assert root.property("currentPage") == 1
    assert root.findChild(QObject, "modelSelector").property("currentValue") == "local.selected"
    bridge.shutdown()
    app.processEvents()


def test_release_smoke_loads_application_stack_without_starting_services(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("VOXWEAVE_HOME", str(tmp_path / "data"))
    report_path = tmp_path / "smoke-report.json"

    assert run_smoke(report_path) == 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["root_object_count"] >= 1
    assert report["platform_plugin"] == "offscreen"


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
        bridge.maintenance._runtime = {"ready": True}
        bridge.maintenance.runtimeChanged.emit()
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
        quiet_deadline = time.monotonic() + 0.65
        while time.monotonic() < quiet_deadline:
            app.processEvents()
            time.sleep(0.01)
        assert prepare_requests == []

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
        assert QMetaObject.invokeMethod(page, "prepareSelectedModel")

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
            "push_to_talk": False,
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


def test_realtime_preference_patch_preserves_concurrent_service_changes(tmp_path) -> None:
    settings = Settings(data_root=str(tmp_path))
    submissions: list[dict] = []

    class ImmediateRequests:
        def submit(self, _operation, arguments, callback, **_kwargs) -> None:
            submissions.append(arguments)
            callback(
                {
                    "settings": {
                        "realtime": {
                            **settings.realtime,
                            "pitch": 5,
                            "input_gate_db": -35.0,
                        }
                    }
                }
            )

    view_model = RealtimeViewModel(
        settings,
        ImmediateRequests(),  # type: ignore[arg-type]
        lambda *_args: None,
        lambda key: key,
    )
    view_model.savePreferences({**settings.realtime, "pitch": 5})
    view_model._persist_preferences()

    assert submissions == [{"realtime": {"pitch": 5}}]
    assert view_model.preferences["pitch"] == 5
    assert view_model.preferences["input_gate_db"] == -35.0


def test_main_qml_translation_keys_are_complete() -> None:
    root = Path(__file__).parents[1] / "src" / "voxweave"
    qml = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted((root / "qml").glob("*.qml"))
    )
    keys = set(re.findall(r'bridge\.text\([^,]+,\s*"([^"]+)"\)', qml))
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
    assert projected[0]["error_summary"] == "操作失败：Readable failure"
    assert "Traceback" not in projected[0]["error_summary"]
    assert projected[1]["result_path"].endswith("output.wav")
    bridge.shutdown()


def test_batch_page_exposes_and_persists_processing_chain() -> None:
    qml_dir = Path(__file__).parents[1] / "src" / "voxweave" / "qml"
    source = (qml_dir / "BatchPage.qml").read_text(encoding="utf-8")
    assert 'objectName: "batchProcessingChain"' in source
    assert '"processing_chain": root.processingChain()' in source


def test_completion_workflows_are_reachable_from_the_desktop_ui() -> None:
    qml_dir = Path(__file__).parents[1] / "src" / "voxweave" / "qml"
    realtime = (qml_dir / "RealtimePage.qml").read_text(encoding="utf-8")
    main = (qml_dir / "Main.qml").read_text(encoding="utf-8")
    mini = (qml_dir / "RealtimeMiniPanel.qml").read_text(encoding="utf-8")
    models = (qml_dir / "ModelsPage.qml").read_text(encoding="utf-8")
    batch = (qml_dir / "BatchPage.qml").read_text(encoding="utf-8")
    projects = (qml_dir / "ProjectsPage.qml").read_text(encoding="utf-8")
    settings = (qml_dir / "SettingsPage.qml").read_text(encoding="utf-8")

    assert 'objectName: "realtimePushToTalkMode"' in realtime
    assert 'objectName: "realtimeMiniPanelButton"' in realtime
    assert 'objectName: "realtimeMiniPanel"' in main
    assert "push_to_talk_pressed" in mini
    assert 'objectName: "modelCoverPreview"' in models
    assert 'objectName: "modelSortSelector"' in models
    assert "batchVariantsModel" in batch
    assert "retryItem" in batch
    assert "rerunResult" in projects
    assert 'objectName: "storageMigrationTarget"' in settings
    assert "activateUpdate" in settings
    assert "rollbackUpdate" in settings
