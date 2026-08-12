from __future__ import annotations

import os
import sys

import pytest

from voxweave.process_control import run_capture


@pytest.mark.skipif(os.name != "nt", reason="Windows console ownership contract")
def test_managed_child_process_has_no_console() -> None:
    completed = run_capture(
        [
            sys.executable,
            "-c",
            "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())",
        ]
    )
    assert completed.returncode == 0
    assert completed.stdout.strip() == "0"
    assert completed.stderr == ""
