from __future__ import annotations

import json
import re
import sys
import uuid
from typing import Any

from . import __version__
from .client import request_json
from .config import configure_process_environment
from .protocol import PROTOCOL, PROTOCOL_VERSION
from .settings_file_store import load_settings

MCP_PROTOCOL_VERSION = "2025-03-26"


def _tool_name(operation: str) -> str:
    return "voxweave_" + re.sub(r"[^A-Za-z0-9_-]", "_", operation)


class VoxWeaveMcpServer:
    def __init__(self) -> None:
        self.settings = load_settings()
        configure_process_environment(self.settings)
        self.operations: dict[str, str] = {}
        self.session_id = uuid.uuid4().hex

    def _describe(self) -> dict[str, Any]:
        return request_json(self.settings, "GET", "/v1/describe")

    def _tools(self) -> list[dict[str, Any]]:
        described = self._describe()
        tools = []
        self.operations.clear()
        for operation, spec in sorted(described["operations"].items()):
            name = _tool_name(operation)
            self.operations[name] = operation
            read_only = not bool(spec["mutating"]) and not bool(spec["long_running"])
            tools.append(
                {
                    "name": name,
                    "title": operation,
                    "description": (
                        f"Execute VoxWeave operation {operation}. "
                        + ("Returns a durable task record." if spec["long_running"] else "")
                    ).strip(),
                    "inputSchema": spec["arguments_schema"],
                    "annotations": {
                        "readOnlyHint": read_only,
                        "destructiveHint": operation in {"storage.archive"},
                        "idempotentHint": read_only,
                        "openWorldHint": operation.startswith("update."),
                    },
                }
            )
        return tools

    def _call_tool(self, params: dict[str, Any], request_id: Any) -> dict[str, Any]:
        name = str(params.get("name") or "")
        if not self.operations:
            self._tools()
        operation = self.operations.get(name)
        if not operation:
            raise LookupError(f"unknown VoxWeave MCP tool: {name}")
        correlation_id = request_id if request_id is not None else uuid.uuid4()
        request = {
            "protocol": PROTOCOL,
            "version": PROTOCOL_VERSION,
            "operation": operation,
            "arguments": dict(params.get("arguments") or {}),
            "request_id": f"mcp:{self.session_id}:{correlation_id}",
            "actor": {
                "kind": "mcp",
                "client": "external-agent",
                "session_id": self.session_id,
            },
        }
        payload = request_json(self.settings, "POST", "/v1/execute", request)
        value = payload.get("result") if payload.get("ok") else {
            "error_type": payload.get("error_type"),
            "error": payload.get("error"),
        }
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(value, ensure_ascii=False, indent=2),
                }
            ],
            "structuredContent": value if isinstance(value, dict) else {"result": value},
            "isError": not bool(payload.get("ok")),
        }

    def dispatch(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if request_id is None:
            return None
        if method == "initialize":
            result: Any = {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "voxweave", "version": __version__},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": self._tools()}
        elif method == "tools/call":
            result = self._call_tool(dict(message.get("params") or {}), request_id)
        elif method in {"resources/list", "prompts/list"}:
            result = {"resources": []} if method == "resources/list" else {"prompts": []}
        else:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"method not found: {method}"},
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def run(self) -> int:
        for line in sys.stdin:
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise TypeError("JSON-RPC message must be an object")
                response = self.dispatch(message)
            except Exception as error:  # noqa: BLE001 - MCP protocol boundary
                response = {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32603, "message": str(error)},
                }
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        return 0


def main() -> int:
    return VoxWeaveMcpServer().run()


if __name__ == "__main__":
    raise SystemExit(main())
