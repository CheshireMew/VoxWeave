from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

Command = Sequence[str | Path]


def _command_arguments(command: Command) -> list[str]:
    return [str(value) for value in command]


def _platform_process_options() -> dict[str, Any]:
    if os.name == "nt":
        return {
            "creationflags": (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        }
    return {"start_new_session": True}


def start_managed_process(command: Command, **kwargs: Any) -> subprocess.Popen[Any]:
    """Start an application-owned child without exposing a console window."""

    platform_options = _platform_process_options()
    overlap = platform_options.keys() & kwargs.keys()
    if overlap:
        raise ValueError(f"managed process options cannot be overridden: {sorted(overlap)}")
    return subprocess.Popen(
        _command_arguments(command),
        **kwargs,
        **platform_options,
    )


def run_capture(
    command: Command,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    cancelled: Callable[[], bool] | None = None,
    poll_interval: float = 0.2,
) -> subprocess.CompletedProcess[str]:
    """Run a captured child process with the application's cancellation contract."""

    arguments = _command_arguments(command)
    process = start_managed_process(
        arguments,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    while True:
        try:
            stdout, stderr = process.communicate(timeout=poll_interval)
            return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            if cancelled and cancelled():
                terminate_process_tree(process)
                raise InterruptedError("task cancellation requested") from None


def run_logged(
    command: Command,
    *,
    log_path: Path,
    cancelled: Callable[[], bool],
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    poll_interval: float = 0.2,
) -> subprocess.CompletedProcess[None]:
    """Run a child process into an append-only log with the same cancellation contract."""

    arguments = _command_arguments(command)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + subprocess.list2cmdline(arguments) + "\n")
        log.flush()
        process = start_managed_process(
            arguments,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        while process.poll() is None:
            if cancelled():
                terminate_process_tree(process)
                raise InterruptedError("task cancellation requested")
            time.sleep(poll_interval)
    return subprocess.CompletedProcess(arguments, process.returncode, None, None)


def terminate_process_tree(process: subprocess.Popen[Any], timeout: float = 5.0) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **_platform_process_options(),
        )
    else:
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            process.kill()
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                return
        process.wait(timeout=timeout)
