from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from voxweave.config import Settings
from voxweave.database import Database
from voxweave.runtime_install import (
    _install_dependencies,
    _replace_directory,
    _require_install_space,
    _run_install_step,
    install_runtime,
)
from voxweave.settings_repository import SettingsRepository
from voxweave.settings_service import SettingsService


def _settings_service(settings: Settings) -> SettingsService:
    return SettingsService(settings, SettingsRepository(Database(settings.database_path)))


def test_runtime_contract_loads_in_dependency_isolated_worker_python() -> None:
    package_root = Path(__file__).parents[1] / "src" / "voxweave"
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(package_root)!r}); "
        "from runtime_contract import runtime_contract; "
        "contract = runtime_contract(); "
        "print(contract.runtime_assets.hubert_file)"
    )
    completed = subprocess.run(
        [sys.executable, "-S", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "hubert_base/pytorch_model.bin"


def test_runtime_install_rejects_data_directory_without_safe_free_space(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_root=str(tmp_path))
    monkeypatch.setattr(
        "voxweave.runtime_install.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=1),
    )
    with pytest.raises(RuntimeError, match="Not enough free space"):
        _require_install_space(settings)


def test_runtime_publish_retries_transient_windows_directory_lock(tmp_path, monkeypatch) -> None:
    source = tmp_path / "staging"
    destination = tmp_path / "runtime"
    source.mkdir()
    original_replace = Path.replace
    attempts = 0

    def transient_replace(path: Path, target: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("scanner still holds the directory")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", transient_replace)
    monkeypatch.setattr("voxweave.runtime_install.time.sleep", lambda _delay: None)

    _replace_directory(source, destination)

    assert attempts == 2
    assert destination.is_dir()


def test_existing_rvc_only_downloads_missing_ffmpeg(tmp_path, monkeypatch) -> None:
    settings = Settings(
        data_root=str(tmp_path),
        rvc_root=str(tmp_path / "existing-rvc"),
        rvc_python=str(tmp_path / "existing-rvc" / ".venv" / "Scripts" / "python.exe"),
    )
    settings.ensure_layout()
    ffmpeg = tmp_path / "components" / "ffmpeg.exe"
    ffprobe = tmp_path / "components" / "ffprobe.exe"
    ffmpeg.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.write_bytes(b"ffmpeg")
    ffprobe.write_bytes(b"ffprobe")

    def inspect(candidate, _cancelled):
        if candidate.ffmpeg == str(ffmpeg) and candidate.ffprobe == str(ffprobe):
            return {"ready": True, "doctor": {"ok": True}}
        return {"ready": False, "doctor": {"ok": True}}

    monkeypatch.setattr("voxweave.runtime_install.inspect_runtime", inspect)
    monkeypatch.setattr(
        "voxweave.runtime_install._ensure_managed_ffmpeg",
        lambda *_args: (ffmpeg, ffprobe),
    )
    monkeypatch.setattr(
        "voxweave.runtime_install._ensure_managed_python",
        lambda *_args: pytest.fail("existing RVC must not download another Python"),
    )
    monkeypatch.setattr(
        "voxweave.runtime_install._require_install_space",
        lambda *_args: pytest.fail("a small FFmpeg repair must not require 12 GiB"),
    )

    result = install_runtime(
        settings,
        _settings_service(settings),
        {},
        lambda _value, _stage, _detail: None,
        lambda: False,
        "runtime-task",
    )

    assert result["ready"] is True
    assert settings.ffmpeg == str(ffmpeg)
    assert settings.ffprobe == str(ffprobe)


def test_nvidia_runtime_installs_pinned_cuda_torch_before_rvc_dependencies(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()
    commands: list[list[str | Path]] = []

    monkeypatch.setattr("voxweave.runtime_install.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "voxweave.runtime_install.shutil.which",
        lambda name: r"C:\Windows\System32\nvidia-smi.exe" if name == "nvidia-smi" else None,
    )
    monkeypatch.setattr(
        "voxweave.runtime_install._run_install_step",
        lambda command, **_kwargs: commands.append(command),
    )

    _install_dependencies(
        settings,
        Path(r"D:\runtime\python.exe"),
        False,
        lambda: False,
        tmp_path / "install.log",
        lambda _value, _stage, _detail: None,
    )

    assert commands[0][4:6] == ["torch==2.7.1+cu118", "torchaudio==2.7.1+cu118"]
    assert commands[0][-4:] == [
        "--index-url",
        "https://mirrors.nju.edu.cn/pytorch/whl/cu118",
        "--extra-index-url",
        "https://mirrors.pku.edu.cn/pypi/simple",
    ]
    requirements = (
        Path(__file__).parents[1]
        / "src"
        / "voxweave"
        / "resources"
        / "runtime_requirements_windows.txt"
    )
    assert commands[1][4:6] == [
        "-r",
        requirements,
    ]
    assert commands[1][-4:] == [
        "--index-url",
        "https://mirrors.pku.edu.cn/pypi/simple",
        "--extra-index-url",
        "https://pypi.org/simple",
    ]


@pytest.mark.skipif(
    os.name != "nt", reason="the currently supported process-tree contract is Windows"
)
def test_install_command_cancellation_terminates_process_tree(tmp_path) -> None:
    child_pid = tmp_path / "child.pid"
    child_code = (
        "import os,time,pathlib;"
        f"pathlib.Path({str(child_pid)!r}).write_text(str(os.getpid()));"
        "time.sleep(30)"
    )
    parent_code = (
        "import subprocess,sys,time;"
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}]);"
        "time.sleep(30)"
    )
    started = time.monotonic()

    def cancelled() -> bool:
        return child_pid.exists() and time.monotonic() - started > 0.2

    with pytest.raises(InterruptedError, match="cancellation requested"):
        _run_install_step(
            [sys.executable, "-c", parent_code],
            cancelled=cancelled,
            log_path=tmp_path / "install.log",
        )
    assert time.monotonic() - started < 8
    pid = int(child_pid.read_text())
    time.sleep(0.2)
    with pytest.raises(OSError):
        os.kill(pid, 0)
    assert "$ " in (tmp_path / "install.log").read_text(encoding="utf-8")


def test_failed_runtime_install_archives_staging_and_does_not_change_settings(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(data_root=str(tmp_path))
    settings.ensure_layout()

    def fail_checkout(
        _settings,
        _component,
        _staging,
        _bootstrap_python,
        cancelled,
        log_path,
        _progress,
    ):
        assert cancelled() is False
        log_path.write_text("clone started\n", encoding="utf-8")
        raise InterruptedError("task cancellation requested")

    ffmpeg = tmp_path / "ffmpeg.exe"
    ffprobe = tmp_path / "ffprobe.exe"
    ffmpeg.write_bytes(b"test")
    ffprobe.write_bytes(b"test")
    monkeypatch.setattr(
        "voxweave.runtime_install._ensure_managed_python",
        lambda *_args: Path(sys.executable),
    )
    monkeypatch.setattr(
        "voxweave.runtime_install._ensure_managed_ffmpeg",
        lambda *_args: (ffmpeg, ffprobe),
    )
    monkeypatch.setattr("voxweave.runtime_install._checkout_runtime_source", fail_checkout)
    with pytest.raises(InterruptedError, match="cancellation requested"):
        install_runtime(
            settings,
            _settings_service(settings),
            {},
            lambda _value, _stage, _detail: None,
            lambda: False,
            "runtime-task",
        )

    assert not (settings.root / "runtime" / "staging" / "runtime-task").exists()
    failures = list((settings.root / "runtime" / "failed").glob("staging-runtime-task-*"))
    assert len(failures) == 1
    assert (failures[0] / "install.log").read_text(encoding="utf-8") == "clone started\n"
    assert settings.rvc_root is None
    assert settings.rvc_python is None
