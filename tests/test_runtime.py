from __future__ import annotations

import os
import sys
import time

import pytest

from voxweave.config import Settings
from voxweave.runtime_install import _run_install_step, install_runtime


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

    def fail_command(_command, *, cancelled, log_path, env=None):
        assert cancelled() is False
        assert env is None
        log_path.write_text("clone started\n", encoding="utf-8")
        raise InterruptedError("task cancellation requested")

    monkeypatch.setattr("voxweave.runtime_install._run_install_step", fail_command)
    with pytest.raises(InterruptedError, match="cancellation requested"):
        install_runtime(
            settings,
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
