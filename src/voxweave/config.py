from __future__ import annotations

import json
import os
import platform
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .file_lock import InterprocessFileLock
from .parameter_contracts import normalize_realtime_settings
from .runtime_contract import runtime_contract

PACKAGE_ROOT = Path(__file__).resolve().parent


def application_root() -> Path:
    """Return the user-visible application directory in source and frozen builds."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return PACKAGE_ROOT.parents[1]


SOURCE_ROOT = application_root()


def _user_pointer_path() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "VoxWeave" / "location.json"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "VoxWeave" / "location.json"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "voxweave" / "location.json"


PORTABLE_POINTER = SOURCE_ROOT / ".voxweave.local.json"
USER_POINTER = _user_pointer_path()
LOCAL_POINTER = PORTABLE_POINTER

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
        try:
            payload = json.loads(LOCAL_POINTER.read_text(encoding="utf-8"))
            value = payload.get("data_root") if isinstance(payload, dict) else None
            if isinstance(value, str) and value.strip():
                return Path(value).expanduser().resolve()
        except (OSError, ValueError):
            pass
    return _default_data_root().resolve()


def data_root_is_configured() -> bool:
    """Return whether the user deliberately selected a data directory."""

    if os.environ.get("VOXWEAVE_HOME"):
        return True
    if not LOCAL_POINTER.is_file():
        return False
    try:
        payload = json.loads(LOCAL_POINTER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(isinstance(payload, dict) and payload.get("data_root"))


def persist_data_root_pointer(data_root: Path, pointer_path: Path = LOCAL_POINTER) -> None:
    target = data_root.expanduser().resolve()
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    with InterprocessFileLock(pointer_path.with_suffix(pointer_path.suffix + ".lock")):
        temporary = pointer_path.with_name(
            f".{pointer_path.name}.{uuid.uuid4().hex}.tmp"
        )
        temporary.write_text(
            json.dumps({"data_root": str(target)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(pointer_path)


@dataclass(slots=True)
class Settings:
    data_root: str
    revision: int = 0
    rvc_root: str | None = None
    rvc_python: str | None = None
    ffmpeg: str | None = None
    ffprobe: str | None = None
    language: str = "zh-CN"
    hardware_backend: str = "auto"
    separation_backend: str = field(
        default_factory=lambda: runtime_contract().source_separation.backend
    )
    separation_model_id: str = field(
        default_factory=lambda: runtime_contract().source_separation.model_id
    )
    wespeaker_model: str | None = None
    weight_roots: list[str] | None = None
    index_roots: list[str] | None = None
    catalog_urls: list[str] | None = None
    realtime: dict[str, Any] = field(default_factory=dict)
    telemetry_enabled: bool = False

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or self.revision < 0:
            raise ValueError("settings revision must be a non-negative integer")
        if self.weight_roots is None:
            self.weight_roots = []
        if self.index_roots is None:
            self.index_roots = []
        if self.catalog_urls is None:
            self.catalog_urls = []
        self.realtime = normalize_realtime_settings(self.realtime)
        if self.telemetry_enabled:
            raise ValueError("VoxWeave does not implement telemetry")

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
    def runtime_verification_path(self) -> Path:
        return self.state_dir / "runtime-verification.json"

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
            "revision": self.revision,
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
            "realtime": dict(self.realtime),
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

    def replace_with(self, candidate: Settings) -> None:
        """Replace this process-local snapshot with a committed snapshot."""

        if Path(candidate.data_root).resolve() != self.root.resolve():
            raise ValueError("settings snapshots must belong to the same data root")
        for name, value in candidate.payload().items():
            setattr(self, name, value)


class SettingsConflictError(RuntimeError):
    def __init__(self, expected_revision: int, current_revision: int):
        super().__init__(
            f"settings revision conflict: expected {expected_revision}, current {current_revision}"
        )
        self.expected_revision = expected_revision
        self.current_revision = current_revision


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
