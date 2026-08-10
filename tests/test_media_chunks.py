from __future__ import annotations

import numpy as np
import soundfile as sf

from voxweave.media_processing import _quiet_chunk_ranges_file


def test_production_quiet_chunk_ranges_cover_source_without_gaps(tmp_path) -> None:
    sample_rate = 100
    audio = np.ones(sample_rate * 150, dtype=np.float32) * 0.2
    for second in (44, 89, 134):
        audio[second * sample_rate : second * sample_rate + 20] = 0
    source = tmp_path / "source.wav"
    sf.write(source, audio, sample_rate, subtype="PCM_24")

    actual_rate, total_frames, ranges = _quiet_chunk_ranges_file(source, lambda: False)

    assert actual_rate == sample_rate
    assert total_frames == len(audio)
    assert ranges[0][0] == 0
    assert ranges[-1][1] == total_frames
    assert all(left[1] == right[0] for left, right in zip(ranges, ranges[1:], strict=False))
    assert all(end > start for start, end in ranges)
