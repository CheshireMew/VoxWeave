from __future__ import annotations

import json
import os
import platform
import shutil
from dataclasses import asdict, dataclass
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
    model_roots: list[str] | None = None
    catalog_urls: list[str] | None = None
    telemetry_enabled: bool = False

    def __post_init__(self) -> None:
        if self.model_roots is None:
            self.model_roots = []
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
            self.artifacts_dir,
            self.downloads_dir,
            self.components_dir,
            self.managed_models_dir,
            self.config_path.parent,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def save(self) -> None:
        self.ensure_layout()
        temp = self.config_path.with_suffix(".json.tmp")
        temp.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temp.replace(self.config_path)


def _which(name: str) -> str | None:
    value = shutil.which(name)
    return str(Path(value).resolve()) if value else None


def load_settings(*, create: bool = True) -> Settings:
    root = resolve_data_root()
    path = root / "config" / "settings.json"
    if path.exists():
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
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
