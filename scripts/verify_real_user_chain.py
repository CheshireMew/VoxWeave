from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path

from voxweave.client import request_json
from voxweave.config import configure_process_environment, load_settings


def execute(operation: str, arguments: dict) -> dict:
    settings = load_settings()
    payload = request_json(
        settings,
        "POST",
        "/v1/execute",
        {
            "protocol": "voxweave-control",
            "version": 1,
            "request_id": str(uuid.uuid4()),
            "operation": operation,
            "arguments": arguments,
        },
    )
    if not payload.get("ok"):
        raise RuntimeError(payload)
    return payload["result"]


def wait_task(task_id: str) -> dict:
    while True:
        task = execute("task.get", {"task_id": task_id})
        if task["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            if task["state"] != "completed":
                raise RuntimeError(task["error"])
            return task
        time.sleep(1)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the real VoxWeave service-to-output chain")
    parser.add_argument("--input", required=True)
    parser.add_argument("--model", action="append", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--video")
    arguments = parser.parse_args()
    settings = load_settings()
    configure_process_environment(settings)
    output_root = Path(arguments.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    results = []
    for model in arguments.model:
        resolved = execute("model.resolve", {"voice": model})
        output = output_root / f"{resolved['family']}.wav"
        submitted = execute(
            "conversion.run",
            {
                "input": str(Path(arguments.input).resolve()),
                "output": str(output),
                "model": resolved["id"],
                "pitch": resolved["recommended"]["pitch"],
                "f0": "rmvpe",
                "index_rate": 0.72,
                "rms_mix_rate": 0.25,
                "protect": 0.33,
                "content_mode": "clean",
                "overwrite": False,
            },
        )
        task = wait_task(submitted["task_id"])
        results.append(task["result"])
    if arguments.video:
        output = output_root / "video-result.mp4"
        submitted = execute(
            "conversion.run",
            {
                "input": str(Path(arguments.video).resolve()),
                "output": str(output),
                "model": arguments.model[0],
                "pitch": 9,
                "content_mode": "mixed",
                "overwrite": False,
            },
        )
        results.append(wait_task(submitted["task_id"])["result"])
    print(json.dumps({"ok": True, "results": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
