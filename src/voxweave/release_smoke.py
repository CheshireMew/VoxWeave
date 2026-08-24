from __future__ import annotations

import argparse
import json
import os
import traceback
from pathlib import Path

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QLibraryInfo, QUrl, qVersion
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication

from . import __version__
from .config import PACKAGE_ROOT, configure_process_environment
from .gui import Bridge
from .settings_file_store import load_settings


def _write_report(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run_smoke(report_path: Path) -> int:
    """Load the frozen desktop stack without starting services or entering the event loop."""

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("QSG_RHI_BACKEND", "software")
    report: dict[str, object] = {
        "schema_version": 1,
        "ok": False,
        "voxweave_version": __version__,
        "pyside_version": pyside_version,
        "qt_version": qVersion(),
        "qt_plugins_path": QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath),
    }
    bridge = None
    exit_code = 1
    try:
        QQuickStyle.setStyle("Basic")
        app = QApplication.instance() or QApplication(["VoxWeave", "--release-smoke"])
        app.setApplicationName("VoxWeave Release Smoke")
        settings = load_settings()
        configure_process_environment(settings)
        engine = QQmlApplicationEngine()
        bridge = Bridge(settings, start_background=False)
        engine.setInitialProperties({"bridge": bridge})
        qml_path = PACKAGE_ROOT / "qml" / "Main.qml"
        engine.load(QUrl.fromLocalFile(str(qml_path)))
        app.processEvents()
        root_objects = engine.rootObjects()
        if not root_objects:
            raise RuntimeError("Main.qml loaded without a root object.")
        report.update(
            {
                "ok": True,
                "qml": str(qml_path),
                "root_object_count": len(root_objects),
                "platform_plugin": os.environ.get("QT_QPA_PLATFORM"),
            }
        )
        exit_code = 0
    except Exception as exc:  # noqa: BLE001 - frozen release boundary
        report.update(
            {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
    finally:
        if bridge is not None:
            try:
                bridge.shutdown()
            except Exception as exc:  # noqa: BLE001 - frozen release boundary
                report.update(
                    {
                        "ok": False,
                        "shutdown_error_type": type(exc).__name__,
                        "shutdown_error": str(exc),
                    }
                )
                exit_code = 1
        _write_report(report_path, report)
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the packaged desktop release smoke test.")
    parser.add_argument("--report", type=Path, required=True)
    arguments = parser.parse_args(argv)
    return run_smoke(arguments.report.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
