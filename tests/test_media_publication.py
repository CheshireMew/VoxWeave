from __future__ import annotations

import json

import pytest

from voxweave.media_checkpoint import _publish_prepared_output


def _checkpoint() -> dict[str, object]:
    return {
        "protocol": "voxweave-conversion-checkpoint",
        "version": 1,
        "stages": {},
    }


def test_cancelled_publication_preserves_existing_output_and_prepared_file(tmp_path) -> None:
    output = tmp_path / "voice.wav"
    output.write_bytes(b"previous-valid-output")
    prepared = tmp_path / ".voice.task.publishing.wav"
    prepared.write_bytes(b"new-validated-output")
    checkpoint = _checkpoint()
    checkpoint_path = tmp_path / "checkpoint.json"

    with pytest.raises(InterruptedError, match="cancellation requested"):
        _publish_prepared_output(
            prepared,
            output,
            overwrite=True,
            cancelled=lambda: True,
            checkpoint=checkpoint,
            checkpoint_path=checkpoint_path,
            result={"output": {"path": str(output)}},
        )

    assert output.read_bytes() == b"previous-valid-output"
    assert prepared.read_bytes() == b"new-validated-output"
    persisted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert persisted["stages"]["publication"]["state"] == "prepared"


def test_publication_atomically_replaces_output_and_records_published_hash(tmp_path) -> None:
    output = tmp_path / "voice.wav"
    output.write_bytes(b"previous-valid-output")
    prepared = tmp_path / ".voice.task.publishing.wav"
    prepared.write_bytes(b"new-validated-output")
    checkpoint = _checkpoint()
    checkpoint_path = tmp_path / "checkpoint.json"

    _publish_prepared_output(
        prepared,
        output,
        overwrite=True,
        cancelled=lambda: False,
        checkpoint=checkpoint,
        checkpoint_path=checkpoint_path,
        result={"output": {"path": str(output)}},
    )

    assert output.read_bytes() == b"new-validated-output"
    assert not prepared.exists()
    persisted = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    publication = persisted["stages"]["publication"]
    assert publication["state"] == "published"
    assert publication["prepared_output"]["path"] == str(output.resolve())
    assert len(publication["prepared_output"]["sha256"]) == 64
