from __future__ import annotations

import itertools
import time
from pathlib import Path
from typing import Any

from voxweave.config import Settings
from voxweave.controller import Controller
from voxweave.protocol import OPERATION_SPECS


def _client(controller: Controller):
    sequence = itertools.count(1)

    def execute(operation: str, arguments: dict[str, Any]) -> Any:
        spec = OPERATION_SPECS[operation]
        request_id = (
            f"workstation-audit:{next(sequence)}"
            if spec.mutating or spec.long_running
            else None
        )
        return controller.execute(
            operation,
            arguments,
            request_id=request_id,
            actor={"kind": "test", "name": "workstation-operation-chain"},
        )

    return execute


def _wait_task(controller: Controller, task_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        task = controller.tasks.get(task_id)
        if task["state"] in {"completed", "failed", "cancelled", "interrupted"}:
            return task
        time.sleep(0.02)
    raise AssertionError(f"task did not reach a terminal state: {task_id}")


def test_new_workstation_operations_are_wired_through_controller(tmp_path: Path) -> None:
    settings = Settings(data_root=str(tmp_path / "data"))
    controller = Controller(settings)
    execute = _client(controller)
    try:
        model_path = tmp_path / "voice.pth"
        model_path.write_bytes(b"voice model")
        controller.models.register(
            model_path,
            model_id="voice.audit",
            display_name="Audit Voice",
            inspection={"status": "ready"},
        )
        source = tmp_path / "source.wav"
        source.write_bytes(b"source")

        project = execute(
            "project.create",
            {
                "name": "Operation chain",
                "input": str(source.resolve()),
                "output": str((tmp_path / "result.wav").resolve()),
                "document": {"default_model": "voice.audit"},
            },
        )
        project = execute(
            "project.update",
            {
                "project_id": project["id"],
                "expected_revision": project["revision"],
                "name": "Operation chain edited",
            },
        )
        assert execute("project.get", {"project_id": project["id"]})["revision"] == 2
        assert execute("project.history", {"project_id": project["id"]})[0][
            "revision"
        ] == 2
        archived_project = execute(
            "project.archive",
            {
                "project_id": project["id"],
                "expected_revision": project["revision"],
                "archived": True,
            },
        )
        restored_project = execute(
            "project.restore",
            {
                "project_id": project["id"],
                "expected_revision": archived_project["revision"],
                "revision": 2,
            },
        )
        assert restored_project["state"] == "active"

        models = execute("model.list", {})
        model = next(item for item in models if item["id"] == "voice.audit")
        model = execute(
            "model.metadata.update",
            {
                "model_id": model["id"],
                "expected_revision": model["metadata_revision"],
                "custom_name": "Audit Favorite",
                "tags": ["audit", "voice"],
                "favorite": True,
            },
        )
        assert model["favorite"] is True

        preset = execute(
            "preset.save",
            {
                "model": "voice.audit",
                "name": "Audit preset",
                "parameters": {"pitch": 2, "f0": "rmvpe"},
            },
        )
        bundle = execute("preset.export", {"preset_ids": [preset["id"]]})
        imported = execute("preset.import", bundle)[0]
        assert imported["needs_reconfirmation"] is False
        archived_preset = execute(
            "preset.archive",
            {
                "preset_id": imported["id"],
                "expected_revision": imported["revision"],
                "archived": True,
            },
        )
        assert archived_preset["archived"] is True

        scene = execute(
            "realtime.scene.create",
            {
                "name": "Audit scene",
                "settings": {
                    "model": "voice.audit",
                    "hostapi": "Windows WASAPI",
                    "input_device": "Microphone",
                    "output_device": "Speakers",
                },
            },
        )
        scene = execute(
            "realtime.scene.update",
            {
                "scene_id": scene["id"],
                "expected_revision": scene["revision"],
                "name": "Audit scene edited",
            },
        )
        scene = execute(
            "realtime.scene.archive",
            {
                "scene_id": scene["id"],
                "expected_revision": scene["revision"],
                "archived": True,
            },
        )
        assert scene["archived"] is True

        input_root = tmp_path / "batch-input"
        input_root.mkdir()
        batch = execute(
            "batch.create",
            {
                "input_root": str(input_root.resolve()),
                "output_root": str((tmp_path / "batch-output").resolve()),
                "model": "voice.audit",
                "preset": {},
                "watch": False,
            },
        )
        plan_task = execute("batch.plan", {"batch_id": batch["id"]})
        plan = _wait_task(controller, plan_task["task_id"])
        assert plan["state"] == "completed"
        assert plan["result"]["file_count"] == 0

        storage_task = execute("storage.inspect", {"older_than_days": 30})
        storage = _wait_task(controller, storage_task["task_id"])
        assert storage["state"] == "completed"
        assert storage["result"]["data_root"] == str(settings.root)
    finally:
        controller.shutdown()
