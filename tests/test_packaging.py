from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

import pytest

from scripts.release_artifacts import (
    ReleaseValidationError,
    assemble_release,
    load_component_authority,
    sha256_file,
    validate_pyinstaller_analysis,
    verify_bundle,
)

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
        "release_smoke.py",
        "separation_worker.py",
    ):
        assert f'"{name}"' in specification


def test_windows_package_excludes_development_dependencies() -> None:
    specification = (ROOT / "packaging" / "VoxWeave.spec").read_text(encoding="utf-8")
    assert '"_pytest"' in specification
    assert '"pytest"' in specification
    assert '"ruff"' in specification
    assert '"httpx"' in specification
    assert '"packaging"' in specification
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


def _fake_windows_bundle(path: Path) -> None:
    files = {
        "VoxWeave.exe": b"fake executable for release workflow tests",
        "_internal/python312.dll": b"fake Python runtime",
        "_internal/PySide6/Qt6Core.dll": b"fake Qt runtime",
        "_internal/voxweave/qml/Main.qml": b"import QtQuick\n",
    }
    for relative, content in files.items():
        target = path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


def _assemble_fake_release(root: Path, *, suffix: str = "") -> dict[str, str]:
    bundle = root / f"bundle{suffix}"
    _fake_windows_bundle(bundle)
    return assemble_release(
        repository=ROOT,
        bundle_root=bundle,
        artifacts_root=root / f"artifacts{suffix}",
        verification_root=root / f"verification{suffix}",
        version="0.2.0",
        commit="a" * 40,
        source_url="https://github.com/CheshireMew/VoxWeave.git",
        source_date_epoch=1_700_000_000,
    )


def test_release_authority_covers_every_bundled_python_distribution() -> None:
    authority = json.loads(
        (ROOT / "packaging" / "runtime-components.json").read_text(encoding="utf-8")
    )
    names = {component["distribution"].casefold() for component in authority["components"]}
    expected = {
        "annotated-types",
        "anyio",
        "cffi",
        "click",
        "colorama",
        "fastapi",
        "h11",
        "idna",
        "numpy",
        "pycparser",
        "pydantic",
        "pydantic-core",
        "pyinstaller",
        "pyside6",
        "pyside6-addons",
        "pyside6-essentials",
        "scipy",
        "shiboken6",
        "soundfile",
        "starlette",
        "typing-extensions",
        "typing-inspection",
        "uvicorn",
        "websockets",
    }
    assert names == expected

    locked = {}
    for lock_path in (ROOT / "requirements.lock", ROOT / "requirements-build.lock"):
        for line in lock_path.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#") and "==" in line:
                name, version = line.split("==", 1)
                locked[name.casefold()] = version
    assert {
        component["distribution"].casefold(): component["version"]
        for component in authority["components"]
    } == {name: locked[name] for name in expected}

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    direct = {
        dependency.split("==", 1)[0].casefold()
        for dependency in project["project"]["dependencies"]
    }
    assert direct <= names


def test_release_flow_collects_licenses_and_verifies_extracted_zip(tmp_path: Path) -> None:
    result = _assemble_fake_release(tmp_path)
    archive = Path(result["archive"])
    checksum = Path(result["checksum"]).read_text(encoding="ascii").split()[0]
    assert checksum == sha256_file(archive)

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["source"]["commit"] == "a" * 40
    assert manifest["target"] == "windows-x64"
    assert "pyinstaller" in manifest["pyinstaller_distributions"]
    assert all(component["license_files"] for component in manifest["components"])
    qt_components = [
        component
        for component in manifest["components"]
        if component["distribution"].casefold().startswith(("pyside6", "shiboken6"))
    ]
    assert qt_components
    assert all(
        "licenses/GNU/LGPL-3.0.txt" in component["license_files"]
        and "QT_PYSIDE_COMPLIANCE.md" in component["license_files"]
        for component in qt_components
    )
    extracted = Path(result["verification"]) / "VoxWeave"
    assert (extracted / "licenses" / "GNU" / "LGPL-3.0.txt").is_file()
    assert (extracted / "licenses" / "CPython" / "LICENSE.txt").is_file()


def test_release_zip_is_deterministic_for_the_same_inputs(tmp_path: Path) -> None:
    first = _assemble_fake_release(tmp_path, suffix="-one")
    second = _assemble_fake_release(tmp_path, suffix="-two")
    assert sha256_file(Path(first["archive"])) == sha256_file(Path(second["archive"]))


def test_release_verification_rejects_post_manifest_tampering(tmp_path: Path) -> None:
    result = _assemble_fake_release(tmp_path)
    extracted = Path(result["verification"]) / "VoxWeave"
    (extracted / "VoxWeave.exe").write_bytes(b"tampered")
    with pytest.raises(ReleaseValidationError, match="integrity verification"):
        verify_bundle(extracted, version="0.2.0", commit="a" * 40)


