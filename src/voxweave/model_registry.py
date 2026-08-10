from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .database import Database
from .hashing import sha256_file
from .model_files import family_and_epoch
from .model_repository import ModelRepository
from .protocol import OperationError

DISPLAY_NAMES = {
    "public_yujie_v2": "公开御姐 V2",
    "keruan_v1": "Keruan V1",
    "guaiguai_v2": "Guaiguai V2",
    "tingbai_v1": "听白 V1",
    "self_female_v1": "女性版自己 V1",
    "suara_wanita_2": "Bunga / Suara Wanita 2",
}
ALIASES = {
    "public_yujie_v2": ["公开御姐", "Public Yujie", "Public Yujie V2"],
    "keruan_v1": ["Keruan", "可软", "可软 V1"],
    "guaiguai_v2": ["Guaiguai", "乖乖", "乖乖 V2"],
    "tingbai_v1": ["听白", "Tingbai"],
    "suara_wanita_2": ["Bunga", "Suara Wanita"],
}
RECOMMENDED_PITCH = {
    "public_yujie_v2": 9,
    "keruan_v1": 9,
    "guaiguai_v2": 9,
    "tingbai_v1": 9,
    "self_female_v1": 6,
    "suara_wanita_2": 7,
}


class ModelConflictError(RuntimeError):
    pass


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
        model_hash = sha256_file(model)
        previous = self.repository.get(model_id)
        if previous and previous["model_sha256"] != model_hash:
            raise ModelConflictError(f"model id {model_id} already exists with another hash")
        candidates = candidates or ([] if index else [])
        status = inspection.get("status", "invalid")
        if len(candidates) > 1 and index is None:
            status = "index_choice_required"
        if len(candidates) == 1 and index is None:
            index = candidates[0]
        recommended = {
            "pitch": RECOMMENDED_PITCH.get(family, 0),
            "f0": "rmvpe",
            "index_rate": 0.72,
            "rms_mix_rate": 0.25,
            "protect": 0.33,
            "content_mode": "clean",
        }
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
                sha256_file(index) if index else None,
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
        results = []
        for row in rows:
            model = Database.decode_json_row(
                row, ("aliases_json", "index_candidates_json", "recommended_json")
            )
            model["protocol"] = "voxweave-rvc-model"
            model["version"] = 1
            model["f0"] = bool(model["f0"]) if model["f0"] is not None else None
            results.append(model)
        return results

    def is_registered(self, model_id: str) -> bool:
        return self.repository.get(model_id) is not None

    def mark_catalog(self, model_id: str) -> None:
        self.repository.mark_catalog(model_id)

    @staticmethod
    def verify_snapshot(model: dict[str, Any]) -> None:
        model_path = Path(model["model_path"])
        if not model_path.is_file():
            raise OperationError("model_missing", f"model file no longer exists: {model_path}")
        actual_model_hash = sha256_file(model_path)
        if actual_model_hash.casefold() != str(model["model_sha256"]).casefold():
            raise OperationError(
                "model_changed",
                f"model file changed after registration: {model['id']}",
            )
        index_path_value = model.get("index_path")
        expected_index_hash = model.get("index_sha256")
        if index_path_value:
            index_path = Path(index_path_value)
            if not index_path.is_file():
                raise OperationError(
                    "model_index_missing", f"model index no longer exists: {index_path}"
                )
            actual_index_hash = sha256_file(index_path)
            if not expected_index_hash or actual_index_hash.casefold() != str(
                expected_index_hash
            ).casefold():
                raise OperationError(
                    "model_index_changed",
                    f"model index changed after registration: {model['id']}",
                )

    def resolve_for_execution(self, selector: str) -> dict[str, Any]:
        model = self.resolve(selector)
        if model["status"] != "ready":
            raise OperationError(
                "model_unavailable", f"model is not ready: {model['id']} ({model['status']})"
            )
        self.verify_snapshot(model)
        return model

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
