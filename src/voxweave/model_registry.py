from __future__ import annotations

import json
import re
import subprocess
import unicodedata
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import PACKAGE_ROOT, Settings
from .database import Database
from .hashing import sha256_file
from .runtime import resolve_rvc_python

CHECKPOINT_PATTERN = re.compile(r"^(?P<family>.+)_e(?P<epoch>\d+)_s\d+$", re.IGNORECASE)
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


def _family_and_epoch(stem: str) -> tuple[str, int | None]:
    match = CHECKPOINT_PATTERN.match(stem)
    if match:
        return match.group("family"), int(match.group("epoch"))
    return stem, None


def _candidate_indices(model: Path, index_roots: list[Path]) -> list[Path]:
    stem = model.stem.casefold()
    family, _ = _family_and_epoch(stem)
    tokens = {stem, family.casefold()}
    candidates: list[Path] = []
    for root in index_roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.index"):
            index_stem = path.stem.casefold()
            if index_stem.startswith("trained_"):
                continue
            if any(token in index_stem or index_stem in token for token in tokens):
                candidates.append(path.resolve())
    ordered = sorted(set(candidates), key=lambda value: str(value).casefold())
    unique_by_hash: dict[str, Path] = {}
    for path in ordered:
        digest = sha256_file(path)
        existing = unique_by_hash.get(digest)
        if existing is None or "assets\\indices" in str(path).casefold():
            unique_by_hash[digest] = path
    return sorted(unique_by_hash.values(), key=lambda value: str(value).casefold())


def _safe_inspect(settings: Settings, model: Path) -> dict[str, Any]:
    return _safe_inspect_many(settings, [model])[str(model.resolve())]


def _safe_inspect_many(settings: Settings, models: list[Path]) -> dict[str, dict[str, Any]]:
    python = resolve_rvc_python(settings)
    if not python:
        return {str(model.resolve()): {"status": "runtime_missing"} for model in models}
    completed = subprocess.run(
        [
            str(python),
            str(PACKAGE_ROOT / "model_inspect_worker.py"),
            *[str(model.resolve()) for model in models],
        ],
        check=False,
        text=True,
        capture_output=True,
        cwd=Path(settings.rvc_root) if settings.rvc_root else None,
    )
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip()
        return {str(model.resolve()): {"status": "invalid", "error": error} for model in models}
    return json.loads(completed.stdout.strip().splitlines()[-1])


