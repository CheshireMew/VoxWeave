from __future__ import annotations

import json
import os
import platform
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PACKAGE_ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = PACKAGE_ROOT.parents[1]
LOCAL_POINTER = SOURCE_ROOT / ".voxweave.local.json"


def _default_data_root() -> Path:
    system = platform.system()
    if system == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "VoxWeave"
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "VoxWeave"
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "voxweave"


def resolve_data_root() -> Path:
    explicit = os.environ.get("VOXWEAVE_HOME")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if LOCAL_POINTER.exists():
        payload = json.loads(LOCAL_POINTER.read_text(encoding="utf-8"))
        value = payload.get("data_root")
        if value:
            return Path(value).expanduser().resolve()
    return _default_data_root().resolve()


@dataclass(slots=True)
class Settings:
    data_root: str
    rvc_root: str | None = None
    rvc_python: str | None = None
    ffmpeg: str | None = None
    ffprobe: str | None = None
    language: str = "zh-CN"
    hardware_backend: str = "auto"
    separation_backend: str = "rvc-pymss"
    separation_model_id: str = "vocals-bs-roformer-368"
    wespeaker_model: str | None = None
    weight_roots: list[str] | None = None
    index_roots: list[str] | None = None
    catalog_urls: list[str] | None = None
    telemetry_enabled: bool = False
    _write_lock: threading.RLock = field(
        default_factory=threading.RLock, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if self.weight_roots is None:
            self.weight_roots = []
        if self.index_roots is None:
            self.index_roots = []
        if self.catalog_urls is None:
            self.catalog_urls = []
        if self.telemetry_enabled:
            raise ValueError("VoxWeave 0.1 does not implement telemetry")

    @property
    def root(self) -> Path:
        return Path(self.data_root)

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def downloads_dir(self) -> Path:
        return self.root / "downloads"

    @property
    def components_dir(self) -> Path:
        return self.root / "components"

    @property
    def managed_models_dir(self) -> Path:
        return self.root / "models"

    @property
    def database_path(self) -> Path:
        return self.state_dir / "voxweave.sqlite3"

    @property
    def discovery_path(self) -> Path:
        return self.state_dir / "service.json"

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "service.lock"

    @property
    def config_path(self) -> Path:
        return self.root / "config" / "settings.json"

    def ensure_layout(self) -> None:
        for path in (
            self.root,
            self.state_dir,
            self.cache_dir,
            self.logs_dir,
            self.artifacts_dir,
            self.downloads_dir,
            self.components_dir,
            self.managed_models_dir,
            self.config_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def payload(self) -> dict[str, Any]:
        return {
            "data_root": self.data_root,
            "rvc_root": self.rvc_root,
            "rvc_python": self.rvc_python,
            "ffmpeg": self.ffmpeg,
            "ffprobe": self.ffprobe,
            "language": self.language,
            "hardware_backend": self.hardware_backend,
            "separation_backend": self.separation_backend,
            "separation_model_id": self.separation_model_id,
            "wespeaker_model": self.wespeaker_model,
            "weight_roots": list(self.weight_roots or []),
            "index_roots": list(self.index_roots or []),
            "catalog_urls": list(self.catalog_urls or []),
            "telemetry_enabled": self.telemetry_enabled,
        }

    def updated(self, **changes: Any) -> Settings:
        payload = self.payload()
        unknown = set(changes) - payload.keys()
        if unknown:
            raise ValueError(f"unsupported settings: {sorted(unknown)}")
        payload.update(changes)
        payload["data_root"] = self.data_root
        return Settings(**payload)

    def update(self, **changes: Any) -> None:
        """Atomically persist an update from the owning process."""
        with self._write_lock:
            candidate = self.updated(**changes)
            candidate.ensure_layout()
            temp = candidate.config_path.with_suffix(".json.tmp")
            temp.write_text(
                json.dumps(candidate.payload(), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temp.replace(candidate.config_path)
            for name, value in candidate.payload().items():
                setattr(self, name, value)


def _which(name: str) -> str | None:
    value = shutil.which(name)
    return str(Path(value).resolve()) if value else None


def load_settings(*, create: bool = True) -> Settings:
    root = resolve_data_root()
    path = root / "config" / "settings.json"
    migrated_legacy_roots = False
    if path.exists():
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        migrated_legacy_roots = "model_roots" in payload
        legacy_roots = payload.pop("model_roots", [])
        payload.setdefault("weight_roots", legacy_roots)
        payload.setdefault("index_roots", [])
        payload["data_root"] = str(root)
        settings = Settings(**payload)
    else:
        settings = Settings(
            data_root=str(root),
            ffmpeg=_which("ffmpeg"),
            ffprobe=_which("ffprobe"),
        )
    if create:
        settings.ensure_layout()
        if migrated_legacy_roots:
            settings.update()
    return settings


def configure_process_environment(settings: Settings) -> None:
    temp = settings.root / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    values = {
        "TMP": temp,
        "TEMP": temp,
        "PIP_CACHE_DIR": settings.root / "pip-cache",
        "HF_HOME": settings.cache_dir / "huggingface",
        "QML_DISK_CACHE_PATH": settings.cache_dir / "qml",
    }
    for name, path in values.items():
        path.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(path)
