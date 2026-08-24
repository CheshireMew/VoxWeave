from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .hashing import VerifiedFile, sha256_file, verify_file
from .model_catalog import ModelCatalogClient
from .model_files import candidate_indices, index_roots_for_model
from .model_inspector import ModelInspector
from .model_registry import ModelRegistry
from .staging import archive_failed_staging
from .verified_download import DownloadSpec, download_verified


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

    def _publish(
        self,
        staged_model_root: Path,
        arguments: dict[str, Any],
        inspection: dict[str, Any],
        *,
        has_index: bool,
        verified_model: VerifiedFile | None = None,
        verified_index: VerifiedFile | None = None,
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
                source_kind=str(arguments.get("source_kind") or "url"),
                license_spdx=arguments["license_spdx"],
                source_url=arguments.get("source_url") or arguments["model"],
                inspection=inspection,
                recommended=arguments.get("recommended"),
                verified_model=(
                    verified_model.rebind(target / "model.pth")
                    if verified_model is not None
                    else None
                ),
                verified_index=(
                    verified_index.rebind(target / "model.index")
                    if verified_index is not None
                    else None
                ),
                allow_catalog_update=arguments.get("source_kind") == "catalog",
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
        verified_model = None
        if arguments.get("model_sha256"):
            try:
                verified_model = verify_file(
                    local_model, expected_sha256=arguments["model_sha256"]
                )
            except ValueError as exc:
                raise ValueError("local model hash does not match expected SHA-256") from exc
        index_value = arguments.get("index")
        index = Path(index_value).expanduser().resolve() if index_value else None
        verified_index = None
        if index and arguments.get("index_sha256"):
            try:
                verified_index = verify_file(
                    index, expected_sha256=arguments["index_sha256"]
                )
            except ValueError as exc:
                raise ValueError("local index hash does not match expected SHA-256") from exc
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
            verified_model=verified_model,
            verified_index=verified_index,
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
        registered_record = self.registry.repository.get(model_id)
        expected_index_hash = str(arguments.get("index_sha256") or "").casefold()

        def registered_catalog_owned() -> bool:
            return registered_record is not None and (
                str(registered_record.get("source_kind") or "") == "catalog"
            )

        def existing_files_match() -> bool:
            existing_model = target / "model.pth"
            if not existing_model.is_file() or (
                sha256_file(existing_model).casefold()
                != str(arguments["model_sha256"]).casefold()
            ):
                return False
            if not arguments.get("index_url"):
                return True
            existing_index = target / "model.index"
            return existing_index.is_file() and (
                sha256_file(existing_index).casefold() == expected_index_hash
            )

        if target.exists():
            registered = registered_record is not None
            if registered and existing_files_match():
                return self.registry.resolve(model_id)
            failed_root = self.settings.root / "model-import-failed"
            failed_root.mkdir(parents=True, exist_ok=True)
            if registered and not registered_catalog_owned():
                raise FileExistsError(
                    f"managed model id already contains different data: {model_id}"
                )
            target.replace(failed_root / f"{model_id}-repair-{uuid.uuid4().hex}")
        elif registered_record is not None and not registered_catalog_owned():
            raise FileExistsError(
                f"model id is already registered from another source: {model_id}"
            )
        staging = self.settings.downloads_dir / "model-import" / task_id
        staging.mkdir(parents=True, exist_ok=False)
        with archive_failed_staging(
            staging,
            self.settings.root / "model-import-failed",
            f"{model_id}-{task_id}",
        ):
            downloaded_model = staging / "model.pth"
            downloaded_model_verified = download_verified(
                DownloadSpec(
                    url=arguments["model"],
                    filename="model.pth",
                    size_bytes=int(arguments["download_size_bytes"]),
                    sha256=arguments["model_sha256"].casefold(),
                ),
                downloaded_model,
                progress=progress,
                cancelled=cancelled,
                progress_start=0.05,
                progress_end=0.65,
            )
            downloaded_index = None
            if arguments.get("index_url"):
                downloaded_index = staging / "model.index"
                downloaded_index_verified = download_verified(
                    DownloadSpec(
                        url=arguments["index_url"],
                        filename="model.index",
                        size_bytes=int(arguments["index_size_bytes"]),
                        sha256=arguments["index_sha256"].casefold(),
                    ),
                    downloaded_index,
                    progress=progress,
                    cancelled=cancelled,
                    progress_start=0.65,
                    progress_end=0.85,
                )
            else:
                downloaded_index_verified = None
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
                verified_model=downloaded_model_verified,
                verified_index=downloaded_index_verified,
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
        arguments["source_kind"] = "catalog"
        result = self.import_model(arguments, progress, cancelled, task_id)
        self.registry.mark_catalog(result["id"])
        return self.registry.resolve(result["id"])
