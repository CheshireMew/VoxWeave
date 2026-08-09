from __future__ import annotations

from voxweave.cli import build_parser


def test_task_cli_has_unambiguous_subcommands() -> None:
    parser = build_parser()
    listed = parser.parse_args(["task", "list"])
    fetched = parser.parse_args(["task", "get", "task-1"])
    assert listed.task_command == "list"
    assert fetched.task_command == "get"
    assert fetched.task_id == "task-1"
