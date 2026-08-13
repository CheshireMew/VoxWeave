from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from .client import request_json
from .config import (
    PACKAGE_ROOT,
    PORTABLE_POINTER,
    SOURCE_ROOT,
    USER_POINTER,
    Settings,
    configure_process_environment,
    data_root_is_configured,
    load_settings,
    persist_data_root_pointer,
    resolve_data_root,
)
from .gui_activity import TaskActivity
from .gui_batches import BatchRulesViewModel
from .gui_maintenance import MaintenanceViewModel
from .gui_media import MediaViewModel
from .gui_models import ModelCatalogViewModel
from .gui_realtime import RealtimeViewModel
from .gui_requests import RequestCoordinator
from .gui_tasks import TaskFeed, TaskListViewModel
from .onboarding import (
    MINIMUM_INITIAL_FREE_BYTES,
    InitialSetup,
    discover_runtime_for_data_root,
    plan_initial_setup,
)


class Bridge(QObject):
    """Application composition root exposed to QML."""

    statusChanged = Signal()
    languageChanged = Signal()

    def __init__(self, settings: Settings, *, start_background: bool = True):
        super().__init__()
        self.settings = settings
        self._status = "Ready"
        self._status_kind = "success"
        self._language = settings.language
        translations_path = PACKAGE_ROOT / "resources" / "translations.json"
        self.translations = json.loads(translations_path.read_text(encoding="utf-8"))

        self.requests = RequestCoordinator(settings, request_json, self._set_status, parent=self)
        self._task_feed = TaskFeed(settings, self.requests, self)
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
        self._media = MediaViewModel(self.requests, self._activity, self._task_feed, self)
        self._realtime = RealtimeViewModel(
            settings, self.requests, self._set_status, self.text, self
        )
        self._batch_rules = BatchRulesViewModel(
            self.requests, self._activity, self._task_feed, self
        )
        self._maintenance = MaintenanceViewModel(settings, self._activity, self._set_status, self)
        self._maintenance.runtimeInstallRequested.connect(self._confirm_runtime_install)
        self._maintenance.runtimeAvailable.connect(self._realtime.refreshDevices)
        self._maintenance.runtimeAvailable.connect(self._model_catalog.provision)
        self._model_catalog.starterInstallRequested.connect(self._confirm_starter_install)
        if start_background:
            QTimer.singleShot(0, self._task_feed.start)
            QTimer.singleShot(0, self._batch_rules.refresh)
            QTimer.singleShot(0, self._realtime.start)
            if getattr(sys, "frozen", False):
                QTimer.singleShot(0, self._maintenance.ensureRuntime)
            else:
                QTimer.singleShot(0, self._model_catalog.discover)
                QTimer.singleShot(0, self._maintenance.inspectRuntime)

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
    def realtime(self) -> RealtimeViewModel:
        return self._realtime

    @Property(QObject, constant=True)
    def batchRules(self) -> BatchRulesViewModel:
        return self._batch_rules

    @Property(QObject, constant=True)
    def maintenance(self) -> MaintenanceViewModel:
        return self._maintenance

    @Property(str, notify=statusChanged)
    def status(self) -> str:
        return self._status

    @Property(str, notify=statusChanged)
    def statusKind(self) -> str:
        return self._status_kind

    @Slot()
    def _confirm_runtime_install(self) -> None:
        answer = QMessageBox.question(
            None,
            "VoxWeave · 准备运行环境",
            "没有检测到可以直接使用的完整 RVC 运行环境。\n\n"
            "继续后将把 Python、RVC、FFmpeg 和必要的推理依赖下载到：\n"
            f"{self.settings.root}\n\n"
            "完整安装至少需要 12 GiB 可用空间，耗时取决于网络速度。\n"
            "现在开始安装吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._set_status("正在准备运行环境，可在任务中心查看进度", "info")
            self._maintenance.installRuntime()
        else:
            self._set_status("已取消安装；可以稍后在设置页继续", "info")

    @Slot(object)
    def _confirm_starter_install(self, value: object) -> None:
        model_ids = list(value)  # type: ignore[arg-type]
        details = {
            "community.zh-male-young": ("青年男声", 83.9),
            "community.zh-female-senior": ("学姐女声", 177.2),
        }
        names = [details[model_id][0] for model_id in model_ids]
        download_size = sum(details[model_id][1] for model_id in model_ids)
        answer = QMessageBox.question(
            None,
            "VoxWeave · 准备中文声音模型",
            "没有检测到可以直接使用的声音模型。\n\n"
            f"建议下载：{'、'.join(names)}\n"
            f"下载量约 {download_size:.1f} MiB，文件保存到：\n"
            f"{self.settings.managed_models_dir}\n\n"
            "现在下载并安装吗？",
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
    def text(self, key: str) -> str:
        return self.translations.get(self._language, {}).get(key, key)

    def _set_status(self, value: str, kind: str = "info") -> None:
        self._status = value
        self._status_kind = kind
        self.statusChanged.emit()

    @Slot()
    def shutdown(self) -> None:
        self._realtime.shutdown()
        self._task_feed.shutdown()
        self.requests.shutdown()


def _suggested_data_location() -> str:
    if sys.platform == "win32":
        system_drive = str(Path.home().drive).casefold()
        for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
            candidate = Path(f"{letter}:/")
            if candidate.drive.casefold() != system_drive and candidate.exists():
                return str(candidate)
    return str(Path.home())


def _select_initial_data_root() -> Path | None:
    """Fallback used only when automatic discovery cannot choose a safe volume."""

    QMessageBox.information(
        None,
        "VoxWeave · 需要选择数据目录",
        "没有找到已有 VoxWeave 环境，也没有找到可自动使用且空间充足的磁盘。\n\n"
        "请选择一个至少有 12 GiB 可用空间的数据目录。",
    )
    while True:
        selected = QFileDialog.getExistingDirectory(
            None,
            "选择 VoxWeave 数据目录",
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
                "VoxWeave · 空间不足",
                f"这个目录只有 {free / 1024**3:.1f} GiB 可用空间，"
                "首次安装至少需要 12 GiB。请换一个目录。",
            )
            continue
        return target


def _persist_detected_runtime(setup: InitialSetup) -> None:
    runtime = setup.runtime
    if not runtime or not setup.data_root:
        return
    settings = load_settings()
    weight_root = runtime.rvc_root / "assets" / "weights"
    weight_roots = list(settings.weight_roots or [])
    if weight_root.is_dir() and str(weight_root) not in weight_roots:
        weight_roots.append(str(weight_root))
    index_roots = list(settings.index_roots or [])
    for path in (runtime.rvc_root / "assets" / "indices", runtime.rvc_root / "logs"):
        if path.is_dir() and str(path) not in index_roots:
            index_roots.append(str(path))
    settings.update(
        rvc_root=str(runtime.rvc_root),
        rvc_python=str(runtime.rvc_python),
        ffmpeg=str(runtime.ffmpeg) if runtime.ffmpeg else settings.ffmpeg,
        ffprobe=str(runtime.ffprobe) if runtime.ffprobe else settings.ffprobe,
        weight_roots=weight_roots,
        index_roots=index_roots,
    )


def _initialize_application() -> bool:
    if not getattr(sys, "frozen", False):
        return True
    if data_root_is_configured():
        target = resolve_data_root()
        try:
            runtime = discover_runtime_for_data_root(
                target,
                application_root=SOURCE_ROOT,
            )
            _persist_detected_runtime(InitialSetup(target, True, runtime, "configured_data"))
        except OSError as exc:
            QMessageBox.warning(
                None,
                "VoxWeave · 自动检测未保存",
                f"已使用数据目录 {target}，但无法保存检测到的运行组件：\n{exc}",
            )
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
        _persist_detected_runtime(
            InitialSetup(target, setup.reused_existing_data, setup.runtime, setup.reason)
        )
    except OSError as exc:
        QMessageBox.critical(
            None,
            "VoxWeave · 无法创建数据目录",
            f"无法使用 {target}：\n{exc}",
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
    bridge = Bridge(settings)
    engine.setInitialProperties({"bridge": bridge})
    engine.load(QUrl.fromLocalFile(str(PACKAGE_ROOT / "qml" / "Main.qml")))
    if not engine.rootObjects():
        bridge.shutdown()
        return 1
    exit_code = app.exec()
    bridge.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
