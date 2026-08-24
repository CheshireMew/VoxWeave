from __future__ import annotations

import shutil

import numpy as np
import soundfile as sf

from voxweave.media_processing import convert_long_audio, convert_selected_segments


class CopyEngine:
    def __init__(self) -> None:
        self.batch_calls = 0
        self.single_calls = 0

    def convert_batch(self, jobs, _model, _parameters, progress, cancelled):
        self.batch_calls += 1
        results = []
        for index, (source, output) in enumerate(jobs):
            assert cancelled() is False
            shutil.copyfile(source, output)
            progress((index + 1) / len(jobs), "converting", None)
            results.append({"output": str(output)})
        return results

    def convert(self, source, output, _model, _parameters, cancelled):
        self.single_calls += 1
        assert cancelled() is False
        shutil.copyfile(source, output)
        return {"output": str(output)}


def _write_audio(path, seconds: int = 100, sample_rate: int = 1000) -> None:
    with sf.SoundFile(
        path, mode="w", samplerate=sample_rate, channels=1, subtype="PCM_24"
    ) as writer:
        for index in range(seconds):
            value = 0.02 if index % 10 else 0.0
            writer.write(np.full(sample_rate, value, dtype=np.float32))


def test_long_audio_conversion_never_reads_the_entire_input_array(tmp_path) -> None:
    source = tmp_path / "long.wav"
    output = tmp_path / "converted.wav"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_audio(source)
    artifacts = convert_long_audio(
        CopyEngine(),  # type: ignore[arg-type]
        source,
        output,
        {},
        {},
        work_dir,
        lambda _value, _stage, _detail: None,
        lambda: False,
    )
    assert len(artifacts) >= 2
    assert sf.info(output).frames == sf.info(source).frames


def test_selected_long_segment_is_split_into_bounded_streaming_chunks(
    tmp_path,
) -> None:
    source = tmp_path / "long.wav"
    output = tmp_path / "selected.wav"
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    _write_audio(source)
    engine = CopyEngine()
    artifacts = convert_selected_segments(
        engine,  # type: ignore[arg-type]
        source,
        output,
        {},
        {},
        [{"speaker": "speaker-1", "start_seconds": 0, "end_seconds": 100}],
        {"speaker-1"},
        work_dir,
        lambda _value, _stage, _detail: None,
        lambda: False,
        "convert",
    )
    assert len(artifacts[0]["conversions"]) == 3
    assert engine.batch_calls == 1
    assert engine.single_calls == 0
    assert sf.info(output).frames == sf.info(source).frames
