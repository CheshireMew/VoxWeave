from __future__ import annotations

import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .capabilities import MODEL_PROTOCOL, MODEL_PROTOCOL_VERSION
from .database import Database
from .hashing import VerifiedFile, sha256_file, verify_file
from .model_files import family_and_epoch
from .model_repository import ModelRepository
from .protocol import ModelRecommendedParameters, OperationError

DISPLAY_NAMES = {
    "public_yujie_v2": "公开御姐 V2",
    "keruan_v1": "可软 V1",
    "guaiguai_v2": "乖乖 V2",
    "guanguan_v1": "关关 V1",
    "jiazi_v2": "夹子 V2",
    "loli_2888": "萝莉 2888",
    "tingbai_v1": "听白 V1",
    "self_female_v1": "女性版自己 V1",
    "suara_wanita_2": "Bunga 女声 2",
}
ALIASES = {
    "public_yujie_v2": ["公开御姐", "Public Yujie", "Public Yujie V2"],
    "keruan_v1": ["Keruan", "可软", "可软 V1"],
    "guaiguai_v2": ["Guaiguai", "乖乖", "乖乖 V2"],
    "guanguan_v1": ["Guanguan", "关关", "关关 V1"],
    "jiazi_v2": ["Jiazi", "夹子", "夹子 V2"],
    "loli_2888": ["Loli 2888", "萝莉", "萝莉 2888"],
    "tingbai_v1": ["听白", "Tingbai"],
    "suara_wanita_2": ["Bunga", "Suara Wanita"],
}
RECOMMENDED_PITCH = {
    "public_yujie_v2": 9,
    "keruan_v1": 9,
    "guaiguai_v2": 9,
    "guanguan_v1": 9,
    "jiazi_v2": 9,
    "loli_2888": 9,
    "tingbai_v1": 9,
    "self_female_v1": 9,
    "suara_wanita_2": 9,
}
DEFAULT_RECOMMENDED = {
    "pitch": 0,
    "f0": "rmvpe",
    "index_rate": 0.72,
    "rms_mix_rate": 0.25,
    "protect": 0.33,
    "content_mode": "clean",
}


class ModelConflictError(RuntimeError):
    pass


_VERIFIED_SNAPSHOT = "__verified_snapshot__"


@dataclass(frozen=True, slots=True)
class VerifiedModelSnapshot:
    model: VerifiedFile
    index: VerifiedFile | None

    def assert_unchanged(self, record: dict[str, Any]) -> None:
        try:
            self.model.assert_unchanged(Path(record["model_path"]))
            if self.index is not None:
                self.index.assert_unchanged(Path(str(record["index_path"])))
        except OSError as exc:
            raise OperationError(
                "model_changed",
                f"model files changed after verification: {record['id']}",
            ) from exc


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or f"model-{uuid.uuid4().hex[:8]}"


