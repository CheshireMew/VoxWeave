from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_desktop_launcher_uses_the_windowless_runtime_path() -> None:
    batch = (ROOT / "VoxWeave.bat").read_text(encoding="utf-8").lower()
    script_source = (ROOT / "VoxWeave.vbs").read_text(encoding="utf-8")
    script = script_source.lower()
    powershell = (ROOT / "scripts" / "run.ps1").read_text(encoding="utf-8").lower()

    assert script_source.isascii()
    assert "wscript.exe" in batch
    assert "powershell.exe" not in batch
    assert "-windowless" in script
    assert "shell.run(command, 0, true)" in script
    assert "pythonw.exe" in powershell
    assert "start-process" in powershell
