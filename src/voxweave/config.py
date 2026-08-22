from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

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

DEFAULT_REALTIME_SETTINGS: dict[str, Any] = {
    "model": "",
    "hostapi": "",
    "input_device": "",
    "output_device": "",
    "pitch": 0,
    "f0": "rmvpe",
    "index_rate": 0.72,
    "rms_mix_rate": 0.25,
    "vad_threshold": 0.35,
    "input_gate_db": -30.0,
    "block_seconds": 0.5,
    "test_mode": False,
}


def normalize_realtime_settings(value: Any) -> dict[str, Any]:
    """Return one complete, validated realtime preference record."""
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError("realtime settings must be an object")
    unknown = set(value) - DEFAULT_REALTIME_SETTINGS.keys()
    if unknown:
        raise ValueError(f"unsupported realtime settings: {sorted(unknown)}")
    result = {**DEFAULT_REALTIME_SETTINGS, **value}
    for name in ("model", "hostapi", "input_device", "output_device"):
        if not isinstance(result[name], str):
            raise ValueError(f"realtime.{name} must be a string")
    pitch = result["pitch"]
    if (
        isinstance(pitch, bool)
        or not isinstance(pitch, int | float)
        or not float(pitch).is_integer()
    ):
        raise ValueError("realtime.pitch must be an integer")
    pitch = int(pitch)
    if not -36 <= pitch <= 36:
        raise ValueError("realtime.pitch must be between -36 and 36")
    result["pitch"] = pitch
    if result["f0"] not in {"rmvpe", "fcpe", "pm"}:
        raise ValueError("realtime.f0 must be rmvpe, fcpe, or pm")
    for name, minimum, maximum in (
        ("index_rate", 0.0, 1.0),
        ("rms_mix_rate", 0.0, 1.0),
        ("vad_threshold", 0.1, 0.9),
        ("input_gate_db", -60.0, -20.0),
    ):
        number = result[name]
        if isinstance(number, bool) or not isinstance(number, int | float):
            raise ValueError(f"realtime.{name} must be a number")
        number = float(number)
        if not minimum <= number <= maximum:
            raise ValueError(f"realtime.{name} must be between {minimum} and {maximum}")
        result[name] = number
    block_seconds = result["block_seconds"]
    if isinstance(block_seconds, bool) or not isinstance(block_seconds, int | float):
        raise ValueError("realtime.block_seconds must be a number")
    block_seconds = float(block_seconds)
    if block_seconds not in {0.25, 0.5, 1.0}:
        raise ValueError("realtime.block_seconds must be 0.25, 0.5, or 1.0")
    result["block_seconds"] = block_seconds
    if not isinstance(result["test_mode"], bool):
        raise ValueError("realtime.test_mode must be a boolean")
    return result


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
    temporary = pointer_path.with_suffix(pointer_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"data_root": str(target)}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(pointer_path)


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
    realtime: dict[str, Any] = field(default_factory=dict)
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
        self.realtime = normalize_realtime_settings(self.realtime)
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
    added_realtime_settings = False
    if path.exists():
        payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        migrated_legacy_roots = "model_roots" in payload
        stored_realtime = payload.get("realtime")
        added_realtime_settings = not isinstance(stored_realtime, dict) or bool(
            DEFAULT_REALTIME_SETTINGS.keys() - stored_realtime.keys()
        )
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
        if migrated_legacy_roots or added_realtime_settings:
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
