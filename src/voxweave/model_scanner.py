from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .model_files import candidate_indices, scan_roots
from .model_inspector import ModelInspector
from .model_registry import ModelRegistry


class ModelScanner:
    def __init__(
        self,
        settings: Settings,
        registry: ModelRegistry,
        inspector: ModelInspector,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.inspector = inspector

    def execute(
        self,
        arguments: dict[str, Any],
        progress: Callable[[float, str, str | None], None],
        cancelled: Callable[[], bool],
    ) -> list[dict[str, Any]]:
        result = self.scan(
            arguments.get("weight_roots"),
            arguments.get("index_roots"),
            progress,
            cancelled,
        )
        if arguments.get("remember_roots"):
            weight_roots = list(self.settings.weight_roots or [])
            index_roots = list(self.settings.index_roots or [])
            for value in arguments.get("weight_roots") or []:
                if value not in weight_roots:
                    weight_roots.append(value)
            for value in arguments.get("index_roots") or []:
                if value not in index_roots:
                    index_roots.append(value)
            self.settings.update(weight_roots=weight_roots, index_roots=index_roots)
        return result

    def scan(
        self,
        weight_roots: list[str] | None = None,
        index_roots: list[str] | None = None,
        progress: Callable[[float, str, str | None], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        weights, indices = scan_roots(
            self.settings,
            weight_roots,
            index_roots,
        )
        discovered: list[Path] = []
        for root in weights:
            if not root.is_dir():
                continue
            for model in root.rglob("*.pth"):
                if cancelled and cancelled():
                    raise InterruptedError("task cancellation requested")
                discovered.append(model)
        discovered.sort(key=lambda value: str(value).casefold())
        if progress:
            progress(0.15, "inspecting", f"inspecting {len(discovered)} models")
        inspections = self.inspector.inspect_many(discovered, cancelled) if discovered else {}
        results = []
        total = max(1, len(discovered))
        for index, model in enumerate(discovered):
            if cancelled and cancelled():
                raise InterruptedError("task cancellation requested")
            results.append(
                self.registry.register(
                    model,
                    candidates=candidate_indices(model, indices, cancelled),
                    inspection=inspections[str(model.resolve())],
                )
            )
            if progress:
                progress(
                    0.2 + 0.79 * ((index + 1) / total),
                    "registering",
                    f"registered {index + 1}/{len(discovered)} models",
                )
        return results
