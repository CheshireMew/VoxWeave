from __future__ import annotations

import pytest

from voxweave.protocol import OPERATIONS, describe, validate_arguments


def test_describe_is_complete_and_stable() -> None:
    payload = describe()
    assert payload["protocol"] == "voxweave-control"
    assert payload["version"] == 1
    assert set(payload["operations"]) == set(OPERATIONS)
    assert len(payload["operations"]) == 20


def test_conversion_requires_absolute_paths() -> None:
    with pytest.raises(ValueError, match="absolute"):
        validate_arguments(
            "conversion.run", {"input": "source.wav", "output": "out.wav", "model": "voice"}
        )


def test_conversion_parameter_bounds() -> None:
    with pytest.raises(ValueError, match="index_rate"):
        validate_arguments(
            "conversion.run",
            {
                "input": "C:/source.wav",
                "output": "C:/out.wav",
                "model": "voice",
                "index_rate": 2,
            },
        )


def test_unknown_arguments_are_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported arguments"):
        validate_arguments("task.list", {"surprise": True})
