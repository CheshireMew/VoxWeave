from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import __version__
from .config import Settings, persist_data_root_pointer
from .file_lock import InterprocessFileLock
from .hashing import sha256_file

RELEASES_URL = "https://api.github.com/repos/CheshireMew/VoxWeave/releases"
RELEASES_PAGE_URL = "https://github.com/CheshireMew/VoxWeave/releases"
Progress = Callable[[float, str, str | None], None]
Cancelled = Callable[[], bool]


def _version_tuple(value: str) -> tuple[int, ...]:
    numbers = re.findall(r"\d+", value.removeprefix("v").split("-", 1)[0])
    return tuple(int(number) for number in numbers) or (0,)


class UpdateService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def state_path(self) -> Path:
        return self.settings.state_dir / "update-installation.json"

    def _state(self) -> dict[str, Any]:
        if not self.state_path.is_file():
            executable = Path(sys.executable).resolve()
            return {
                "protocol": "voxweave-update-installation",
                "version": 1,
                "active_version": __version__,
                "active_executable": str(executable),
                "installations": {
                    __version__: {
                        "version": __version__,
                        "state": "active",
                        "install_path": str(executable.parent),
                        "executable_path": str(executable),
                        "archive_path": "",
                        "sha256": "",
                        "previous_version": None,
                        "bootstrap_command": [],
                        "error": None,
                    }
                },
            }
        payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        if payload.get("protocol") != "voxweave-update-installation":
            raise ValueError("unsupported update installation state")
        active_version = str(payload.get("active_version") or __version__)
        active_executable = Path(
            payload.get("active_executable") or sys.executable
        ).resolve()
        payload.setdefault("installations", {}).setdefault(
            active_version,
            {
                "version": active_version,
                "state": "active",
                "install_path": str(active_executable.parent),
                "executable_path": str(active_executable),
                "archive_path": "",
                "sha256": "",
                "previous_version": None,
                "bootstrap_command": [],
                "error": None,
            },
        )
        return payload

    def _write_state(self, payload: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with InterprocessFileLock(self.state_path.with_suffix(".json.lock")):
            temporary = self.state_path.with_name(
                f".{self.state_path.name}.{uuid.uuid4().hex}.tmp"
            )
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.state_path)

    @staticmethod
    def _bootstrap_command(state_path: Path, version: str, token: str) -> list[str]:
        return [
            sys.executable,
            *([] if getattr(sys, "frozen", False) else ["-m", "voxweave.app"]),
            "--voxweave-update-bootstrap",
            str(state_path),
            version,
            token,
        ]

    @staticmethod
    def _request_json(url: str) -> Any:
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"VoxWeave/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            url,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 403 and error.headers.get("X-RateLimit-Remaining") == "0":
                reset_value = error.headers.get("X-RateLimit-Reset")
                retry = "later"
                if reset_value and reset_value.isdigit():
                    retry = datetime.fromtimestamp(int(reset_value), UTC).isoformat()
                raise RuntimeError(
                    "GitHub API rate limit exceeded; retry after "
                    f"{retry}, open {RELEASES_PAGE_URL}, or set GITHUB_TOKEN"
                ) from error
            raise

    def _release(self, include_prerelease: bool, version: str | None = None) -> dict[str, Any]:
        releases = self._request_json(RELEASES_URL)
        for release in releases:
            tag = str(release.get("tag_name") or "").removeprefix("v")
            if release.get("draft") or (release.get("prerelease") and not include_prerelease):
                continue
            if version is None or tag == version.removeprefix("v"):
                return release
        raise LookupError(f"VoxWeave release not found: {version or 'latest'}")

    @staticmethod
    def _windows_asset(release: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [
            asset
            for asset in release.get("assets") or []
            if str(asset.get("name") or "").casefold().endswith(".zip")
            and "windows" in str(asset.get("name") or "").casefold()
        ]
        return candidates[0] if candidates else None

    def _public(self, release: dict[str, Any]) -> dict[str, Any]:
        version = str(release.get("tag_name") or "").removeprefix("v")
        asset = self._windows_asset(release)
        return {
            "current_version": __version__,
            "latest_version": version,
            "update_available": _version_tuple(version) > _version_tuple(__version__),
            "prerelease": bool(release.get("prerelease")),
            "release_name": str(release.get("name") or release.get("tag_name") or version),
            "release_url": str(release.get("html_url") or ""),
            "published_at": release.get("published_at"),
            "notes": str(release.get("body") or "")[:8000],
            "download_size_bytes": int((asset or {}).get("size") or 0),
            "downloaded_path": None,
            "sha256": None,
        }

    def check(
        self, arguments: dict[str, Any], progress: Progress, cancelled: Cancelled, _task_id: str
    ) -> dict[str, Any]:
        progress(0.1, "checking_update", "reading GitHub releases")
        if cancelled():
            raise InterruptedError("update check cancelled")
        release = self._release(bool(arguments.get("include_prerelease", False)))
        progress(0.95, "checking_update", "release metadata verified")
        return self._public(release)

    @staticmethod
    def _expected_digest(asset: dict[str, Any]) -> str | None:
        digest = str(asset.get("digest") or "")
        return digest.split(":", 1)[1] if digest.startswith("sha256:") else None

    def download(
        self, arguments: dict[str, Any], progress: Progress, cancelled: Cancelled, task_id: str
    ) -> dict[str, Any]:
        version = str(arguments["version"]).removeprefix("v")
        release = self._release(True, version)
        asset = self._windows_asset(release)
        if not asset:
            raise LookupError(f"Windows release archive is unavailable for {version}")
        expected = self._expected_digest(asset)
        if not expected:
            raise RuntimeError("release archive has no publisher SHA-256 digest")
        target_dir = self.settings.downloads_dir / "updates" / version
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / str(asset["name"])
        partial = target_dir / f".{asset['name']}.{task_id}.partial"
        if target.is_file():
            actual = sha256_file(target, cancelled=cancelled)
            if actual.casefold() != expected.casefold():
                raise FileExistsError(
                    f"existing update archive has a different digest: {target}"
                )
            result = self._public(release)
            result.update(downloaded_path=str(target), sha256=actual)
            return result
        request = urllib.request.Request(
            str(asset["browser_download_url"]),
            headers={"User-Agent": f"VoxWeave/{__version__}"},
        )
        digest = hashlib.sha256()
        downloaded = 0
        total = max(1, int(asset.get("size") or 0))
        try:
            with urllib.request.urlopen(  # noqa: S310
                request, timeout=30
            ) as response, partial.open("xb") as writer:
                while chunk := response.read(1024 * 1024):
                    if cancelled():
                        raise InterruptedError("update download cancelled")
                    writer.write(chunk)
                    digest.update(chunk)
                    downloaded += len(chunk)
                    progress(
                        min(0.95, downloaded / total * 0.9),
                        "downloading_update",
                        asset["name"],
                    )
        except Exception:
            if partial.exists():
                failed = partial.with_suffix(partial.suffix + ".failed")
                partial.replace(failed)
            raise
        actual = digest.hexdigest()
        if actual.casefold() != expected.casefold():
            partial.replace(partial.with_suffix(partial.suffix + ".checksum-failed"))
            raise RuntimeError("downloaded update SHA-256 does not match publisher digest")
        if target.exists():
            raise FileExistsError(target)
        partial.replace(target)
        result = self._public(release)
        result.update(downloaded_path=str(target), sha256=actual)
        return result

    def install(
        self,
        arguments: dict[str, Any],
        progress: Progress,
        cancelled: Cancelled,
        task_id: str,
    ) -> dict[str, Any]:
        version = str(arguments["version"]).removeprefix("v")
        update_dir = self.settings.downloads_dir / "updates" / version
        archives = sorted(update_dir.glob("*.zip"))
        if len(archives) != 1:
            raise LookupError(f"expected one downloaded Windows archive for {version}")
        archive = archives[0].resolve()
        archive_hash = sha256_file(archive, cancelled=cancelled)
        install_root = self.settings.components_dir / "app-versions" / version
        state = self._state()
        existing = state["installations"].get(version)
        if install_root.exists():
            if existing and Path(existing["executable_path"]).is_file():
                return dict(existing)
            raise FileExistsError(f"unmanaged update install directory exists: {install_root}")
        staging = install_root.with_name(f".{version}.install-{task_id}")
        if staging.exists():
            raise FileExistsError(staging)
        staging.mkdir(parents=True)
        try:
            with zipfile.ZipFile(archive) as package:
                entries = package.infolist()
                total = max(1, sum(entry.file_size for entry in entries))
                extracted = 0
                for entry in entries:
                    if cancelled():
                        raise InterruptedError("update installation cancelled")
                    entry_path = Path(entry.filename.replace("/", "\\"))
                    if entry_path.is_absolute() or ".." in entry_path.parts:
                        raise ValueError(f"unsafe update archive path: {entry.filename}")
                    if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError(
                            f"update archive contains a symbolic link: {entry.filename}"
                        )
                    target = (staging / entry_path).resolve()
                    if staging.resolve() not in target.parents and target != staging.resolve():
                        raise ValueError(f"unsafe update archive path: {entry.filename}")
                    if entry.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with package.open(entry) as source, target.open("xb") as destination:
                        while chunk := source.read(1024 * 1024):
                            if cancelled():
                                raise InterruptedError("update installation cancelled")
                            destination.write(chunk)
                            extracted += len(chunk)
                            progress(
                                min(0.9, extracted / total * 0.9),
                                "installing_update",
                                entry.filename,
                            )
            executables = list(staging.rglob("VoxWeave.exe"))
            if len(executables) != 1:
                raise ValueError("Windows update archive must contain exactly one VoxWeave.exe")
            relative_executable = executables[0].relative_to(staging)
            persist_data_root_pointer(
                self.settings.root,
                executables[0].parent / ".voxweave.local.json",
            )
            staging.replace(install_root)
            executable = install_root / relative_executable
        except Exception:
            if staging.exists():
                failures = self.settings.components_dir / "update-failures"
                failures.mkdir(parents=True, exist_ok=True)
                staging.replace(failures / f"{staging.name}-{uuid.uuid4().hex}")
            raise
        record = {
            "version": version,
            "state": "installed",
            "install_path": str(install_root),
            "executable_path": str(executable),
            "archive_path": str(archive),
            "sha256": archive_hash,
            "previous_version": state.get("active_version"),
            "bootstrap_command": [],
            "error": None,
        }
        state["installations"][version] = record
        self._write_state(state)
        progress(0.98, "verifying_update", str(executable))
        return dict(record)

    def activate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        version = str(arguments["version"]).removeprefix("v")
        state = self._state()
        record = state["installations"].get(version)
        if not record or not Path(record["executable_path"]).is_file():
            raise LookupError(f"installed update not found: {version}")
        token = uuid.uuid4().hex
        record.update(
            state="pending",
            previous_version=state.get("active_version"),
            bootstrap_command=self._bootstrap_command(self.state_path, version, token),
            error=None,
        )
        state["pending"] = {
            "version": version,
            "token": token,
            "previous_version": state.get("active_version"),
            "previous_executable": state.get("active_executable"),
        }
        self._write_state(state)
        return dict(record)

    def rollback(self, arguments: dict[str, Any]) -> dict[str, Any]:
        state = self._state()
        requested = arguments.get("version")
        if requested:
            version = str(requested).removeprefix("v")
        else:
            candidates = [
                value
                for value in state["installations"].values()
                if value["version"] != state.get("active_version")
                and Path(value["executable_path"]).is_file()
            ]
            if not candidates:
                raise LookupError("no installed rollback version is available")
            version = str(candidates[-1]["version"])
        return self.activate({"version": version})

    def mark_healthy(self, token: str) -> None:
        state = self._state()
        pending = state.get("pending") or {}
        if pending.get("token") != token:
            return
        marker = self.settings.state_dir / "update-health" / f"{token}.json"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(
            json.dumps({"token": token, "healthy_at": datetime.now(UTC).isoformat()}) + "\n",
            encoding="utf-8",
        )
