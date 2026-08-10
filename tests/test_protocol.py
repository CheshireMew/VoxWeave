from __future__ import annotations

import pytest

from voxweave.protocol import OPERATION_SPECS, describe, parse_arguments


def test_describe_is_complete_and_stable() -> None:
    payload = describe()
    assert payload["protocol"] == "voxweave-control"
    assert payload["version"] == 1
    assert set(payload["operations"]) == set(OPERATION_SPECS)
    assert len(payload["operations"]) == 31
    assert payload["operations"]["conversion.run"]["arguments_schema"]["type"] == "object"


def test_realtime_requires_valid_devices_and_latency() -> None:
    parsed = parse_arguments(
        "realtime.start",
        {
            "model": "voice",
            "input_device": 1,
            "output_device": 2,
            "test_mode": True,
        },
    )
    assert parsed["test_mode"] is True

    with pytest.raises(ValueError, match="input_device"):
        parse_arguments(
            "realtime.start",
            {"model": "voice", "input_device": -1, "output_device": 2},
        )
    with pytest.raises(ValueError, match="block_seconds"):
        parse_arguments(
            "realtime.start",
            {
                "model": "voice",
                "input_device": 1,
                "output_device": 2,
                "block_seconds": 0.3,
            },
        )
    with pytest.raises(ValueError, match="vad_threshold"):
        parse_arguments(
            "realtime.start",
            {
                "model": "voice",
                "input_device": 1,
                "output_device": 2,
                "vad_threshold": 0.95,
            },
        )
    with pytest.raises(ValueError, match="threshold_db"):
        parse_arguments(
            "realtime.start",
            {
                "model": "voice",
                "input_device": 1,
                "output_device": 2,
                "threshold_db": -45,
            },
        )


def test_conversion_requires_absolute_paths() -> None:
    with pytest.raises(ValueError, match="absolute"):
        parse_arguments(
            "conversion.run", {"input": "source.wav", "output": "out.wav", "model": "voice"}
        )


def test_conversion_parameter_bounds() -> None:
    with pytest.raises(ValueError, match="index_rate"):
        parse_arguments(
            "conversion.run",
            {
                "input": "C:/source.wav",
                "output": "C:/out.wav",
                "model": "voice",
                "index_rate": 2,
            },
        )


def test_unknown_arguments_are_rejected() -> None:
    with pytest.raises(ValueError, match="surprise"):
        parse_arguments("task.list", {"surprise": True})


def test_storage_archive_requires_explicit_source_removal_confirmation(tmp_path) -> None:
    with pytest.raises(ValueError, match="confirm_source_removal"):
        parse_arguments(
            "storage.archive",
            {
                "destination_root": str(tmp_path / "archive"),
                "confirm_source_removal": False,
            },
        )
