from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import platform
import re
import shutil
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = "release-manifest.json"
COMPONENTS_NAME = "runtime-components.json"
ARCHIVE_ROOT = "VoxWeave"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
LICENSE_PREFIXES = ("LICENSE", "LICENCE", "COPYING", "NOTICE")
CURATED_LICENSE_HASHES = {
    "GPL-3.0.txt": "3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986",
    "LGPL-3.0.txt": "e3a994d82e644b03a792a930f574002658412f62407f5fee083f2555c5f23118",
}
REQUIRED_RUNTIME_FILES = (
    "VoxWeave.exe",
    "_internal/python312.dll",
    "_internal/PySide6/Qt6Core.dll",
)
FORBIDDEN_DIRECTORY_NAMES = {
    ".archive",
    ".git",
    ".pytest_cache",
    "build",
    "models",
    "tests",
    "weights",
}
FORBIDDEN_FILE_SUFFIXES = {".index", ".pth"}
FORBIDDEN_BUNDLED_DISTRIBUTIONS = {
    "httpx",
    "jsonschema",
    "packaging",
    "pip",
    "pytest",
    "ruff",
    "setuptools",
    "wheel",
}


class ReleaseValidationError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def load_component_authority(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"Cannot read component authority {path}: {exc}") from exc
    if payload.get("schema_version") != 1:
        raise ReleaseValidationError("Unsupported runtime component authority schema.")
    components = payload.get("components")
    if not isinstance(components, list) or not components:
        raise ReleaseValidationError("Runtime component authority has no components.")
    names = [component.get("distribution") for component in components]
    if any(not isinstance(name, str) or not name for name in names):
        raise ReleaseValidationError("Every runtime component needs a distribution name.")
    if len({name.casefold() for name in names}) != len(names):
        raise ReleaseValidationError(
            "Runtime component authority contains duplicate distributions."
        )
    required_fields = ("version", "license_expression", "source_url")
    for component in components:
        missing = [
            field
            for field in required_fields
            if not isinstance(component.get(field), str) or not component[field]
        ]
        if missing:
            raise ReleaseValidationError(
                f"Runtime component {component['distribution']} is missing {missing}."
            )
    python_authority = payload.get("python")
    if not isinstance(python_authority, dict) or any(
        not isinstance(python_authority.get(field), str) or not python_authority[field]
        for field in ("distribution", *required_fields)
    ):
        raise ReleaseValidationError("Runtime component authority has invalid CPython metadata.")
    return payload


def _safe_component_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip(".-")


def _canonical_distribution_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).casefold()


def validate_pyinstaller_analysis(
    analysis_path: Path,
    authority: dict[str, Any],
) -> list[str]:
    try:
        analysis = ast.literal_eval(analysis_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError) as exc:
        raise ReleaseValidationError(
            f"Cannot read PyInstaller analysis {analysis_path}: {exc}"
        ) from exc
    if not isinstance(analysis, tuple) or len(analysis) < 15:
        raise ReleaseValidationError("Unsupported PyInstaller analysis format.")
    module_entries = []
    for index in (13, 14):
        entries = analysis[index]
        if not isinstance(entries, list):
            raise ReleaseValidationError("Unsupported PyInstaller module inventory format.")
        module_entries.extend(entries)

    package_owners = importlib.metadata.packages_distributions()
    observed = {_canonical_distribution_name("PyInstaller")}
    unknown_modules = []
    for entry in module_entries:
        if not isinstance(entry, tuple) or len(entry) < 2:
            raise ReleaseValidationError("Malformed PyInstaller module inventory entry.")
        module_name, source = entry[:2]
        if not isinstance(module_name, str) or not isinstance(source, str):
            raise ReleaseValidationError("Malformed PyInstaller module name or source.")
        normalized_source = source.replace("\\", "/").casefold()
        if "/site-packages/" not in normalized_source:
            continue
        if "/site-packages/pyinstaller/" in normalized_source:
            owners = ["PyInstaller"]
        elif "/site-packages/_pyinstaller_hooks_contrib/" in normalized_source:
            owners = ["pyinstaller-hooks-contrib"]
        else:
            owners = package_owners.get(module_name.split(".", 1)[0], [])
        if not owners:
            unknown_modules.append(f"{module_name} ({source})")
            continue
        observed.update(_canonical_distribution_name(owner) for owner in owners)
    if unknown_modules:
        raise ReleaseValidationError(
            "Cannot assign bundled modules to installed distributions: "
            + ", ".join(sorted(unknown_modules)[:10])
        )

    forbidden = observed & FORBIDDEN_BUNDLED_DISTRIBUTIONS
    if forbidden:
        raise ReleaseValidationError(
            f"Development distributions entered the release: {sorted(forbidden)}"
        )
    declared = {
        _canonical_distribution_name(component["distribution"])
        for component in authority["components"]
    }
    undeclared = observed - declared
    missing = declared - observed
    if undeclared or missing:
        raise ReleaseValidationError(
            "PyInstaller distribution inventory differs from the release authority: "
            f"undeclared={sorted(undeclared)}, missing={sorted(missing)}"
        )
    return sorted(observed)