def test_release_verification_rejects_bundled_models(tmp_path: Path) -> None:
    bundle = tmp_path / "bundle"
    _fake_windows_bundle(bundle)
    model = bundle / "models" / "voice.pth"
    model.parent.mkdir()
    model.write_bytes(b"must not ship")
    with pytest.raises(ReleaseValidationError, match="development/runtime data|Model artifact"):
        assemble_release(
            repository=ROOT,
            bundle_root=bundle,
            artifacts_root=tmp_path / "artifacts",
            verification_root=tmp_path / "verification",
            version="0.2.0",
            commit="a" * 40,
            source_url="https://github.com/CheshireMew/VoxWeave.git",
            source_date_epoch=1_700_000_000,
        )


def test_windows_build_is_external_clean_and_one_per_commit() -> None:
    script = (ROOT / "scripts" / "build-exe.ps1").read_text(encoding="utf-8")
    assert ".archive" not in script
    assert "status --porcelain=v1 --untracked-files=normal" in script
    assert "Release builds must stay outside the repository" in script
    assert "Release builds must not consume the system drive" in script
    assert "A build for this exact version and commit already exists" in script
    assert "release_artifacts.py" in script
    assert "--analysis-toc" in script
    assert "--voxweave-release-smoke" in script
    assert "WaitForExit(30000)" in script


def test_frozen_entry_dispatches_release_smoke_without_starting_gui(monkeypatch) -> None:
    from voxweave import app, release_smoke

    received = []
    monkeypatch.setattr(
        sys,
        "argv",
        ["VoxWeave.exe", "--voxweave-release-smoke", "--report", "x"],
    )
    monkeypatch.setattr(release_smoke, "main", lambda arguments: received.append(arguments) or 17)

    assert app.main() == 17
    assert received == [["--report", "x"]]


def _write_fake_pyinstaller_analysis(path: Path, *, extra_module: str | None = None) -> None:
    modules = [
        ("_pyi_rth_utils", "D:/fake/site-packages/PyInstaller/fake.py", "PYMODULE"),
        ("annotated_types", "D:/fake/site-packages/annotated_types/__init__.py", "PYMODULE"),
        ("anyio", "D:/fake/site-packages/anyio/__init__.py", "PYMODULE"),
        ("cffi", "D:/fake/site-packages/cffi/__init__.py", "PYMODULE"),
        ("click", "D:/fake/site-packages/click/__init__.py", "PYMODULE"),
        ("colorama", "D:/fake/site-packages/colorama/__init__.py", "PYMODULE"),
        ("fastapi", "D:/fake/site-packages/fastapi/__init__.py", "PYMODULE"),
        ("h11", "D:/fake/site-packages/h11/__init__.py", "PYMODULE"),
        ("idna", "D:/fake/site-packages/idna/__init__.py", "PYMODULE"),
        ("numpy", "D:/fake/site-packages/numpy/__init__.py", "PYMODULE"),
        ("pycparser", "D:/fake/site-packages/pycparser/__init__.py", "PYMODULE"),
        ("pydantic", "D:/fake/site-packages/pydantic/__init__.py", "PYMODULE"),
        ("pydantic_core", "D:/fake/site-packages/pydantic_core/__init__.py", "PYMODULE"),
        ("PySide6", "D:/fake/site-packages/PySide6/__init__.py", "PYMODULE"),
        ("scipy", "D:/fake/site-packages/scipy/__init__.py", "PYMODULE"),
        ("shiboken6", "D:/fake/site-packages/shiboken6/__init__.py", "PYMODULE"),
        ("_soundfile", "D:/fake/site-packages/_soundfile.py", "PYMODULE"),
        ("starlette", "D:/fake/site-packages/starlette/__init__.py", "PYMODULE"),
        ("typing_extensions", "D:/fake/site-packages/typing_extensions.py", "PYMODULE"),
        ("typing_inspection", "D:/fake/site-packages/typing_inspection/__init__.py", "PYMODULE"),
        ("uvicorn", "D:/fake/site-packages/uvicorn/__init__.py", "PYMODULE"),
        ("websockets", "D:/fake/site-packages/websockets/__init__.py", "PYMODULE"),
    ]
    if extra_module is not None:
        modules.append(
            (extra_module, f"D:/fake/site-packages/{extra_module}/__init__.py", "PYMODULE")
        )
    analysis = [[] for _ in range(20)]
    analysis[14] = modules
    path.write_text(repr(tuple(analysis)), encoding="utf-8")


def test_pyinstaller_analysis_matches_the_license_authority(tmp_path: Path) -> None:
    analysis_path = tmp_path / "Analysis-00.toc"
    _write_fake_pyinstaller_analysis(analysis_path)
    authority = load_component_authority(ROOT / "packaging" / "runtime-components.json")

    observed = validate_pyinstaller_analysis(analysis_path, authority)

    assert "pyinstaller" in observed
    assert "pytest" not in observed
    assert "packaging" not in observed


def test_pyinstaller_analysis_rejects_development_dependency_leaks(tmp_path: Path) -> None:
    analysis_path = tmp_path / "Analysis-00.toc"
    _write_fake_pyinstaller_analysis(analysis_path, extra_module="_pytest")
    authority = load_component_authority(ROOT / "packaging" / "runtime-components.json")

    with pytest.raises(ReleaseValidationError, match="Development distributions"):
        validate_pyinstaller_analysis(analysis_path, authority)
