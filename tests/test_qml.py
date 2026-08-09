from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402

from voxweave.config import load_settings  # noqa: E402
from voxweave.gui import Bridge  # noqa: E402


def test_main_qml_loads() -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    engine = QQmlApplicationEngine()
    bridge = Bridge(load_settings())
    engine.rootContext().setContextProperty("bridge", bridge)
    qml = Path(__file__).parents[1] / "src" / "voxweave" / "qml" / "Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    assert engine.rootObjects()
    bridge.timer.stop()
    app.processEvents()
