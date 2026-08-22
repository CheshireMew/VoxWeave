from __future__ import annotations

import os

import pytest

from voxweave.hashing import sha256_file


def test_hash_reloads_content_when_size_and_timestamps_are_preserved(tmp_path) -> None:
    path = tmp_path / "media.bin"
    path.write_bytes(b"first")
    original_stat = path.stat()
    first = sha256_file(path)

    path.write_bytes(b"other")
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    assert path.stat().st_size == original_stat.st_size
    assert path.stat().st_mtime_ns == original_stat.st_mtime_ns
    assert sha256_file(path) != first


def test_hash_cancellation_is_checked_before_reading(tmp_path) -> None:
    path = tmp_path / "media.bin"
    path.write_bytes(b"content")

    with pytest.raises(InterruptedError, match="cancelled"):
        sha256_file(path, cancelled=lambda: True)


@pytest.mark.skipif(os.name != "nt", reason="Windows stable file sharing contract")
def test_hash_refuses_a_file_held_open_for_writing(tmp_path) -> None:
    path = tmp_path / "media.bin"
    path.write_bytes(b"content")

    with path.open("r+b"):
        with pytest.raises(OSError, match="stable hashing"):
            sha256_file(path)
