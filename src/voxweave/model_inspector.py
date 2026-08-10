from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import PACKAGE_ROOT, Settings
from .process_control import run_capture
from .runtime import resolve_rvc_python


class ModelInspector:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def inspect(self, model: Path) -> dict[str, Any]:
        return self.inspect_many([model])[str(model.resolve())]

    def inspect_many(
        self,
        models: list[Path],
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, dict[str, Any]]:
        python = resolve_rvc_python(self.settings)
        if not python:
            return {str(model.resolve()): {"status": "runtime_missing"} for model in models}
        completed = run_capture(
            [
                python,
                PACKAGE_ROOT / "model_inspect_worker.py",
                *[model.resolve() for model in models],
            ],
            cwd=Path(self.settings.rvc_root) if self.settings.rvc_root else None,
            cancelled=cancelled,
        )
        if completed.returncode != 0:
            error = completed.stderr.strip() or completed.stdout.strip()
            return {
                str(model.resolve()): {"status": "invalid", "error": error}
                for model in models
            }
        return json.loads(completed.stdout.strip().splitlines()[-1])