def _license_entries(distribution: importlib.metadata.Distribution) -> list[Any]:
    entries = []
    for entry in distribution.files or ():
        parts = PurePosixPath(str(entry).replace("\\", "/")).parts
        if ".." in parts or not parts:
            continue
        if PurePosixPath(*parts).name.upper().startswith(LICENSE_PREFIXES):
            entries.append(entry)
    return sorted(entries, key=lambda value: str(value).casefold())


def collect_component_licenses(
    components: list[dict[str, Any]],
    license_root: Path,
) -> list[dict[str, Any]]:
    collected = []
    for component in components:
        name = component["distribution"]
        expected_version = component.get("version")
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ReleaseValidationError(
                f"Locked runtime component is not installed: {name}"
            ) from exc
        if distribution.version != expected_version:
            raise ReleaseValidationError(
                f"Runtime component version mismatch for {name}: "
                f"expected {expected_version}, found {distribution.version}."
            )
        entries = _license_entries(distribution)
        if not entries:
            raise ReleaseValidationError(f"No license material was installed for {name}.")
        destination_root = license_root / "python" / _safe_component_name(name)
        copied_paths = []
        for entry in entries:
            relative = Path(*PurePosixPath(str(entry).replace("\\", "/")).parts)
            source = Path(distribution.locate_file(entry)).resolve()
            if not source.is_file():
                raise ReleaseValidationError(f"License file for {name} is missing: {source}")
            destination = destination_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            copied_paths.append(destination.relative_to(license_root.parent).as_posix())
        if name.casefold().startswith(("pyside6", "shiboken6")):
            copied_paths.extend(
                (
                    "licenses/GNU/LGPL-3.0.txt",
                    "licenses/GNU/GPL-3.0.txt",
                    "QT_PYSIDE_COMPLIANCE.md",
                )
            )
        collected.append({**component, "license_files": copied_paths})
    return collected


def _copy_release_licenses(
    repository: Path,
    bundle_root: Path,
    authority: dict[str, Any],
) -> list[dict[str, Any]]:
    license_root = bundle_root / "licenses"
    if license_root.exists():
        raise ReleaseValidationError(f"Fresh package unexpectedly contains {license_root}.")
    license_root.mkdir(parents=True)

    for name, expected_hash in CURATED_LICENSE_HASHES.items():
        source = repository / "packaging" / "licenses" / name
        if not source.is_file() or sha256_file(source) != expected_hash:
            raise ReleaseValidationError(f"Curated license text is missing or changed: {source}")
        destination = license_root / "GNU" / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    project_license = repository / "LICENSE"
    project_notice = repository / "LICENSE-NOTICE.md"
    project_licensing = repository / "LICENSING.md"
    notices = repository / "THIRD_PARTY_NOTICES.md"
    qt_notice = repository / "packaging" / "QT_PYSIDE_COMPLIANCE.md"
    for source in (project_license, project_notice, project_licensing, notices, qt_notice):
        if not source.is_file():
            raise ReleaseValidationError(f"Required release notice is missing: {source}")
    project_license_root = license_root / "VoxWeave"
    project_license_root.mkdir(parents=True)
    for source in (project_license, project_notice, project_licensing):
        shutil.copy2(source, project_license_root / source.name)
        shutil.copy2(source, bundle_root / source.name)
    shutil.copy2(notices, bundle_root / notices.name)
    shutil.copy2(qt_notice, bundle_root / qt_notice.name)

    python_authority = authority.get("python", {})
    expected_python = str(python_authority.get("version", ""))
    running_python = f"{sys.version_info.major}.{sys.version_info.minor}"
    if running_python != expected_python:
        raise ReleaseValidationError(
            f"Release Python mismatch: expected {expected_python}, found {running_python}."
        )
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise ReleaseValidationError(f"CPython license is missing: {python_license}")
    python_license_target = license_root / "CPython" / "LICENSE.txt"
    python_license_target.parent.mkdir(parents=True)
    shutil.copy2(python_license, python_license_target)

    authority_target = license_root / COMPONENTS_NAME
    shutil.copy2(repository / "packaging" / COMPONENTS_NAME, authority_target)
    return collect_component_licenses(authority["components"], license_root)


