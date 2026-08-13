from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


def localized_model_name(
    model: dict[str, Any], language: str, translations: dict[str, dict[str, str]]
) -> str:
    table = translations.get(language, translations["en"])
    family = str(model.get("family") or "")
    model_id = str(model.get("id") or "")
    name = (
        table.get(f"model.name.{model_id}")
        or table.get(f"model.name.{family}")
        or str(model.get("display_name") or family)
    )
    epoch = model.get("checkpoint_epoch")
    if epoch is None:
        return name
    template = table.get("model.name_with_epoch", "{name} (Epoch {epoch})")
    return template.format(name=name, epoch=epoch)


def localized_task_title(
    task: dict[str, Any], language: str, translations: dict[str, dict[str, str]]
) -> str:
    table = translations.get(language, translations["en"])
    operation = str(task.get("operation") or "")
    label = table.get(f"task.operation.{operation}") or table.get(
        "task.operation.default", "Task"
    )
    arguments = task.get("arguments") or {}
    subject = ""
    if operation in {"model.import", "model.catalog.install"}:
        subject = str(arguments.get("display_name") or arguments.get("model_id") or "")
    if not subject and arguments.get("input"):
        value = str(arguments["input"])
        parsed = urlsplit(value)
        subject = Path(parsed.path if parsed.scheme else value).name
    if not subject and operation == "model.import" and arguments.get("model"):
        value = str(arguments["model"])
        parsed = urlsplit(value)
        subject = Path(parsed.path if parsed.scheme else value).name
    return f"{label} · {subject}" if subject else label


def task_error_summary(task: dict[str, Any]) -> str:
    error = str(task.get("error") or "").strip()
    return error.splitlines()[0] if error else ""


def task_result_path(task: dict[str, Any]) -> str:
    result = task.get("result")
    if not isinstance(result, dict):
        return ""
    output = result.get("output")
    if isinstance(output, dict) and output.get("path"):
        return str(output["path"])
    outputs = result.get("outputs")
    if isinstance(outputs, list) and outputs and isinstance(outputs[0], dict):
        return str(outputs[0].get("output_path") or "")
    return ""