class ModelRegistry:
    def __init__(self, database: Database, settings: Settings):
        self.database = database
        self.settings = settings

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
        index: Path | None = None,
        candidates: list[Path] | None = None,
        model_id: str | None = None,
        display_name: str | None = None,
        aliases: list[str] | None = None,
        source_kind: str = "external",
        license_spdx: str | None = None,
        source_url: str | None = None,
        inspection: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        model = model.expanduser().resolve()
        if model.suffix.casefold() != ".pth" or not model.is_file():
            raise ValueError(f"RVC model does not exist or is not .pth: {model}")
        if index:
            index = index.expanduser().resolve()
            if index.suffix.casefold() != ".index" or not index.is_file():
                raise ValueError(f"RVC index does not exist or is not .index: {index}")
        family, epoch = _family_and_epoch(model.stem)
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
        previous = self.database.fetch_one("SELECT * FROM models WHERE id=?", (model_id,))
        if previous and previous["model_sha256"] != model_hash:
            raise ModelConflictError(f"model id {model_id} already exists with another hash")
        inspection = inspection or _safe_inspect(self.settings, model)
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
        self.database.execute(
            """
            INSERT INTO models(
              id,display_name,aliases_json,family,checkpoint_epoch,model_path,model_sha256,
              index_path,index_sha256,index_candidates_json,rvc_version,sample_rate,f0,source_kind,
              license_spdx,source_url,recommended_json,status,imported_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              display_name=excluded.display_name,aliases_json=excluded.aliases_json,
              model_path=excluded.model_path,index_path=excluded.index_path,
              index_sha256=excluded.index_sha256,index_candidates_json=excluded.index_candidates_json,
              rvc_version=excluded.rvc_version,sample_rate=excluded.sample_rate,f0=excluded.f0,
              source_kind=excluded.source_kind,license_spdx=excluded.license_spdx,
              source_url=excluded.source_url,recommended_json=excluded.recommended_json,
              status=excluded.status
            """,
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

    def _index_roots_for_model(self, model: Path) -> list[Path]:
        roots = [model.parent]
        if self.settings.rvc_root:
            for candidate in (
                Path(self.settings.rvc_root) / "assets" / "indices",
                Path(self.settings.rvc_root) / "logs",
            ):
                if candidate.is_dir():
                    roots.append(candidate.resolve())
        return sorted(set(roots), key=lambda value: str(value).casefold())

    def _download(
        self,
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

    def import_model(
        self,
        arguments: dict[str, Any],
        progress: Callable[[float, str, str | None], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        model_value = str(arguments["model"])
        if not model_value.lower().startswith("https://"):
            progress(0.2, "validating", "safely inspecting local model")
            local_model = Path(model_value)
            if arguments.get("model_sha256") and (
                sha256_file(local_model).casefold() != arguments["model_sha256"].casefold()
            ):
                raise ValueError("local model hash does not match expected SHA-256")
            index = arguments.get("index")
            if (
                index
                and arguments.get("index_sha256")
                and (sha256_file(Path(index)).casefold() != arguments["index_sha256"].casefold())
            ):
                raise ValueError("local index hash does not match expected SHA-256")
            result = self.register(
                local_model,
                index=Path(index) if index else None,
                candidates=(
                    None
                    if index
                    else _candidate_indices(
                        local_model.resolve(), self._index_roots_for_model(local_model.resolve())
                    )
                ),
                model_id=arguments.get("id"),
                display_name=arguments.get("display_name"),
                aliases=arguments.get("aliases"),
                license_spdx=arguments.get("license_spdx"),
                source_url=arguments.get("source_url"),
            )
            progress(1.0, "completed", result["display_name"])
            return result

        model_id = arguments["id"]
        target = self.settings.managed_models_dir / model_id
        if target.exists():
            raise FileExistsError(f"managed model directory already exists: {target}")
        download_root = self.settings.downloads_dir / f"model-import-{uuid.uuid4()}"
        download_root.mkdir(parents=True, exist_ok=False)
        downloaded_model = download_root / "model.pth"
        self._download(
            model_value,
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
            downloaded_index = download_root / "model.index"
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
        inspection = _safe_inspect(self.settings, downloaded_model)
        if inspection.get("status") != "ready":
            raise ValueError(inspection.get("error") or "downloaded model is not RVC-compatible")
        target.mkdir(parents=True, exist_ok=False)
        model_path = target / "model.pth"
        downloaded_model.replace(model_path)
        index_path = None
        if downloaded_index:
            index_path = target / "model.index"
            downloaded_index.replace(index_path)
        result = self.register(
            model_path,
            index=index_path,
            model_id=model_id,
            display_name=arguments["display_name"],
            aliases=arguments.get("aliases", []),
            source_kind="url",
            license_spdx=arguments["license_spdx"],
            source_url=arguments.get("source_url") or model_value,
            inspection=inspection,
        )
        progress(1.0, "completed", result["display_name"])
        return result

    def scan(
        self, weight_roots: list[str] | None = None, index_roots: list[str] | None = None
    ) -> list[dict[str, Any]]:
        weights = [
            Path(path).expanduser().resolve()
            for path in (weight_roots or self.settings.model_roots)
        ]
        if self.settings.rvc_root:
            default = Path(self.settings.rvc_root) / "assets" / "weights"
            if default.is_dir() and default not in weights:
                weights.append(default.resolve())
        indices = [Path(path).expanduser().resolve() for path in (index_roots or [])]
        if self.settings.rvc_root:
            for default in (
                Path(self.settings.rvc_root) / "assets" / "indices",
                Path(self.settings.rvc_root) / "logs",
            ):
                if default.is_dir() and default.resolve() not in indices:
                    indices.append(default.resolve())
        results: list[dict[str, Any]] = []
        discovered = [
            model
            for root in weights
            if root.is_dir()
            for model in sorted(root.rglob("*.pth"), key=lambda value: str(value).casefold())
        ]
        inspections = _safe_inspect_many(self.settings, discovered) if discovered else {}
        for model in discovered:
            candidates = _candidate_indices(model, indices)
            results.append(
                self.register(
                    model,
                    candidates=candidates,
                    inspection=inspections.get(str(model.resolve())),
                )
            )
        return results

    def list_models(self) -> list[dict[str, Any]]:
        rows = self.database.fetch_all("SELECT * FROM models ORDER BY family,checkpoint_epoch,id")
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

    def install_from_catalog(
        self,
        catalog_url: str,
        model_id: str,
        progress: Callable[[float, str, str | None], None],
        cancelled: Callable[[], bool],
    ) -> dict[str, Any]:
        if not catalog_url.lower().startswith("https://"):
            raise ValueError("catalog URL must use HTTPS")
        with urllib.request.urlopen(catalog_url, timeout=60) as response:
            catalog = json.load(response)
        if catalog.get("protocol") != "voxweave-model-catalog" or catalog.get("version") != 1:
            raise ValueError("unsupported VoxWeave model catalog")
        entry = next(
            (item for item in catalog.get("models", []) if item.get("id") == model_id), None
        )
        if not entry:
            raise LookupError(f"catalog model not found: {model_id}")
        if not entry.get("license_spdx"):
            raise ValueError("catalog model has no SPDX license")
        required = {"model_url", "model_sha256", "model_size_bytes", "display_name"}
        missing = required - set(entry)
        if missing:
            raise ValueError(f"catalog model is missing fields: {sorted(missing)}")
        arguments = {
            "model": entry["model_url"],
            "id": entry["id"],
            "display_name": entry["display_name"],
            "aliases": entry.get("aliases", []),
            "license_spdx": entry["license_spdx"],
            "source_url": entry.get("source_url") or catalog_url,
            "model_sha256": entry["model_sha256"],
            "download_size_bytes": entry["model_size_bytes"],
        }
        if entry.get("index_url"):
            index_required = {"index_sha256", "index_size_bytes"} - set(entry)
            if index_required:
                raise ValueError(f"catalog index is missing fields: {sorted(index_required)}")
            arguments.update(
                {
                    "index_url": entry["index_url"],
                    "index_sha256": entry["index_sha256"],
                    "index_size_bytes": entry["index_size_bytes"],
                }
            )
        result = self.import_model(arguments, progress, cancelled)
        self.database.execute("UPDATE models SET source_kind='catalog' WHERE id=?", (result["id"],))
        return self.resolve(result["id"])
