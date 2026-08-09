from __future__ import annotations

import numpy as np

from voxweave.media_pipeline import _quiet_chunk_ranges


def test_quiet_chunk_ranges_cover_source_without_gaps() -> None:
    sample_rate = 100
    audio = np.ones(sample_rate * 150, dtype=np.float32) * 0.2
    for second in (44, 89, 134):
        audio[second * sample_rate : second * sample_rate + 20] = 0
    ranges = _quiet_chunk_ranges(audio, sample_rate)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == len(audio)
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:], strict=False))
    assert all(end > start for start, end in ranges)