class ModelRegistry:
    def __init__(self, database: Database):
        self.repository = ModelRepository(database)

    def _aliases_in_use(self, excluding: str | None = None) -> dict[str, str]:
        owners: dict[str, str] = {}
        for model in self.list_models():
            if model["id"] == excluding:
                continue
            for value in [model["id"], model["display_name"], *model["aliases"]]:
                owners[value.casefold()] = model["id"]
        return owners

    def register(
        self,
        model: Path,
        *,
        inspection: dict[str, Any],
        index: Path | None = None,
        candidates: list[Path] | None = None,
        model_id: str | None = None,
        display_name: str | None = None,
        aliases: list[str] | None = None,
        source_kind: str = "external",
        license_spdx: str | None = None,
        source_url: str | None = None,
        recommended: dict[str, Any] | None = None,
        verified_model: VerifiedFile | None = None,
        verified_index: VerifiedFile | None = None,
        allow_catalog_update: bool = False,
    ) -> dict[str, Any]:
        model = model.expanduser().resolve()
        if model.suffix.casefold() != ".pth" or not model.is_file():
            raise ValueError(f"RVC model does not exist or is not .pth: {model}")
        if index:
            index = index.expanduser().resolve()
            if index.suffix.casefold() != ".index" or not index.is_file():
                raise ValueError(f"RVC index does not exist or is not .index: {index}")
        family, epoch = family_and_epoch(model.stem)
        model_id = model_id or (
            f"local.{slugify(family)}.e{epoch}"
            if epoch is not None
            else f"local.{slugify(family)}.default"
        )
        display_name = display_name or DISPLAY_NAMES.get(model.stem, model.stem.replace("_", " "))
        if aliases is None:
            aliases = ALIASES.get(model.stem, [])
        aliases = [value.strip() for value in aliases if value.strip()]
        owners = self._aliases_in_use(excluding=model_id)
        for value in [model_id, display_name, *aliases]:
            if value.casefold() in owners:
                raise ModelConflictError(
                    f"name or alias is already owned by {owners[value.casefold()]}: {value}"
                )
        model_hash = (
            verified_model.assert_unchanged(model).sha256
            if verified_model is not None
            else sha256_file(model)
        )
        previous = self.repository.get(model_id)
        if (
            previous
            and previous["model_sha256"] != model_hash
            and not (
                allow_catalog_update
                and source_kind == "catalog"
                and previous.get("source_kind") == "catalog"
            )
        ):
            raise ModelConflictError(f"model id {model_id} already exists with another hash")
        candidates = candidates or ([] if index else [])
        status = inspection.get("status", "invalid")
        if len(candidates) > 1 and index is None:
            status = "index_choice_required"
        if len(candidates) == 1 and index is None:
            index = candidates[0]
        recommended = ModelRecommendedParameters.model_validate(
            {
                **DEFAULT_RECOMMENDED,
                "pitch": RECOMMENDED_PITCH.get(family, 0),
                **(recommended or {}),
            }
        ).model_dump(mode="json")
        now = datetime.now(UTC).isoformat()
        self.repository.save(
            (
                model_id,
                display_name,
                json.dumps(aliases, ensure_ascii=False),
                family,
                epoch,
                str(model),
                model_hash,
                str(index) if index else None,
                (
                    verified_index.assert_unchanged(index).sha256
                    if index and verified_index is not None
                    else sha256_file(index)
                    if index
                    else None
                ),
                json.dumps([str(path) for path in candidates], ensure_ascii=False),
                inspection.get("version"),
                inspection.get("sample_rate"),
                inspection.get("f0"),
                source_kind,
                license_spdx,
                source_url,
                json.dumps(recommended, ensure_ascii=False),
                status,
                now,
            ),
        )
        return self.resolve(model_id)

    def list_models(self) -> list[dict[str, Any]]:
        rows = self.repository.list()
        metadata = self.repository.metadata()
        duplicates: dict[str, list[str]] = {}
        for row in rows:
            duplicates.setdefault(str(row["model_sha256"]), []).append(str(row["id"]))
        results = []
        for row in rows:
            model = Database.decode_json_row(
                row, ("aliases_json", "index_candidates_json", "recommended_json")
            )
            model["protocol"] = MODEL_PROTOCOL
            model["version"] = MODEL_PROTOCOL_VERSION
            model["f0"] = bool(model["f0"]) if model["f0"] is not None else None
            model["archived"] = bool(model.get("archived"))
            user = metadata.get(model["id"], {})
            model["custom_name"] = user.get("custom_name")
            model["tags"] = list(user.get("tags") or [])
            model["favorite"] = bool(user.get("favorite"))
            model["notes"] = str(user.get("notes") or "")
            model["sample_path"] = user.get("sample_path")
            model["cover_path"] = user.get("cover_path")
            model["usage_count"] = int(user.get("usage_count") or 0)
            model["last_used_at"] = user.get("last_used_at")
            model["duplicate_model_ids"] = [
                model_id
                for model_id in duplicates.get(str(model["model_sha256"]), [])
                if model_id != model["id"]
            ]
            model["integrity_status"] = str(
                user.get("integrity_status") or "unchecked"
            )
            model["integrity_checked_at"] = user.get("integrity_checked_at")
            model["integrity_error"] = user.get("integrity_error")
            model["metadata_revision"] = int(user.get("revision") or 0)
            model_path = Path(str(model["model_path"]))
            index_path = Path(str(model["index_path"])) if model.get("index_path") else None
            if not model_path.is_file():
                model["status"] = "missing"
            elif index_path is not None and not index_path.is_file():
                model["status"] = "index_missing"
            results.append(model)
        return results

    def update_metadata(self, arguments: dict[str, Any]) -> dict[str, Any]:
        model_id = str(arguments["model_id"])
        if not self.repository.get(model_id):
            raise LookupError(f"model not found: {model_id}")
        changes = {
            key: arguments[key]
            for key in (
                "custom_name",
                "tags",
                "favorite",
                "notes",
                "sample_path",
                "cover_path",
            )
            if key in arguments
        }
        sample_path = changes.get("sample_path")
        if sample_path and not Path(str(sample_path)).is_file():
            raise FileNotFoundError(str(sample_path))
        cover_path = changes.get("cover_path")
        if cover_path and not Path(str(cover_path)).is_file():
            raise FileNotFoundError(str(cover_path))
        self.repository.update_metadata(
            model_id, int(arguments.get("expected_revision", 0)), changes
        )
        return self.resolve(model_id)

    def is_registered(self, model_id: str) -> bool:
        return self.repository.get(model_id) is not None

    def catalog_state(self, entry: dict[str, Any]) -> dict[str, Any]:
        model_id = str(entry["id"])
        record = self.repository.get(model_id)
        if record is None:
            return {
                "registered": False,
                "installed": False,
                "available": False,
                "archived": False,
                "status": "not_installed",
                "repairable": False,
            }
        model_exists = Path(str(record["model_path"])).is_file()
        index_required = bool(entry.get("index_url"))
        index_exists = bool(record.get("index_path")) and Path(
            str(record["index_path"])
        ).is_file()
        installed = model_exists and (not index_required or index_exists)
        archived = bool(record.get("archived"))
        stored_status = str(record.get("status") or "invalid")
        status = (
            "missing"
            if not model_exists
            else "index_missing"
            if index_required and not index_exists
            else stored_status
        )
        catalog_owned = str(record.get("source_kind") or "") == "catalog"
        return {
            "registered": True,
            "installed": installed,
            "available": installed and status == "ready" and not archived,
            "archived": archived,
            "status": status,
            "repairable": catalog_owned,
        }

    def mark_catalog(self, model_id: str) -> None:
        self.repository.mark_catalog(model_id)

    def set_archived(self, model_id: str, archived: bool) -> dict[str, Any]:
        if not self.repository.get(model_id):
            raise LookupError(f"model not found: {model_id}")
        self.repository.set_archived(model_id, archived)
        return self.resolve(model_id)

    @staticmethod
    def verify_snapshot(model: dict[str, Any]) -> VerifiedModelSnapshot:
        existing = model.get(_VERIFIED_SNAPSHOT)
        if isinstance(existing, VerifiedModelSnapshot):
            existing.assert_unchanged(model)
            return existing
        model_path = Path(model["model_path"])
        if not model_path.is_file():
            raise OperationError("model_missing", f"model file no longer exists: {model_path}")
        try:
            verified_model = verify_file(
                model_path, expected_sha256=str(model["model_sha256"])
            )
        except ValueError as exc:
            raise OperationError(
                "model_changed",
                f"model file changed after registration: {model['id']}",
            ) from exc
        index_path_value = model.get("index_path")
        expected_index_hash = model.get("index_sha256")
        verified_index = None
        if index_path_value:
            index_path = Path(index_path_value)
            if not index_path.is_file():
                raise OperationError(
                    "model_index_missing", f"model index no longer exists: {index_path}"
                )
            if not expected_index_hash:
                raise OperationError(
                    "model_index_changed",
                    f"model index changed after registration: {model['id']}",
                )
            try:
                verified_index = verify_file(
                    index_path, expected_sha256=str(expected_index_hash)
                )
            except ValueError as exc:
                raise OperationError(
                    "model_index_changed",
                    f"model index changed after registration: {model['id']}",
                ) from exc
        snapshot = VerifiedModelSnapshot(verified_model, verified_index)
        model[_VERIFIED_SNAPSHOT] = snapshot
        return snapshot

    def resolve_for_execution(self, selector: str) -> dict[str, Any]:
        model = self.resolve(selector)
        if model.get("archived"):
            raise OperationError("model_unavailable", f"model is archived: {model['id']}")
        if model["status"] != "ready":
            raise OperationError(
                "model_unavailable", f"model is not ready: {model['id']} ({model['status']})"
            )
        self.verify_snapshot(model)
        usage = self.repository.record_usage(model["id"])
        model["usage_count"] = int(usage.get("usage_count") or 0)
        model["last_used_at"] = usage.get("last_used_at")
        return model

    def verify_integrity(
        self,
        arguments: dict[str, Any],
        progress: Any,
        cancelled: Any,
    ) -> dict[str, Any]:
        model_id = str(arguments["model_id"])
        model = self.resolve(model_id)
        progress(0.1, "verifying_model", model_id)
        if cancelled():
            raise InterruptedError("model verification cancelled")
        try:
            self.verify_snapshot(model)
        except FileNotFoundError as error:
            self.repository.set_integrity(model_id, "missing", str(error))
        except OperationError as error:
            status = "missing" if "missing" in error.code else "changed"
            self.repository.set_integrity(model_id, status, str(error))
        except Exception as error:  # noqa: BLE001 - persist explicit integrity result
            self.repository.set_integrity(model_id, "error", str(error))
        else:
            self.repository.set_integrity(model_id, "verified", None)
        progress(0.95, "verifying_model", model_id)
        return self.resolve(model_id)

    def resolve(self, selector: str) -> dict[str, Any]:
        expected = selector.strip().casefold()
        matches = []
        for model in self.list_models():
            values = [model["id"], model["display_name"], *model["aliases"]]
            if any(value.casefold() == expected for value in values):
                matches.append(model)
        if not matches:
            raise LookupError(f"model not found: {selector}")
        if len(matches) > 1:
            raise ModelConflictError(f"model selector is not unique: {selector}")
        return matches[0]
