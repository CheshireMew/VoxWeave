from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_package_keeps_required_runtime_sources() -> None:
    specification = (ROOT / "packaging" / "VoxWeave.spec").read_text(encoding="utf-8")
    for name in (
        "analysis_worker.py",
        "model_inspect_worker.py",
        "runtime_assets_worker.py",
        "runtime_worker.py",
        "rvc_realtime_audio.py",
        "rvc_realtime_worker.py",
        "rvc_worker.py",
        "separation_worker.py",
    ):
        assert f'"{name}"' in specification


def test_windows_package_excludes_development_dependencies() -> None:
    specification = (ROOT / "packaging" / "VoxWeave.spec").read_text(encoding="utf-8")
    assert '"pytest"' in specification
    assert '"ruff"' in specification
    assert '"httpx"' in specification
    assert 'name="VoxWeave"' in specification
    assert "console=False" in specification


def test_qml_packaging_uses_an_explicit_runtime_allowlist() -> None:
    hook = (ROOT / "packaging" / "hooks" / "hook-PySide6.QtQml.py").read_text(encoding="utf-8")
    assert '"QtMultimedia"' in hook
    assert '"QtQuick/Controls/Basic"' in hook
    assert '"QtQuick/Dialogs"' in hook
    assert "QtWebEngine" not in hook
    assert "QtQuick3D" not in hook


def test_windows_package_keeps_only_supported_qt_translations() -> None:
    specification = (ROOT / "packaging" / "VoxWeave.spec").read_text(encoding="utf-8")
    assert 'supported_qt_translations = ("_en.qm", "_ja.qm", "_zh_CN.qm")' in specification


def test_managed_runtime_excludes_rvc_web_and_training_dependencies() -> None:
    requirements = (
        ROOT / "src" / "voxweave" / "resources" / "runtime_requirements_windows.txt"
    ).read_text(encoding="utf-8")
    installed = "\n".join(
        line for line in requirements.casefold().splitlines() if not line.startswith("#")
    )
    for required in ("faiss-cpu", "sounddevice", "silero-vad", "transformers"):
        assert required in installed
    for unrelated in ("gradio", "tensorboard", "fastapi", "matplotlib"):
        assert unrelated not in installed
