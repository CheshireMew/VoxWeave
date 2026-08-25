from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from .hotkeys import parse_windows_hotkey


class WindowsGlobalHotkeys(QAbstractNativeEventFilter):
    def __init__(self, callbacks: dict[str, Callable[[], None]]) -> None:
        super().__init__()
        self.callbacks = callbacks
        self.registered: dict[int, str] = {}
        self._push_to_talk_key: int | None = None
        self._push_to_talk_active = False
        self._release_timer = QTimer()
        self._release_timer.setInterval(15)
        self._release_timer.timeout.connect(self._poll_push_to_talk_release)

    def update(self, hotkeys: dict[str, str]) -> list[str]:
        self.close()
        if os.name != "nt":
            return []
        failures = []
        user32 = ctypes.windll.user32
        for offset, action in enumerate(
            ("start_stop", "bypass", "mute", "push_to_talk"), start=1
        ):
            value = str(hotkeys.get(action) or "")
            try:
                modifiers, virtual_key = parse_windows_hotkey(value)
            except ValueError as error:
                failures.append(str(error))
                continue
            hotkey_id = 0xB100 + offset
            if not user32.RegisterHotKey(None, hotkey_id, modifiers, virtual_key):
                failures.append(f"global hotkey is already in use: {value}")
                continue
            self.registered[hotkey_id] = action
            if action == "push_to_talk":
                self._push_to_talk_key = virtual_key
        return failures

    def nativeEventFilter(self, event_type: bytes, message: int) -> tuple[bool, int]:
        if os.name != "nt" or bytes(event_type) not in {
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        }:
            return False, 0
        from ctypes import wintypes

        event = ctypes.cast(int(message), ctypes.POINTER(wintypes.MSG)).contents
        if event.message != 0x0312 or int(event.wParam) not in self.registered:
            return False, 0
        action = self.registered[int(event.wParam)]
        if action == "push_to_talk":
            if not self._push_to_talk_active:
                self._push_to_talk_active = True
                callback = self.callbacks.get("push_to_talk_pressed")
                if callback:
                    QTimer.singleShot(0, callback)
            self._release_timer.start()
            return True, 0
        callback = self.callbacks.get(action)
        if callback:
            QTimer.singleShot(0, callback)
        return True, 0

    def _poll_push_to_talk_release(self) -> None:
        if (
            os.name == "nt"
            and self._push_to_talk_key is not None
            and ctypes.windll.user32.GetAsyncKeyState(self._push_to_talk_key) & 0x8000
        ):
            return
        self._release_timer.stop()
        if self._push_to_talk_active:
            self._push_to_talk_active = False
            callback = self.callbacks.get("push_to_talk_released")
            if callback:
                callback()

    def close(self) -> None:
        self._release_timer.stop()
        if self._push_to_talk_active:
            self._push_to_talk_active = False
            callback = self.callbacks.get("push_to_talk_released")
            if callback:
                callback()
        if os.name == "nt":
            for hotkey_id in self.registered:
                ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
        self.registered.clear()
        self._push_to_talk_key = None


def _tray_icon() -> QIcon:
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#7c5cff"))
    painter.setPen(QColor("#7c5cff"))
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(QColor("white"))
    font = painter.font()
    font.setBold(True)
    font.setPixelSize(34)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "V")
    painter.end()
    return QIcon(pixmap)


class RealtimeTrayController(QObject):
    def __init__(
        self,
        bridge: Any,
        window: Any,
        mini_window: Any = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.bridge = bridge
        self.window = window
        self.mini_window = mini_window
        self.tray = QSystemTrayIcon(_tray_icon(), self)
        self.tray.setToolTip("VoxWeave")
        menu = QMenu()
        self.status_action = QAction("VoxWeave · Idle", menu)
        self.status_action.setEnabled(False)
        menu.addAction(self.status_action)
        menu.addSeparator()
        menu.addAction("Start / Stop", bridge.realtime.toggleStartStop)
        menu.addAction("Dry bypass", bridge.realtime.toggleBypass)
        menu.addAction("Mute", bridge.realtime.toggleMute)
        menu.addAction("Record dry + wet", bridge.realtime.toggleRecording)
        menu.addSeparator()
        if mini_window is not None:
            menu.addAction("Show mini controls", self.show_mini_window)
        menu.addAction("Show VoxWeave", self.show_window)
        menu.addAction("Quit", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._activated)
        bridge.realtime.statusChanged.connect(self._update_status)
        self._update_status()
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        if self.available:
            self.tray.show()

    def _update_status(self) -> None:
        state = str(self.bridge.realtime.status.get("state") or "idle")
        self.status_action.setText(f"VoxWeave · {state.title()}")

    def _activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.show_window()

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.requestActivate()

    def show_mini_window(self) -> None:
        if self.mini_window is None:
            return
        self.mini_window.show()
        self.mini_window.raise_()
        self.mini_window.requestActivate()

    def close(self) -> None:
        self.tray.hide()
