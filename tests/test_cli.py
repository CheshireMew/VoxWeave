from __future__ import annotations

from typing import Any

from voxweave import cli
from voxweave.cli import build_parser
from voxweave.client import shutdown_service
from voxweave.config import Settings


def test_task_cli_has_unambiguous_subcommands() -> None:
    parser = build_parser()
    listed = parser.parse_args(["task", "list"])
    fetched = parser.parse_args(["task", "get", "task-1"])
    assert listed.task_command == "list"
    assert fetched.task_command == "get"
    assert fetched.task_id == "task-1"


def test_service_cli_has_an_explicit_safe_stop() -> None:
    args = build_parser().parse_args(["service", "stop"])
    assert args.command == "service"
    assert args.service_command == "stop"


def test_service_stop_does_not_start_a_missing_service(tmp_path) -> None:
    result = shutdown_service(Settings(data_root=str(tmp_path)))
    assert result == {"ok": True, "state": "stopped"}
    assert not (tmp_path / "state" / "service.json").exists()


def test_scan_models_adds_request_id_at_the_cli_send_boundary(
    tmp_path, monkeypatch
) -> None:
    captured: dict[str, Any] = {}

    def request(_settings, method: str, route: str, payload: dict[str, Any]) -> dict:
        captured.update(method=method, route=route, payload=payload)
        return {"ok": True, "result": {}}

    monkeypatch.setattr(cli, "load_settings", lambda: Settings(data_root=str(tmp_path)))
    monkeypatch.setattr(cli, "request_json", request)
    monkeypatch.setattr(
        "sys.argv",
        ["voxweave", "--json", "scan-models", "--weights", str(tmp_path)],
    )

    assert cli.main() == 0
    assert captured["method"] == "POST"
    assert captured["route"] == "/v1/execute"
    assert captured["payload"]["operation"] == "model.scan"
    assert captured["payload"]["request_id"]
