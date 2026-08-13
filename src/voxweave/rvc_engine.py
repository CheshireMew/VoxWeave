from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import Settings
from .hashing import sha256_file
from .model_registry import ModelRegistry
from .process_control import run_capture
from .runtime import resolve_rvc_entry, resolve_rvc_python


class RvcEngineError(RuntimeError):
    pass


def _bounded(name: str, value: float, minimum: float, maximum: float) -> float:
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


class RvcEngine:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _parameters(parameters: dict[str, Any]) -> dict[str, Any]:
        values = {
            "pitch": int(parameters.get("pitch", 0)),
            "f0": parameters.get("f0", "rmvpe"),
            "index_rate": _bounded(
                "index_rate", float(parameters.get("index_rate", 0.72)), 0.0, 1.0
            ),
            "rms_mix_rate": _bounded(
                "rms_mix_rate", float(parameters.get("rms_mix_rate", 0.25)), 0.0, 1.0
            ),
            "protect": _bounded("protect", float(parameters.get("protect", 0.33)), 0.0, 0.5),
        }
        if values["f0"] not in {"rmvpe", "fcpe", "pm"}:
            raise ValueError("f0 must be rmvpe, fcpe, or pm")
        return values

    def _runtime(self) -> tuple[Path, Path]:
        python = resolve_rvc_python(self.settings)
        entry = resolve_rvc_entry(self.settings)
        if not python or not entry:
            raise RvcEngineError("RVC runtime is not configured")
        return python, entry

    def audio_devices(self) -> dict[str, Any]:
        python, entry = self._runtime()
        command = [
            str(python),
            "-B",
            str(entry),
            "--rvc-root",
            str(Path(self.settings.rvc_root).resolve()),
            "devices",
        ]
        return self._run_worker(command, entry, None)

    def realtime_worker_command(self) -> tuple[list[str], Path]:
        python, entry = self._runtime()
        command = [
            str(python),
            "-B",
            str(entry),
            "--rvc-root",
            str(Path(self.settings.rvc_root).resolve()),
            "realtime",
        ]
        return command, entry

    def realtime_payload(
        self,
        model: dict[str, Any],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        values = self._parameters(parameters)
        payload = {
            "command": "start",
            "model_id": model["id"],
            "model": model["model_path"],
            "index": model.get("index_path"),
            "pitch": values["pitch"],
            "f0": values["f0"],
            "index_rate": values["index_rate"],
            "rms_mix_rate": values["rms_mix_rate"],
            "input_device": int(parameters["input_device"]),
            "output_device": int(parameters["output_device"]),
            "block_seconds": float(parameters["block_seconds"]),
            "crossfade_seconds": float(parameters.get("crossfade_seconds", 0.05)),
            "extra_seconds": float(parameters.get("extra_seconds", 2.5)),
            "vad_threshold": float(parameters.get("vad_threshold", 0.35)),
            "input_gate_db": float(parameters.get("input_gate_db", -40.0)),
        }
        converter_identity = {
            "model_sha256": model["model_sha256"],
            "index_sha256": model.get("index_sha256"),
            "pitch": values["pitch"],
            "index_rate": values["index_rate"],
        }
        payload["converter_key"] = hashlib.sha256(
            json.dumps(converter_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_identity = {
            **payload,
            "input_device_name": parameters.get("input_device_name"),
            "output_device_name": parameters.get("output_device_name"),
            "input_device_sample_rate": parameters.get("input_device_sample_rate"),
            "output_device_sample_rate": parameters.get("output_device_sample_rate"),
            "hostapi": parameters.get("hostapi"),
        }
        payload["cache_key"] = hashlib.sha256(
            json.dumps(cache_identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        payload["test_mode"] = bool(parameters.get("test_mode", False))
        return payload

    @staticmethod
    def _run_worker(
        command: list[str], entry: Path, cancelled: Callable[[], bool] | None
    ) -> dict[str, Any]:
        completed = run_capture(command, cwd=entry.parent, cancelled=cancelled)
        stdout = completed.stdout
        stderr = completed.stderr
        lines = [line for line in stdout.splitlines() if line.strip()]
        payload: dict[str, Any] | None = None
        for line in reversed(lines):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        if completed.returncode != 0 or not payload or not payload.get("ok"):
            detail = (payload or {}).get("error") or stderr.strip() or stdout.strip()
            raise RvcEngineError(detail)
        return payload

    def _command(
        self,
        python: Path,
        entry: Path,
        subcommand: str,
        model: dict[str, Any],
        values: dict[str, Any],
    ) -> list[str]:
        command = [
            str(python),
            "-B",
            str(entry),
            "--rvc-root",
            str(Path(self.settings.rvc_root).resolve()),
            subcommand,
            "--model",
            model["model_path"],
            "--pitch",
            str(values["pitch"]),
            "--f0",
            values["f0"],
            "--index-rate",
            str(values["index_rate"]),
            "--rms-mix-rate",
            str(values["rms_mix_rate"]),
            "--protect",
            str(values["protect"]),
        ]
        if model.get("index_path"):
            command.extend(["--index", model["index_path"]])
        return command

    def convert(
        self,
        input_path: Path,
        output_path: Path,
        model: dict[str, Any],
        parameters: dict[str, Any],
        progress: Callable[[float, str, str | None], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        python, entry = self._runtime()
        input_path = input_path.expanduser().resolve()
        output_path = output_path.expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(input_path)
        if output_path.exists() and not parameters.get("overwrite", False):
            raise FileExistsError(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        values = self._parameters(parameters)
        command = self._command(python, entry, "convert", model, values)
        command.extend(
            [
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            ]
        )
        if parameters.get("overwrite"):
            command.append("--overwrite")
        if progress:
            progress(0.35, "converting", f"loading {model['display_name']}")
        ModelRegistry.verify_snapshot(model)
        payload = self._run_worker(command, entry, cancelled)
        ModelRegistry.verify_snapshot(model)
        if not output_path.is_file():
            raise RvcEngineError("RVC reported success but output file is missing")
        if progress:
            progress(0.8, "validating", "hashing RVC output")
        return {
            "engine": "rvc",
            "model_id": model["id"],
            "model_sha256": model["model_sha256"],
            "index_sha256": model.get("index_sha256"),
            "parameters": {
                **values,
            },
            "output_path": str(output_path),
            "output_sha256": sha256_file(output_path),
            "upstream": payload,
        }

    def convert_batch(
        self,
        jobs: list[tuple[Path, Path]],
        model: dict[str, Any],
        parameters: dict[str, Any],
        progress: Callable[[float, str, str | None], None] | None = None,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        if not jobs:
            raise ValueError("RVC batch requires at least one job")
        ModelRegistry.verify_snapshot(model)
        python, entry = self._runtime()
        values = self._parameters(parameters)
        items = []
        for input_path, output_path in jobs:
            input_path = input_path.expanduser().resolve()
            output_path = output_path.expanduser().resolve()
            if not input_path.is_file():
                raise FileNotFoundError(input_path)
            if output_path.exists():
                raise FileExistsError(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            items.append({"input": str(input_path), "output": str(output_path)})
        manifest_path = jobs[0][1].parent / "rvc-batch-request.json"
        manifest_path.write_text(
            json.dumps(
                {"protocol": "voxweave-rvc-batch", "version": 1, "items": items},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        command = self._command(python, entry, "convert-batch", model, values)
        command.extend(["--manifest", str(manifest_path)])
        if progress:
            progress(0.35, "converting", f"loading {model['display_name']} for {len(jobs)} chunks")
        payload = self._run_worker(command, entry, cancelled)
        ModelRegistry.verify_snapshot(model)
        upstream_results = payload.get("results") or []
        if len(upstream_results) != len(jobs):
            raise RvcEngineError("RVC batch returned an unexpected result count")
        results = []
        for (input_path, output_path), upstream in zip(jobs, upstream_results, strict=True):
            if not output_path.is_file():
                raise RvcEngineError(f"RVC batch output is missing: {output_path}")
            results.append(
                {
                    "engine": "rvc",
                    "model_id": model["id"],
                    "model_sha256": model["model_sha256"],
                    "index_sha256": model.get("index_sha256"),
                    "parameters": values,
                    "input_path": str(input_path),
                    "output_path": str(output_path),
                    "output_sha256": sha256_file(output_path),
                    "upstream": upstream,
                }
            )
        if progress:
            progress(0.78, "converting", f"converted {len(jobs)} verified chunks")
        return results
