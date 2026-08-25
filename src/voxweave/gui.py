from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Property, QLocale, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from . import __version__
from .capabilities import AUDIO_EXTENSIONS, MEDIA_EXTENSIONS, VIDEO_EXTENSIONS
from .client import ManagedServiceClient, request_json, shutdown_service
from .config import (
    PACKAGE_ROOT,
    PORTABLE_POINTER,
    SOURCE_ROOT,
    USER_POINTER,
    Settings,
    configure_process_environment,
    data_root_is_configured,
    persist_data_root_pointer,
    resolve_data_root,
)
from .gui_activity import TaskActivity
from .gui_batches import BatchRulesViewModel
from .gui_maintenance import MaintenanceViewModel
from .gui_media import MediaViewModel
from .gui_models import ModelCatalogViewModel
from .gui_presenters import error_summary
from .gui_projects import ProjectsViewModel
from .gui_realtime import RealtimeViewModel
from .gui_realtime_controls import RealtimeTrayController, WindowsGlobalHotkeys
from .gui_requests import RequestCoordinator
from .gui_tasks import TaskFeed, TaskListViewModel
from .onboarding import (
    MINIMUM_INITIAL_FREE_BYTES,
    plan_initial_setup,
)
from .settings_file_store import load_settings


class Bridge(QObject):
    """Application composition root exposed to QML."""

    statusChanged = Signal()
    languageChanged = Signal()

    def __init__(
        self,
        settings: Settings,
        *,
        start_background: bool = True,
        transport: object | None = None,
        service_client: ManagedServiceClient | None = None,
    ):
        super().__init__()
        self.settings = settings
        self._status = "Ready"
        self._status_detail = "Ready"
        self._status_kind = "success"
        self._status_generation = 0
        self._language = settings.language
        translations_path = PACKAGE_ROOT / "resources" / "translations.json"
        self.translations = json.loads(translations_path.read_text(encoding="utf-8"))

        if (
            service_client is None
            and transport is None
            and getattr(request_json, "__module__", "") == "voxweave.client"
        ):
            service_client = ManagedServiceClient(settings)
            transport = service_client.request
        self._service_client = service_client
        self.requests = RequestCoordinator(
            settings,
            transport or request_json,
            self._set_status,
            self._operation_label,
            self._format_error,
            parent=self,
        )
        self._task_feed = TaskFeed(
            settings,
            self.requests,
            self,
            self._service_client.ensure if self._service_client is not None else None,
        )
        self._activity = TaskActivity(self.requests, self._task_feed, self._set_status, self)

        def locale_context() -> tuple[str, dict[str, dict[str, str]]]:
            return self._language, self.translations

        self._model_catalog = ModelCatalogViewModel(
            self.requests,
            self._activity,
            self._task_feed,
            locale_context,
            self,
            status_callback=self._set_status,
        )
        self._task_list = TaskListViewModel(
            self._task_feed,
            self.requests,
            locale_context,
            self._set_status,
            self,
        )
        self._media = MediaViewModel(
            self.requests,
            self._activity,
            self._task_feed,
            self,
            status_callback=self._set_status,
            text_callback=self.text,
        )
        self._projects = ProjectsViewModel(self.requests, self._activity, self)
        self._realtime = RealtimeViewModel(
            settings, self.requests, self._set_status, self.text, self
        )
        self._batch_rules = BatchRulesViewModel(
            self.requests, self._activity, self._task_feed, self
        )
        self._maintenance = MaintenanceViewModel(
            settings,
            self._activity,
            self._set_status,
            self.text,
            self,
        )
        self._maintenance.runtimeInstallRequested.connect(self._confirm_runtime_install)
        self._maintenance.runtimeAvailable.connect(self._realtime.ensureDevices)
        self._maintenance.runtimeAvailable.connect(self._model_catalog.provision)
        self._model_catalog.starterInstallRequested.connect(self._confirm_starter_install)
        if start_background:
            QTimer.singleShot(0, self._task_feed.start)
            QTimer.singleShot(0, self._batch_rules.refresh)
            QTimer.singleShot(0, self._projects.refresh)
            QTimer.singleShot(0, self._realtime.start)
            QTimer.singleShot(0, self._realtime.refreshScenes)
            QTimer.singleShot(0, self._realtime.inspectRouting)
            QTimer.singleShot(0, self._model_catalog.discover)
            QTimer.singleShot(0, self._maintenance.ensureRuntime)

    @Property(QObject, constant=True)
    def activity(self) -> TaskActivity:
        return self._activity

    @Property(QObject, constant=True)
    def modelCatalog(self) -> ModelCatalogViewModel:
        return self._model_catalog

    @Property(QObject, constant=True)
    def taskList(self) -> TaskListViewModel:
        return self._task_list

    @Property(QObject, constant=True)
    def media(self) -> MediaViewModel:
        return self._media

    @Property(QObject, constant=True)
    def projects(self) -> ProjectsViewModel:
        return self._projects

    @Property(QObject, constant=True)
    def realtime(self) -> RealtimeViewModel:
        return self._realtime

    @Property(QObject, constant=True)
    def batchRules(self) -> BatchRulesViewModel:
        return self._batch_rules

    @Property(QObject, constant=True)
    def maintenance(self) -> MaintenanceViewModel:
        return self._maintenance

    @Property(str, constant=True)
    def applicationVersion(self) -> str:
        return __version__

    @Property(str, constant=True)
    def mediaFileFilter(self) -> str:
        return " ".join(f"*{value}" for value in MEDIA_EXTENSIONS)

    @Property(str, constant=True)
    def audioFileFilter(self) -> str:
        return " ".join(f"*{value}" for value in AUDIO_EXTENSIONS)

    @Property(str, constant=True)
    def videoFileFilter(self) -> str:
        return " ".join(f"*{value}" for value in VIDEO_EXTENSIONS)

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=statusChanged)
    def statusKind(self) -> str:
        return self._status_kind

    @Property(str, notify=statusChanged)
    def statusDetail(self) -> str:
        return self._status_detail

    @Slot()
    def _confirm_runtime_install(self) -> None:
        answer = QMessageBox.question(
            None,
            self.text("setup.runtime.title"),
            self.text("setup.runtime.detail").format(root=self.settings.root),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._set_status(self.text("setup.runtime.started"), "info")
            self._maintenance.installRuntime()
        else:
            self._set_status(self.text("setup.runtime.cancelled"), "info")

    @Slot(object)
    def _confirm_starter_install(self, value: object) -> None:
        model_ids = list(value)  # type: ignore[arg-type]
        if not model_ids:
            self._model_catalog.confirmStarterInstall()
            return
        details = self._model_catalog.starterDetails
        model_ids = [
            model_id
            for model_id in model_ids
            if int(details.get(model_id, {}).get("download_size_bytes") or 0) > 0
        ]
        if not model_ids:
            self._model_catalog.declineStarterInstall()
            self._set_status(self.text("models.auto.no_starter"), "warning")
            return
        names = [
            self.text(f"model.name.{model_id}")
            if self.text(f"model.name.{model_id}") != f"model.name.{model_id}"
            else str(details.get(model_id, {}).get("display_name") or model_id)
            for model_id in model_ids
        ]
        source_lines = []
        for model_id, name in zip(model_ids, names, strict=True):
            detail = details.get(model_id, {})
            license_value = str(detail.get("license_spdx") or "LicenseRef-Unknown")
            license_label = (
                self.text("models.license_unknown")
                if license_value == "LicenseRef-Unknown"
                else license_value
            )
            source_lines.append(
                self.text("setup.models.source_line").format(
                    name=name,
                    license=license_label,
                    source=detail.get("source_url") or detail.get("model_url") or "—",
                )
            )
        download_size = (
            sum(
                int(details.get(model_id, {}).get("download_size_bytes") or 0)
                for model_id in model_ids
            )
            / 1024**2
        )
        answer = QMessageBox.question(
            None,
            self.text("setup.models.title"),
            self.text("setup.models.detail").format(
                names=self.text("setup.models.separator").join(names),
                size=download_size,
                root=self.settings.managed_models_dir,
                details="\n".join(source_lines),
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._model_catalog.confirmStarterInstall()
        else:
            self._model_catalog.declineStarterInstall()

    @Property(str, notify=languageChanged)
    def language(self) -> str:
        return self._language

    @language.setter
    def language(self, value: str) -> None:
        if value not in self.translations or value == self._language:
            return
        previous = self._language
        self._language = value
        self._emit_locale_changed()

        def rollback(message: str) -> None:
            self._language = previous
            self._emit_locale_changed()
            self._set_status(message, "danger")

        self.requests.submit(
            "settings.update",
            {"language": value},
            show_status=False,
            error_callback=rollback,
            request_key="language",
        )

    def _emit_locale_changed(self) -> None:
        self.languageChanged.emit()
        self._model_catalog.locale_changed()
        self._task_list.locale_changed()

    @Slot(str, result=str)
    @Slot(str, str, result=str)
    def text(self, language_or_key: str, key: str | None = None) -> str:
        """Translate for Python or for a QML binding that observes ``language``."""

        language = language_or_key if key is not None else self._language
        translation_key = key if key is not None else language_or_key
        return self.translations.get(language, {}).get(translation_key, translation_key)

    def _operation_label(self, operation: str) -> str:
        key = f"task.operation.{operation}"
        translated = self.text(key)
        return operation if translated == key else translated

    def _format_error(self, error_type: object, message: object) -> str:
        code = str(error_type or "operation_failed")
        key = f"error.{code}"
        translated = self.text(key)
        if translated == key:
            translated = self.text("error.operation_failed")
        return translated.format(message=error_summary(message))

    @Slot(str, result=str)
    def summarizeError(self, value: str) -> str:
        return error_summary(value)

    @Slot()
    def copyStatus(self) -> None:
        value = self._status
        if self._status_detail and self._status_detail != self._status:
            value += f"\n\n{self._status_detail}"
        QApplication.clipboard().setText(value)

    @Slot()
    def dismissStatus(self) -> None:
        self._status_generation += 1
        self._status = "Ready"
        self._status_detail = "Ready"
        self._status_kind = "success"
        self.statusChanged.emit()

    @Slot()
    def openProjectPage(self) -> None:
        QDesktopServices.openUrl(QUrl("https://github.com/CheshireMew/VoxWeave"))

    def _set_status(self, value: str, kind: str = "info", detail: str | None = None) -> None:
        if self._status_kind == "danger" and kind != "danger":
            return
        raw_value = str(value)
        diagnostic = detail or getattr(value, "detail", None) or raw_value
        self._status_generation += 1
        generation = self._status_generation
        self._status = error_summary(raw_value) if kind == "danger" else raw_value
        self._status_detail = str(diagnostic)
        self._status_kind = kind
        self.statusChanged.emit()
        if kind == "success" and raw_value != "Ready":
            def clear_success() -> None:
                if generation == self._status_generation and self._status_kind == "success":
                    self.dismissStatus()

            QTimer.singleShot(4500, clear_success)

    @Slot()
    def shutdown(self) -> None:
        self._realtime.shutdown()
        self._task_feed.shutdown()
        self.requests.shutdown()
        if self._service_client is not None:
            try:
                self._service_client.shutdown_if_owned()
            except Exception:
                pass

    @Slot()
    def stopBackgroundService(self) -> None:
        try:
            result = shutdown_service(self.settings)
        except Exception as exc:  # noqa: BLE001 - desktop boundary
            self._set_status(str(exc), "danger")
            return
        if result.get("state") in {"stopped", "stopping"} or result.get("ok"):
            self._set_status(self.text("service.stopped"), "success")
            QTimer.singleShot(0, QApplication.quit)


def _suggested_data_location() -> str:
    if sys.platform == "win32":
        system_drive = str(Path.home().drive).casefold()
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            candidate = Path(f"{letter}:/")
            if candidate.drive.casefold() != system_drive and candidate.exists():
                return str(candidate)
    return str(Path.home())


def _setup_text(key: str) -> str:
    translations = json.loads(
        (PACKAGE_ROOT / "resources" / "translations.json").read_text(encoding="utf-8")
    )
    language = "zh-CN" if QLocale.system().name().casefold().startswith("zh") else "en"
    return translations.get(language, translations["en"]).get(key, key)


def _select_initial_data_root() -> Path | None:
    """Fallback used only when automatic discovery cannot choose a safe volume."""

    QMessageBox.information(
        None,
        _setup_text("setup.data.title"),
        _setup_text("setup.data.detail"),
    )
    while True:
        selected = QFileDialog.getExistingDirectory(
            None,
            _setup_text("setup.data.choose"),
            _suggested_data_location(),
            QFileDialog.Option.ShowDirsOnly,
        )
        if not selected:
            return None
        target = Path(selected).resolve()
        if target.parent == target:
            target /= "VoxWeave"
        target.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(target).free
        if free < MINIMUM_INITIAL_FREE_BYTES:
            QMessageBox.warning(
                None,
                _setup_text("setup.data.space_title"),
                _setup_text("setup.data.space_detail").format(free=free / 1024**3),
            )
            continue
        return target


def _initialize_application() -> bool:
    if not getattr(sys, "frozen", False):
        return True
    if data_root_is_configured():
        return True
    setup = plan_initial_setup(
        application_root=SOURCE_ROOT,
        pointer_paths=(USER_POINTER, PORTABLE_POINTER),
        extra_data_candidates=(resolve_data_root(),),
    )
    target = setup.data_root
    if target is not None:
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError:
            target = None
    if target is None:
        target = _select_initial_data_root()
        if target is None:
            return False
    try:
        target.mkdir(parents=True, exist_ok=True)
        persist_data_root_pointer(target)
    except OSError as exc:
        QMessageBox.critical(
            None,
            _setup_text("setup.data.create_failed_title"),
            _setup_text("setup.data.create_failed_detail").format(target=target, error=exc),
        )
        return False
    return True


def main() -> int:
    QQuickStyle.setStyle("Basic")
    app = QApplication(sys.argv)
    app.setApplicationName("VoxWeave")
    app.setOrganizationName("CheshireMew")
    if not _initialize_application():
        return 0
    settings = load_settings()
    configure_process_environment(settings)
    engine = QQmlApplicationEngine()
    service_client = ManagedServiceClient(settings)
    bridge = Bridge(
        settings,
        transport=service_client.request,
        service_client=service_client,
    )
    engine.setInitialProperties({"bridge": bridge})
    engine.load(QUrl.fromLocalFile(str(PACKAGE_ROOT / "qml" / "Main.qml")))
    if not engine.rootObjects():
        bridge.shutdown()
        return 1
    window = engine.rootObjects()[0]
    if "--voxweave-update-health-token" in sys.argv:
        token_index = sys.argv.index("--voxweave-update-health-token") + 1
        if token_index < len(sys.argv):
            from .updater import UpdateService

            UpdateService(settings).mark_healthy(sys.argv[token_index])
    hotkeys = WindowsGlobalHotkeys(
        {
            "start_stop": bridge.realtime.toggleStartStop,
            "bypass": bridge.realtime.toggleBypass,
            "mute": bridge.realtime.toggleMute,
            "push_to_talk_pressed": bridge.realtime.pushToTalkPressed,
            "push_to_talk_released": bridge.realtime.pushToTalkReleased,
        }
    )
    app.installNativeEventFilter(hotkeys)

    def refresh_hotkeys() -> None:
        failures = hotkeys.update(bridge.realtime.hotkeys)
        if failures:
            bridge._set_status(
                bridge.text(bridge.language, "hotkey.registration_failed").format(
                    message="; ".join(failures)
                ),
                "warning",
            )

    refresh_hotkeys()
    bridge.realtime.hotkeysChanged.connect(refresh_hotkeys)
    mini_window = window.findChild(QObject, "realtimeMiniPanel")
    if mini_window is not None:
        bridge.realtime.miniPanelRequested.connect(mini_window.show)
    tray = RealtimeTrayController(bridge, window, mini_window, bridge)
    app.setQuitOnLastWindowClosed(not tray.available)
    exit_code = app.exec()
    tray.close()
    hotkeys.close()
    bridge.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