def _release_files(bundle_root: Path) -> list[dict[str, Any]]:
    files = []
    for path in sorted(bundle_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
        if path.is_file() and path != bundle_root / MANIFEST_NAME:
            files.append(
                {
                    "path": path.relative_to(bundle_root).as_posix(),
                    "sha256": sha256_file(path),
                    "size": path.stat().st_size,
                }
            )
    return files


def _timestamp_text(source_date_epoch: int) -> str:
    value = datetime.fromtimestamp(source_date_epoch, UTC)
    return value.isoformat().replace("+00:00", "Z")


def create_release_manifest(
    bundle_root: Path,
    *,
    version: str,
    commit: str,
    source_url: str,
    source_date_epoch: int,
    components: list[dict[str, Any]],
    python_authority: dict[str, Any],
    analysis_distributions: list[str],
) -> Path:
    if not COMMIT_PATTERN.fullmatch(commit):
        raise ReleaseValidationError("Release commit must be a full lowercase Git SHA-1.")
    manifest_path = bundle_root / MANIFEST_NAME
    if manifest_path.exists():
        raise ReleaseValidationError(f"Fresh package unexpectedly contains {manifest_path}.")
    payload = {
        "schema_version": 1,
        "product": "VoxWeave",
        "version": version,
        "target": "windows-x64",
        "source": {"commit": commit, "repository": source_url},
        "source_date_epoch": source_date_epoch,
        "source_timestamp_utc": _timestamp_text(source_date_epoch),
        "build_python": platform.python_version(),
        "pyinstaller_distributions": analysis_distributions,
        "components": [{**python_authority, "license_files": ["licenses/CPython/LICENSE.txt"]}]
        + components,
        "files": _release_files(bundle_root),
    }
    _write_json(manifest_path, payload)
    return manifest_path


def verify_bundle(bundle_root: Path, *, version: str, commit: str) -> dict[str, Any]:
    manifest_path = bundle_root / MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseValidationError(f"Release manifest is unreadable: {exc}") from exc
    if manifest.get("version") != version or manifest.get("source", {}).get("commit") != commit:
        raise ReleaseValidationError("Release manifest provenance does not match the request.")
    if manifest.get("target") != "windows-x64":
        raise ReleaseValidationError("Release manifest target is not windows-x64.")

    declared = manifest.get("files")
    if not isinstance(declared, list) or not declared:
        raise ReleaseValidationError("Release manifest has no file inventory.")
    declared_paths = {item.get("path") for item in declared}
    if len(declared_paths) != len(declared) or any(
        not isinstance(path, str) for path in declared_paths
    ):
        raise ReleaseValidationError("Release manifest contains invalid or duplicate file paths.")
    actual_paths = {
        path.relative_to(bundle_root).as_posix()
        for path in bundle_root.rglob("*")
        if path.is_file() and path != manifest_path
    }
    if actual_paths != declared_paths:
        missing = sorted(declared_paths - actual_paths)
        extra = sorted(actual_paths - declared_paths)
        raise ReleaseValidationError(f"Release inventory differs: missing={missing}, extra={extra}")

    for item in declared:
        relative = PurePosixPath(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ReleaseValidationError(f"Unsafe release path: {relative}")
        path = bundle_root.joinpath(*relative.parts)
        if path.stat().st_size != item.get("size") or sha256_file(path) != item.get("sha256"):
            raise ReleaseValidationError(f"Release file failed integrity verification: {relative}")
        lower_parts = {part.casefold() for part in relative.parts[:-1]}
        if lower_parts & FORBIDDEN_DIRECTORY_NAMES:
            raise ReleaseValidationError(
                f"Forbidden development/runtime data in release: {relative}"
            )
        if relative.suffix.casefold() in FORBIDDEN_FILE_SUFFIXES:
            raise ReleaseValidationError(f"Model artifact must not be bundled: {relative}")

    for relative in REQUIRED_RUNTIME_FILES:
        if relative not in declared_paths:
            raise ReleaseValidationError(f"Required Windows runtime file is missing: {relative}")
    required_notices = {
        "LICENSE",
        "LICENSE-NOTICE.md",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
        "QT_PYSIDE_COMPLIANCE.md",
        "licenses/VoxWeave/LICENSE",
        "licenses/VoxWeave/LICENSE-NOTICE.md",
        "licenses/VoxWeave/LICENSING.md",
        "licenses/GNU/GPL-3.0.txt",
        "licenses/GNU/LGPL-3.0.txt",
        "licenses/CPython/LICENSE.txt",
        f"licenses/{COMPONENTS_NAME}",
    }
    if not required_notices <= declared_paths:
        raise ReleaseValidationError(
            f"Release license closure is incomplete: {sorted(required_notices - declared_paths)}"
        )
    components = manifest.get("components", [])
    if not components or any(not component.get("license_files") for component in components):
        raise ReleaseValidationError("Every bundled component must declare license material.")
    return manifest


def _zip_datetime(source_date_epoch: int) -> tuple[int, int, int, int, int, int]:
    value = datetime.fromtimestamp(max(source_date_epoch, 315532800), UTC)
    return (value.year, value.month, value.day, value.hour, value.minute, value.second)


def create_deterministic_zip(bundle_root: Path, archive_path: Path, source_date_epoch: int) -> None:
    timestamp = _zip_datetime(source_date_epoch)
    with zipfile.ZipFile(
        archive_path,
        "x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as archive:
        for path in sorted(bundle_root.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file():
                continue
            relative = path.relative_to(bundle_root).as_posix()
            info = zipfile.ZipInfo(f"{ARCHIVE_ROOT}/{relative}", timestamp)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            mode = 0o755 if path.suffix.casefold() == ".exe" else 0o644
            info.external_attr = mode << 16
            with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)


def verify_archive(
    archive_path: Path,
    verification_root: Path,
    *,
    version: str,
    commit: str,
) -> dict[str, Any]:
    if verification_root.exists():
        raise ReleaseValidationError(f"Verification target already exists: {verification_root}")
    verification_root.mkdir(parents=True)
    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ReleaseValidationError("Release ZIP contains duplicate entries.")
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise ReleaseValidationError(f"Unsafe ZIP entry: {name}")
            if path.parts[0] != ARCHIVE_ROOT:
                raise ReleaseValidationError(f"ZIP entry is outside {ARCHIVE_ROOT}: {name}")
        archive.extractall(verification_root)
    return verify_bundle(verification_root / ARCHIVE_ROOT, version=version, commit=commit)


def assemble_release(
    *,
    repository: Path,
    bundle_root: Path,
    artifacts_root: Path,
    verification_root: Path,
    version: str,
    commit: str,
    source_url: str,
    source_date_epoch: int,
    analysis_toc: Path | None = None,
) -> dict[str, str]:
    repository = repository.resolve()
    bundle_root = bundle_root.resolve()
    if not bundle_root.is_dir():
        raise ReleaseValidationError(f"PyInstaller bundle does not exist: {bundle_root}")
    if artifacts_root.exists():
        raise ReleaseValidationError(f"Artifact target already exists: {artifacts_root}")
    artifacts_root.mkdir(parents=True)

    authority_path = repository / "packaging" / COMPONENTS_NAME
    authority = load_component_authority(authority_path)
    analysis_distributions = (
        validate_pyinstaller_analysis(analysis_toc.resolve(), authority)
        if analysis_toc is not None
        else sorted(
            _canonical_distribution_name(component["distribution"])
            for component in authority["components"]
        )
    )
    components = _copy_release_licenses(repository, bundle_root, authority)
    manifest_path = create_release_manifest(
        bundle_root,
        version=version,
        commit=commit,
        source_url=source_url,
        source_date_epoch=source_date_epoch,
        components=components,
        python_authority=authority["python"],
        analysis_distributions=analysis_distributions,
    )
    verify_bundle(bundle_root, version=version, commit=commit)

    archive_path = artifacts_root / f"VoxWeave-{version}-windows-x64.zip"
    create_deterministic_zip(bundle_root, archive_path, source_date_epoch)
    archive_hash = sha256_file(archive_path)
    checksum_path = archive_path.with_suffix(archive_path.suffix + ".sha256")
    checksum_path.write_text(
        f"{archive_hash}  {archive_path.name}\n",
        encoding="ascii",
        newline="\n",
    )
    shutil.copy2(manifest_path, artifacts_root / MANIFEST_NAME)
    verify_archive(
        archive_path,
        verification_root,
        version=version,
        commit=commit,
    )
    summary_path = artifacts_root / "release-summary.json"
    _write_json(
        summary_path,
        {
            "schema_version": 1,
            "version": version,
            "commit": commit,
            "target": "windows-x64",
            "archive": archive_path.name,
            "archive_sha256": archive_hash,
            "manifest": MANIFEST_NAME,
            "verification_directory": str(verification_root.resolve()),
        },
    )
    return {
        "archive": str(archive_path),
        "checksum": str(checksum_path),
        "manifest": str(artifacts_root / MANIFEST_NAME),
        "summary": str(summary_path),
        "verification": str(verification_root.resolve()),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Assemble and verify a VoxWeave Windows release.")
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--artifacts-root", type=Path, required=True)
    parser.add_argument("--verification-root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--analysis-toc", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        result = assemble_release(
            repository=arguments.repository,
            bundle_root=arguments.bundle_root,
            artifacts_root=arguments.artifacts_root,
            verification_root=arguments.verification_root,
            version=arguments.version,
            commit=arguments.commit,
            source_url=arguments.source_url,
            source_date_epoch=arguments.source_date_epoch,
            analysis_toc=arguments.analysis_toc,
        )
    except (OSError, ReleaseValidationError, zipfile.BadZipFile) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
