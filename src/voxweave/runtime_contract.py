from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .verified_download import DownloadSpec

RESOURCE_PATH = Path(__file__).resolve().parent / "resources" / "runtime_components.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _object(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    return value


def _keys(
    value: dict[str, Any],
    path: str,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
) -> None:
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing:
        raise ValueError(f"{path} is missing fields: {sorted(missing)}")
    if extra:
        raise ValueError(f"{path} has unknown fields: {sorted(extra)}")


def _string(value: object, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{path} must be a non-empty string")
    return value


def _optional_string(value: object, path: str) -> str | None:
    return None if value is None else _string(value, path)


def _positive_int(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{path} must be a positive integer")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _strings(value: object, path: str, *, length: int | None = None) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError(f"{path} must be an array")
    result = tuple(_string(item, f"{path}[{index}]") for index, item in enumerate(value))
    if length is not None and len(result) != length:
        raise ValueError(f"{path} must contain exactly {length} items")
    return result


@dataclass(frozen=True, slots=True)
class RuntimeArtifact:
    filename: str
    url: str
    size_bytes: int
    sha256: str
    version: str | None = None
    revision: str | None = None
    repository: str | None = None

    @classmethod
    def from_dict(cls, value: object, path: str) -> RuntimeArtifact:
        data = _object(value, path)
        _keys(
            data,
            path,
            required={"filename", "url", "size_bytes", "sha256"},
            optional={"version", "revision", "repository"},
        )
        sha256 = _string(data["sha256"], f"{path}.sha256")
        if not SHA256_PATTERN.fullmatch(sha256):
            raise ValueError(f"{path}.sha256 must be 64 lowercase hexadecimal characters")
        return cls(
            filename=_string(data["filename"], f"{path}.filename"),
            url=_string(data["url"], f"{path}.url"),
            size_bytes=_positive_int(data["size_bytes"], f"{path}.size_bytes"),
            sha256=sha256,
            version=_optional_string(data.get("version"), f"{path}.version"),
            revision=_optional_string(data.get("revision"), f"{path}.revision"),
            repository=_optional_string(data.get("repository"), f"{path}.repository"),
        )

    def download_spec(self) -> DownloadSpec:
        from .verified_download import DownloadSpec  # noqa: PLC0415

        return DownloadSpec(
            url=self.url,
            filename=self.filename,
            size_bytes=self.size_bytes,
            sha256=self.sha256,
        )


@dataclass(frozen=True, slots=True)
class PackagePlan:
    packages: tuple[str, ...]
    requirements_file: str | None
    index: str
    extra_index: str

    @classmethod
    def from_dict(cls, value: object, path: str) -> PackagePlan:
        data = _object(value, path)
        _keys(
            data,
            path,
            required={"index", "extra_index"},
            optional={"packages", "requirements_file"},
        )
        packages = _strings(data.get("packages", []), f"{path}.packages")
        requirements_file = _optional_string(
            data.get("requirements_file"), f"{path}.requirements_file"
        )
        if not packages and requirements_file is None:
            raise ValueError(f"{path} must declare packages or a requirements file")
        return cls(
            packages=packages,
            requirements_file=requirements_file,
            index=_string(data["index"], f"{path}.index"),
            extra_index=_string(data["extra_index"], f"{path}.extra_index"),
        )


@dataclass(frozen=True, slots=True)
class RuntimeAssets:
    repo_id: str
    revision: str
    base_patterns: tuple[str, ...]
    hubert_file: str
    rmvpe_file: str

    @classmethod
    def from_dict(cls, value: object, path: str) -> RuntimeAssets:
        data = _object(value, path)
        required = {"repo_id", "revision", "base_patterns", "hubert_file", "rmvpe_file"}
        _keys(data, path, required=required)
        return cls(
            repo_id=_string(data["repo_id"], f"{path}.repo_id"),
            revision=_string(data["revision"], f"{path}.revision"),
            base_patterns=_strings(data["base_patterns"], f"{path}.base_patterns"),
            hubert_file=_string(data["hubert_file"], f"{path}.hubert_file"),
            rmvpe_file=_string(data["rmvpe_file"], f"{path}.rmvpe_file"),
        )

    @property
    def required_files(self) -> tuple[str, str]:
        return self.hubert_file, self.rmvpe_file

    @property
    def rmvpe_filename(self) -> str:
        return Path(self.rmvpe_file).name

    @property
    def rmvpe_directory(self) -> str:
        return str(Path(self.rmvpe_file).parent)


@dataclass(frozen=True, slots=True)
class SeparationComponent:
    backend: str
    model_id: str
    source: str
    weight_files: tuple[str, str]
    code_license_spdx: str
    model_license_spdx: str
    distribution_allowed: bool
    requires_nvidia: bool

    @classmethod
    def from_dict(cls, value: object, path: str) -> SeparationComponent:
        data = _object(value, path)
        required = {
            "backend",
            "model_id",
            "source",
            "weight_files",
            "code_license_spdx",
            "model_license_spdx",
            "distribution_allowed",
            "requires_nvidia",
        }
        _keys(data, path, required=required)
        weights = _strings(data["weight_files"], f"{path}.weight_files", length=2)
        return cls(
            backend=_string(data["backend"], f"{path}.backend"),
            model_id=_string(data["model_id"], f"{path}.model_id"),
            source=_string(data["source"], f"{path}.source"),
            weight_files=(weights[0], weights[1]),
            code_license_spdx=_string(data["code_license_spdx"], f"{path}.code_license_spdx"),
            model_license_spdx=_string(data["model_license_spdx"], f"{path}.model_license_spdx"),
            distribution_allowed=_boolean(
                data["distribution_allowed"], f"{path}.distribution_allowed"
            ),
            requires_nvidia=_boolean(data["requires_nvidia"], f"{path}.requires_nvidia"),
        )

    @property
    def model_file(self) -> str:
        return self.weight_files[0]

    @property
    def config_file(self) -> str:
        return self.weight_files[1]


@dataclass(frozen=True, slots=True)
class SpeakerComponent:
    backend: str
    install_directory: str
    repo_id: str
    source: str
    revision: str
    filename: str
    code_license_spdx: str
    model_license_spdx: str

    @classmethod
    def from_dict(cls, value: object, path: str) -> SpeakerComponent:
        data = _object(value, path)
        required = {
            "backend",
            "install_directory",
            "repo_id",
            "source",
            "revision",
            "filename",
            "code_license_spdx",
            "model_license_spdx",
        }
        _keys(data, path, required=required)
        return cls(**{name: _string(data[name], f"{path}.{name}") for name in required})


@dataclass(frozen=True, slots=True)
class PlatformRuntimeContract:
    python: RuntimeArtifact
    rvc_source: RuntimeArtifact
    ffmpeg: RuntimeArtifact
    package_indexes: dict[str, str]
    package_plans: dict[str, PackagePlan]
    runtime_assets: RuntimeAssets
    source_separation: SeparationComponent
    speaker_embedding: SpeakerComponent

    @classmethod
    def from_dict(cls, value: object, path: str) -> PlatformRuntimeContract:
        data = _object(value, path)
        required = {
            "python",
            "rvc_source",
            "ffmpeg",
            "package_indexes",
            "package_plans",
            "runtime_assets",
            "source_separation",
            "speaker_embedding",
        }
        _keys(data, path, required=required)
        raw_indexes = _object(data["package_indexes"], f"{path}.package_indexes")
        indexes = {
            _string(name, f"{path}.package_indexes key"): _string(
                url, f"{path}.package_indexes.{name}"
            )
            for name, url in raw_indexes.items()
        }
        raw_plans = _object(data["package_plans"], f"{path}.package_plans")
        plans = {
            _string(name, f"{path}.package_plans key"): PackagePlan.from_dict(
                plan, f"{path}.package_plans.{name}"
            )
            for name, plan in raw_plans.items()
        }
        missing = {
            reference
            for plan in plans.values()
            for reference in (plan.index, plan.extra_index)
            if reference not in indexes
        }
        if missing:
            raise ValueError(f"{path} references unknown package indexes: {sorted(missing)}")
        return cls(
            python=RuntimeArtifact.from_dict(data["python"], f"{path}.python"),
            rvc_source=RuntimeArtifact.from_dict(data["rvc_source"], f"{path}.rvc_source"),
            ffmpeg=RuntimeArtifact.from_dict(data["ffmpeg"], f"{path}.ffmpeg"),
            package_indexes=indexes,
            package_plans=plans,
            runtime_assets=RuntimeAssets.from_dict(
                data["runtime_assets"], f"{path}.runtime_assets"
            ),
            source_separation=SeparationComponent.from_dict(
                data["source_separation"], f"{path}.source_separation"
            ),
            speaker_embedding=SpeakerComponent.from_dict(
                data["speaker_embedding"], f"{path}.speaker_embedding"
            ),
        )

    def pip_arguments(self, plan_name: str) -> list[str | Path]:
        plan = self.package_plans[plan_name]
        arguments: list[str | Path] = [*plan.packages]
        if plan.requirements_file is not None:
            arguments.extend(["-r", RESOURCE_PATH.parent / plan.requirements_file])
        arguments.extend(
            [
                "--index-url",
                self.package_indexes[plan.index],
                "--extra-index-url",
                self.package_indexes[plan.extra_index],
            ]
        )
        return arguments


def _platform_key() -> str:
    if platform.system() == "Windows" and platform.machine().casefold() in {"amd64", "x86_64"}:
        return "windows-x86_64"
    raise RuntimeError("the managed installer currently supports Windows x64 only")


def runtime_contract_json() -> dict[str, object]:
    value = json.loads(RESOURCE_PATH.read_text(encoding="utf-8"))
    return _object(value, "runtime contract")


@lru_cache(maxsize=1)
def runtime_contract() -> PlatformRuntimeContract:
    document = runtime_contract_json()
    _keys(document, "runtime contract", required={"version", "platforms"})
    if document["version"] != 2:
        raise ValueError("runtime contract.version must be 2")
    platforms = _object(document["platforms"], "runtime contract.platforms")
    key = _platform_key()
    if key not in platforms:
        raise ValueError(f"runtime contract does not define platform {key}")
    return PlatformRuntimeContract.from_dict(platforms[key], f"runtime contract.platforms.{key}")
