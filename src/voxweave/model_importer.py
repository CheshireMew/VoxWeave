from __future__ import annotations

import urllib.request
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .hashing import sha256_file
from .model_catalog import ModelCatalogClient
from .model_files import candidate_indices, index_roots_for_model
from .model_inspector import ModelInspector
from .model_registry import ModelRegistry
from .staging import archive_failed_staging


class ModelImporter:
    def __init__(
        self,
        settings: Settings,
        registry: ModelRegistry,
        inspector: ModelInspector,
        catalog: ModelCatalogClient,
    ) -> None:
        self.settings = settings
        self.registry = registry
        self.inspector = inspector
        self.catalog = catalog

    @staticmethod
    def _download(
        url: str,
        target: Path,
        expected_size: int,
        expected_sha256: str,
        progress: Callable[[float, str, str | None], None],
        cancelled: Callable[[], bool],
        progress_start: float,
        progress_end: float,
    ) -> None:
        request = urllib.request.Request(url, headers={"User-Agent": "VoxWeave/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response, target.open("xb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) != expected_size:
                raise ValueError("download size does not match declared size")
            received = 0
            while chunk := response.read(1024 * 1024):
                if cancelled():
                    raise InterruptedError("task cancellation requested")
                output.write(chunk)
                received += len(chunk)
                fraction = min(1.0, received / max(1, expected_size))
                progress(
                    progress_start + (progress_end - progress_start) * fraction,
                    "download",
                    f"{received}/{expected_size} bytes",
                )
        if target.stat().st_size != expected_size:
            raise ValueError("downloaded file size mismatch")
        if sha256_file(target).casefold() != expected_sha256.casefold():
            raise ValueError("downloaded file hash mismatch")

    def _publish(
        self,
        staged_model_root: Path,
        arguments: dict[str, Any],
        inspection: dict[str, Any],
        *,
        has_index: bool,
    ) -> dict[str, Any]:
        model_id = arguments["id"]
        target = self.settings.managed_models_dir / model_id
        staged_model_root.replace(target)
        try:
            return self.registry.register(
                target / "model.pth",
                index=target / "model.index" if has_index else None,
                model_id=model_id,
                display_name=arguments["display_name"],
                aliases=arguments.get("aliases", []),
                source_kind="url",
                license_spdx=arguments["license_spdx"],
                source_url=arguments.get("source_url") or arguments["model"],
                inspection=inspection,
                recommended=arguments.get("recommended"),
            )
        except Exception:
            failed_root = self.settings.root / "model-import-failed"
            failed_root.mkdir(parents=True, exist_ok=True)
            target.replace(failed_root / f"{model_id}-{uuid.uuid4().hex}")
            raise

    def import_model(
        self,
        arguments: dict[str, Any],
        progress: Callable[[float, str, str | None], None],
        cancelled: Callable[[], bool],
        task_id: str,
    ) -> dict[str, Any]:
        model_value = str(arguments["model"])
        if not model_value.lower().startswith("https://"):
            return self._import_local(arguments, progress, cancelled)
        return self._import_url(arguments, progress, cancelled, task_id)

    def _import_local(
        self,
        arguments: dict[str, Any],
        progress: Callable[[float, str, str | None], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        if cancelled():
            raise InterruptedError("task cancellation requested")
        progress(0.2, "validating", "safely inspecting local model")
        local_model = Path(arguments["model"]).expanduser().resolve()
        if arguments.get("model_sha256") and (
            sha256_file(local_model).casefold() != arguments["model_sha256"].casefold()
        ):
            raise ValueError("local model hash does not match expected SHA-256")
        index_value = arguments.get("index")
        index = Path(index_value).expanduser().resolve() if index_value else None
        if index and arguments.get("index_sha256") and (
            sha256_file(index).casefold() != arguments["index_sha256"].casefold()
        ):
            raise ValueError("local index hash does not match expected SHA-256")
        return self.registry.register(
            local_model,
            index=index,
            candidates=(
                None
                if index
                else candidate_indices(
                    local_model,
                    index_roots_for_model(self.settings, local_model),
                    cancelled,
                )
            ),
            model_id=arguments.get("id"),
            display_name=arguments.get("display_name"),
            aliases=arguments.get("aliases"),
            license_spdx=arguments.get("license_spdx"),
            source_url=arguments.get("source_url"),
            inspection=self.inspector.inspect(local_model),
            recommended=arguments.get("recommended"),
        )

    def _import_url(
        self,
        arguments: dict[str, Any],
        progress: Callable[[float, str, str | None], None],
        cancelled: Callable[[], bool],
        task_id: str,
    ) -> dict[str, Any]:
        model_id = arguments["id"]
        target = self.settings.managed_models_dir / model_id
        if target.exists():
            registered = self.registry.is_registered(model_id)
            existing_model = target / "model.pth"
            if registered and existing_model.is_file() and (
                sha256_file(existing_model).casefold()
                == arguments["model_sha256"].casefold()
            ):
                return self.registry.resolve(model_id)
            failed_root = self.settings.root / "model-import-failed"
            failed_root.mkdir(parents=True, exist_ok=True)
            if registered:
                raise FileExistsError(
                    f"managed model id already contains different data: {model_id}"
                )
            target.replace(failed_root / f"{model_id}-{uuid.uuid4().hex}")
        staging = self.settings.downloads_dir / "model-import" / task_id
        staging.mkdir(parents=True, exist_ok=False)
        with archive_failed_staging(
            staging,
            self.settings.root / "model-import-failed",
            f"{model_id}-{task_id}",
        ):
            downloaded_model = staging / "model.pth"
            self._download(
                arguments["model"],
                downloaded_model,
                int(arguments["download_size_bytes"]),
                arguments["model_sha256"],
                progress,
                cancelled,
                0.05,
                0.65,
            )
            downloaded_index = None
            if arguments.get("index_url"):
                downloaded_index = staging / "model.index"
                self._download(
                    arguments["index_url"],
                    downloaded_index,
                    int(arguments["index_size_bytes"]),
                    arguments["index_sha256"],
                    progress,
                    cancelled,
                    0.65,
                    0.85,
                )
            progress(0.88, "validating", "safely inspecting downloaded model")
            inspection = self.inspector.inspect(downloaded_model)
            if inspection.get("status") != "ready":
                raise ValueError(
                    inspection.get("error") or "downloaded model is not RVC-compatible"
                )
            if cancelled():
                raise InterruptedError("task cancellation requested")
            return self._publish(
                staging,
                arguments,
                inspection,
                has_index=downloaded_index is not None,
            )

    def install_from_catalog(
        self,
        catalog_url: str | None,
        model_id: str,
        progress: Callable[[float, str, str | None], None],
        cancelled: Callable[[], bool],
        task_id: str,
    ) -> dict[str, Any]:
        arguments = self.catalog.import_arguments(catalog_url, model_id, cancelled)
        result = self.import_model(arguments, progress, cancelled, task_id)
        self.registry.mark_catalog(result["id"])
        return self.registry.resolve(result["id"])
