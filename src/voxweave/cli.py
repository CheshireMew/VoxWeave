from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from .client import request_json, shutdown_service
from .config import configure_process_environment, load_settings
from .i18n import translate


def _read_request(value: str) -> dict[str, Any]:
    text = sys.stdin.read() if value == "-" else value
    return json.loads(text)


def _human(payload: dict[str, Any], language: str) -> str:
    if payload.get("ok") is False:
        return translate(
            language,
            "cli.error",
            code=payload.get("error_type"),
            message=payload.get("error"),
        )
    result = payload.get("result", payload)
    if isinstance(result, dict) and result.get("id") and result.get("state"):
        return translate(
            language,
            "cli.task",
            task_id=result["id"],
            state=result["state"],
            progress=f"{result.get('progress', 0):.0%}",
        )
    return json.dumps(result, ensure_ascii=False, indent=2)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="voxweave",
        description=(
            "VoxWeave 高质量离线与实时 RVC 变声工作台 / "
            "offline and realtime RVC workstation"
        ),
        epilog=(
            "AGPL-3.0-only; ABSOLUTELY NO WARRANTY. Source: https://github.com/CheshireMew/VoxWeave"
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="仅输出稳定 JSON / emit stable JSON only"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("describe", help="查看实际服务合同 / describe live contract")
    execute = subparsers.add_parser("execute", help="执行协议操作 / execute an operation")
    execute.add_argument("operation", nargs="?")
    execute.add_argument("--arguments", default="{}", help="JSON object")
    execute.add_argument("--request", help="full JSON request or - for stdin")
    subparsers.add_parser("models", help="列出 RVC 模型 / list registered models")
    scan = subparsers.add_parser("scan-models", help="扫描模型目录 / scan model roots")
    scan.add_argument("--weights", action="append", default=[])
    scan.add_argument("--indices", action="append", default=[])
    task = subparsers.add_parser("task", help="管理任务 / manage tasks")
    task_commands = task.add_subparsers(dest="task_command", required=True)
    task_commands.add_parser("list", help="列出任务 / list tasks")
    for command, help_text in (
        ("get", "查看任务 / show a task"),
        ("cancel", "取消任务 / cancel a task"),
        ("retry", "重试任务 / retry a task"),
    ):
        child = task_commands.add_parser(command, help=help_text)
        child.add_argument("task_id")
    service = subparsers.add_parser("service", help="管理后台服务 / manage the service")
    service_commands = service.add_subparsers(dest="service_command", required=True)
    service_commands.add_parser("stop", help="安全停止后台服务 / stop the service safely")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings()
    configure_process_environment(settings)
    if args.command == "describe":
        payload = request_json(settings, "GET", "/v1/describe")
    elif args.command == "service":
        if args.service_command != "stop":
            raise AssertionError(f"unhandled service command: {args.service_command}")
        payload = shutdown_service(settings)
    else:
        if args.command == "execute":
            if args.request:
                request = _read_request(args.request)
            else:
                if not args.operation:
                    raise SystemExit("execute requires an operation or --request")
                request = {
                    "protocol": "voxweave-control",
                    "version": 1,
                    "operation": args.operation,
                    "arguments": json.loads(args.arguments),
                }
        elif args.command == "models":
            request = {
                "protocol": "voxweave-control",
                "version": 1,
                "operation": "model.list",
                "arguments": {},
            }
        elif args.command == "scan-models":
            arguments = {}
            if args.weights:
                arguments["weight_roots"] = args.weights
            if args.indices:
                arguments["index_roots"] = args.indices
            request = {
                "protocol": "voxweave-control",
                "version": 1,
                "operation": "model.scan",
                "arguments": arguments,
            }
        elif args.command == "task":
            operation = f"task.{args.task_command}"
            arguments = {} if args.task_command == "list" else {"task_id": args.task_id}
            request = {
                "protocol": "voxweave-control",
                "version": 1,
                "operation": operation,
                "arguments": arguments,
            }
        else:
            raise AssertionError(f"unhandled command: {args.command}")
        request.setdefault("request_id", str(uuid.uuid4()))
        payload = request_json(settings, "POST", "/v1/execute", request)
    print(
        json.dumps(payload, ensure_ascii=False, indent=2)
        if args.json
        else _human(payload, settings.language)
    )
    return 0 if payload.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
