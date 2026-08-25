from __future__ import annotations

import queue
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Any


class RealtimeInferenceStopTimeout(RuntimeError):
    """The inference thread did not relinquish the resident worker in time."""


class RealtimeAudioRecorder:
    """Writes dry and wet streams away from the realtime inference thread."""

    def __init__(
        self,
        np: Any,
        directory: str,
        session_id: str,
        sample_rate: int,
        output_channels: int,
    ) -> None:
        self.np = np
        root = Path(directory).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        stem = f"{stamp}-{session_id}"
        self.dry_path = root / f"{stem}-dry.wav"
        self.wet_path = root / f"{stem}-wet.wav"
        self.sample_rate = sample_rate
        self.output_channels = output_channels
        self.items: queue.Queue[tuple[Any, Any] | None] = queue.Queue(maxsize=32)
        self.dropped_blocks = 0
        self.error: str | None = None
        self.thread = threading.Thread(
            target=self._write,
            name="voxweave-realtime-recorder",
            daemon=True,
        )
        self.thread.start()

    def enqueue(self, dry: Any, wet: Any) -> None:
        try:
            self.items.put_nowait((dry.copy(), wet.copy()))
        except queue.Full:
            self.dropped_blocks += 1

    def _write(self) -> None:
        import soundfile as sf  # noqa: PLC0415

        try:
            with sf.SoundFile(
                self.dry_path,
                mode="w",
                samplerate=self.sample_rate,
                channels=1,
                subtype="PCM_24",
            ) as dry_file, sf.SoundFile(
                self.wet_path,
                mode="w",
                samplerate=self.sample_rate,
                channels=self.output_channels,
                subtype="PCM_24",
            ) as wet_file:
                while True:
                    item = self.items.get()
                    if item is None:
                        break
                    dry, wet = item
                    dry_file.write(dry)
                    wet_file.write(wet)
        except Exception:  # noqa: BLE001 - isolated recorder boundary
            self.error = traceback.format_exc()

    def close(self) -> dict[str, Any]:
        while True:
            try:
                self.items.put_nowait(None)
                break
            except queue.Full:
                try:
                    self.items.get_nowait()
                except queue.Empty:
                    continue
                self.dropped_blocks += 1
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.error = "realtime recorder did not stop within 5 seconds"
        return {
            "recording": False,
            "recording_dry_path": str(self.dry_path),
            "recording_wet_path": str(self.wet_path),
            "recording_dropped_blocks": self.dropped_blocks,
            "recording_error": self.error,
        }

VAD_SAMPLE_RATE = 16000
VAD_WINDOW_SAMPLES = 512
TEST_MODE_END_SILENCE_SECONDS = 0.8
TEST_MODE_MAX_UTTERANCE_SECONDS = 30.0


@dataclass(frozen=True)
class StreamSpec:
    sample_rate: int
    input_channels: int
    output_channels: int
    block_frame: int
    crossfade_frame: int
    extra_frame: int
    sola_buffer_frame: int
    sola_search_frame: int
    zero_crossing: int


@dataclass(frozen=True)
class VoiceActivityDecision:
    process_block: bool
    active: bool
    probability: float


def should_process_audio(
    decision: VoiceActivityDecision, input_db: float, input_gate_db: float
) -> bool:
    return decision.process_block or input_db >= input_gate_db


class VoiceActivityState:
    def __init__(
        self,
        threshold: float,
        *,
        onset_windows: int = 2,
        min_silence_ms: int = 250,
    ) -> None:
        if not 0.1 <= threshold <= 0.9:
            raise ValueError("vad_threshold must be between 0.1 and 0.9")
        self.threshold = threshold
        self.onset_windows = onset_windows
        self.min_silence_samples = VAD_SAMPLE_RATE * min_silence_ms // 1000
        self.active = False
        self.speech_windows = 0
        self.silence_samples = 0

    def reset(self) -> None:
        self.active = False
        self.speech_windows = 0
        self.silence_samples = 0

    def update(self, probability: float) -> bool:
        was_active = self.active
        if probability >= self.threshold:
            self.speech_windows += 1
            self.silence_samples = 0
            if not self.active and self.speech_windows >= self.onset_windows:
                self.active = True
        elif self.active and probability < max(0.05, self.threshold - 0.15):
            self.speech_windows = 0
            self.silence_samples += VAD_WINDOW_SAMPLES
            if self.silence_samples >= self.min_silence_samples:
                self.active = False
                self.silence_samples = 0
        elif not self.active:
            self.speech_windows = 0
        return was_active or self.active


class UtteranceTestMode:
    """Buffer converted speech, then play it only after the utterance ends."""

    def __init__(self) -> None:
        self.outputs: deque[Any] = deque()
        self.configure(False)

    def configure(self, enabled: bool) -> None:
        self.enabled = enabled
        self.phase = "capture" if enabled else "off"
        self.utterance_active = False
        self.silence_callbacks = 0
        self.tail_callbacks_remaining = 0
        self.outputs.clear()

    def begin_utterance(self) -> None:
        if self.phase != "capture":
            raise RuntimeError("test mode is not ready to capture")
        self.utterance_active = True
        self.silence_callbacks = 0

    def mark_speech(self) -> None:
        if not self.utterance_active:
            raise RuntimeError("test mode has no active utterance")
        self.silence_callbacks = 0

    def mark_silence(self, required_callbacks: int) -> bool:
        if not self.utterance_active:
            raise RuntimeError("test mode has no active utterance")
        self.silence_callbacks += 1
        return self.silence_callbacks >= required_callbacks

    def buffer_output(self, output: Any) -> None:
        if not self.utterance_active:
            raise RuntimeError("test mode has no active utterance")
        self.outputs.append(output)

    def start_playback(self) -> Any:
        if not self.outputs:
            raise RuntimeError("test mode has no buffered output")
        self.phase = "playback"
        self.utterance_active = False
        self.silence_callbacks = 0
        return self.outputs.popleft()

    def playback_output(self) -> Any | None:
        if self.phase != "playback":
            raise RuntimeError("test mode is not playing")
        if self.outputs:
            return self.outputs.popleft()
        self.phase = "tail"
        self.tail_callbacks_remaining = 1
        return None

    def finish_tail_callback(self) -> bool:
        if self.phase != "tail":
            raise RuntimeError("test mode is not draining speaker tail")
        if self.tail_callbacks_remaining > 0:
            self.tail_callbacks_remaining -= 1
            if self.tail_callbacks_remaining > 0:
                return False
        self.phase = "capture"
        return True


class StreamingVoiceActivityDetector:
    def __init__(
        self,
        *,
        torch: Any,
        transforms: Any,
        model: Any,
        input_sample_rate: int,
        threshold: float,
    ) -> None:
        self.torch = torch
        self.model = model
        self.state = VoiceActivityState(threshold)
        self.resampler = (
            transforms.Resample(
                orig_freq=input_sample_rate,
                new_freq=VAD_SAMPLE_RATE,
                dtype=torch.float32,
            )
            if input_sample_rate != VAD_SAMPLE_RATE
            else None
        )
        self.buffer = torch.zeros(0, dtype=torch.float32)

    def reset(self) -> None:
        self.model.reset_states()
        self.state.reset()
        self.buffer = self.torch.zeros(0, dtype=self.torch.float32)

    def process(self, mono: Any) -> VoiceActivityDecision:
        audio = self.torch.from_numpy(mono)
        if self.resampler is not None:
            audio = self.resampler(audio)
        self.buffer = self.torch.cat((self.buffer, audio.detach().cpu()))
        process_block = self.state.active
        probability = 0.0
        while self.buffer.numel() >= VAD_WINDOW_SAMPLES:
            window = self.buffer[:VAD_WINDOW_SAMPLES]
            self.buffer = self.buffer[VAD_WINDOW_SAMPLES:]
            current = float(self.model(window, VAD_SAMPLE_RATE).item())
            probability = max(probability, current)
            process_block = self.state.update(current) or process_block
        return VoiceActivityDecision(
            process_block=process_block,
            active=self.state.active,
            probability=probability,
        )


def select_stream_spec(
    sd: Any,
    input_device: int,
    output_device: int,
    input_info: Any,
    output_info: Any,
    target_sample_rate: int,
    block_seconds: float,
    crossfade_seconds: float,
    extra_seconds: float,
) -> StreamSpec:
    max_input_channels = int(input_info["max_input_channels"])
    output_channels = min(2, int(output_info["max_output_channels"]))
    input_channel_candidates = [1]
    if max_input_channels >= 2:
        input_channel_candidates.append(2)
    candidates = [
        int(input_info["default_samplerate"]),
        int(output_info["default_samplerate"]),
        int(target_sample_rate),
    ]
    sample_rate = None
    input_channels = None
    for candidate in dict.fromkeys(candidates):
        for channel_count in input_channel_candidates:
            try:
                sd.check_input_settings(
                    device=input_device,
                    channels=channel_count,
                    dtype="float32",
                    samplerate=candidate,
                )
            except sd.PortAudioError:
                continue
            input_channels = channel_count
            break
        if input_channels is None:
            continue
        try:
            sd.check_output_settings(
                device=output_device,
                channels=output_channels,
                dtype="float32",
                samplerate=candidate,
            )
        except sd.PortAudioError:
            input_channels = None
            continue
        sample_rate = candidate
        break
    if sample_rate is None or input_channels is None:
        raise RuntimeError("input and output devices have no compatible sample rate")

    zero_crossing = sample_rate // 100
    block_frame = round(block_seconds * sample_rate / zero_crossing) * zero_crossing
    crossfade_frame = round(crossfade_seconds * sample_rate / zero_crossing) * zero_crossing
    extra_frame = round(extra_seconds * sample_rate / zero_crossing) * zero_crossing
    if block_frame <= 0 or crossfade_frame <= 0 or extra_frame < 0:
        raise ValueError("realtime frame durations must produce positive audio buffers")
    return StreamSpec(
        sample_rate=sample_rate,
        input_channels=input_channels,
        output_channels=output_channels,
        block_frame=block_frame,
        crossfade_frame=crossfade_frame,
        extra_frame=extra_frame,
        sola_buffer_frame=min(crossfade_frame, 4 * zero_crossing),
        sola_search_frame=zero_crossing,
        zero_crossing=zero_crossing,
    )


def select_mono_channel(np: Any, indata: Any) -> Any:
    if indata.shape[1] == 1:
        return indata[:, 0]
    channel_rms = np.sqrt(np.mean(np.square(indata), axis=0, dtype=np.float64))
    return indata[:, int(np.argmax(channel_rms))]


class RealtimeAudioProcessor:
    def __init__(
        self,
        *,
        np: Any,
        torch: Any,
        functional: Any,
        transforms: Any,
        sd: Any,
        converter: Any,
        vad_model: Any,
        device: Any,
        spec: StreamSpec,
        vad_threshold: float,
        input_gate_db: float,
        rms_mix_rate: float,
        f0_method: str,
    ) -> None:
        self.np = np
        self.torch = torch
        self.functional = functional
        self.sd = sd
        self.converter = converter
        self.device = device
        self.spec = spec
        self.rms_mix_rate = rms_mix_rate
        self.f0_method = f0_method
        if not -60.0 <= input_gate_db <= -20.0:
            raise ValueError("input_gate_db must be between -60 and -20")
        self.input_gate_db = input_gate_db
        self.vad = StreamingVoiceActivityDetector(
            torch=torch,
            transforms=transforms,
            model=vad_model,
            input_sample_rate=spec.sample_rate,
            threshold=vad_threshold,
        )
        self.output_active = False
        self.control_lock = threading.RLock()
        self.bypass = False
        self.muted = False
        self.push_to_talk_enabled = False
        self.push_to_talk_pressed = False
        self.recorder: RealtimeAudioRecorder | None = None
        self.recording_result: dict[str, Any] = {}
        self.test_session = UtteranceTestMode()
        self.warming = True
        self.callback_error: list[str] = []
        self.metrics_lock = threading.Lock()
        self.metrics: dict[str, Any] = {
            "callbacks": 0,
            "inference_callbacks": 0,
            "skipped_callbacks": 0,
            "suppressed_callbacks": 0,
            "xruns": 0,
            "infer_ms": 0,
            "peak_in": 0.0,
            "peak_out": 0.0,
            "input_db": -120.0,
            "vad_probability": 0.0,
            "speech_detected": False,
            "rvc_inference_active": False,
            "test_mode": False,
            "test_phase": "off",
            "buffered_blocks": 0,
            "completed_utterances": 0,
            "playback_active": False,
            "microphone_suppressed": False,
            "input_overruns": 0,
            "output_underruns": 0,
            "pipeline_depth": 0,
            "bypass": False,
            "muted": False,
            "push_to_talk_enabled": False,
            "push_to_talk_pressed": False,
            "recording": False,
        }

        buffer_frames = (
            spec.extra_frame + spec.crossfade_frame + spec.sola_search_frame + spec.block_frame
        )
        self.input_wav = torch.zeros(buffer_frames, device=device, dtype=torch.float32)
        self.input_wav_resampled = torch.zeros(
            160 * buffer_frames // spec.zero_crossing,
            device=device,
            dtype=torch.float32,
        )
        self.sola_buffer = torch.zeros(spec.sola_buffer_frame, device=device, dtype=torch.float32)
        self.sola_denominator = torch.ones(
            1, 1, spec.sola_buffer_frame, device=device, dtype=torch.float32
        )
        self.fade_in = (
            torch.sin(
                0.5
                * np.pi
                * torch.linspace(
                    0.0,
                    1.0,
                    steps=spec.sola_buffer_frame,
                    device=device,
                    dtype=torch.float32,
                )
            )
            ** 2
        )
        self.fade_out = 1 - self.fade_in
        self.input_resampler = transforms.Resample(
            orig_freq=spec.sample_rate, new_freq=16000, dtype=torch.float32
        ).to(device)
        self.output_resampler = (
            transforms.Resample(
                orig_freq=converter.tgt_sr,
                new_freq=spec.sample_rate,
                dtype=torch.float32,
            ).to(device)
            if converter.tgt_sr != spec.sample_rate
            else None
        )

    def _push_input(self, mono: Any, frames: int) -> int:
        spec = self.spec
        block_frame_16k = 160 * spec.block_frame // spec.zero_crossing
        self.input_wav[: -spec.block_frame] = self.input_wav[spec.block_frame :].clone()
        self.input_wav[-frames:] = self.torch.from_numpy(mono).to(self.device)
        self.input_wav_resampled[:-block_frame_16k] = self.input_wav_resampled[
            block_frame_16k:
        ].clone()
        resample_input = self.input_wav[-frames - 2 * spec.zero_crossing :]
        resampled = self.input_resampler(resample_input)
        self.input_wav_resampled[-160 * (frames // spec.zero_crossing + 1) :] = resampled[160:]
        return block_frame_16k

    def _infer(self, block_frame_16k: int) -> Any:
        spec = self.spec
        inferred = self.converter.infer(
            self.input_wav_resampled,
            block_frame_16k,
            spec.extra_frame // spec.zero_crossing,
            (spec.block_frame + spec.sola_buffer_frame + spec.sola_search_frame)
            // spec.zero_crossing,
            self.f0_method,
        )
        return self.output_resampler(inferred) if self.output_resampler else inferred

    def _clear_output_state(self) -> None:
        self.sola_buffer.zero_()
        self.converter.cache_pitch.zero_()
        self.converter.cache_pitchf.zero_()

    def _match_rms(self, inferred: Any) -> None:
        if self.rms_mix_rate >= 1:
            return
        reference = self.input_wav[
            self.spec.extra_frame : self.spec.extra_frame + inferred.shape[0]
        ]
        input_rms = self.torch.sqrt(self.torch.mean(reference**2) + 1e-6)
        output_rms = self.torch.sqrt(self.torch.mean(inferred**2) + 1e-6)
        inferred *= self.torch.pow(input_rms / output_rms, 1.0 - self.rms_mix_rate)

    def _crossfade(self, inferred: Any) -> Any:
        size = self.spec.sola_buffer_frame
        convolution_input = inferred[None, None, : size + self.spec.sola_search_frame]
        correlation = self.functional.conv1d(convolution_input, self.sola_buffer[None, None, :])
        denominator = self.torch.sqrt(
            self.functional.conv1d(convolution_input**2, self.sola_denominator) + 1e-8
        )
        sola_offset = int(self.torch.argmax(correlation[0, 0] / denominator[0, 0]))
        inferred = inferred[sola_offset:]
        inferred[:size] *= self.fade_in
        inferred[:size] += self.sola_buffer * self.fade_out
        self.sola_buffer[:] = inferred[self.spec.block_frame : self.spec.block_frame + size]
        return inferred[: self.spec.block_frame]

    def _clear_conversion_state(self) -> None:
        self.input_wav.zero_()
        self.input_wav_resampled.zero_()
        self._clear_output_state()
        self.output_active = False

    def _measure_input(self, indata: Any) -> tuple[Any, float, float]:
        mono = select_mono_channel(self.np, indata)
        peak_in = float(self.np.max(self.np.abs(mono))) if mono.size else 0.0
        rms = float(self.np.sqrt(self.np.mean(self.np.square(mono), dtype=self.np.float64)))
        input_db = float(20 * self.np.log10(max(rms, 1e-6)))
        return mono, peak_in, input_db

    def _convert_output(self, block_frame_16k: int) -> tuple[Any, int, float]:
        infer_started = time.perf_counter()
        inferred = self._infer(block_frame_16k)
        self._match_rms(inferred)
        output = (
            self._crossfade(inferred).repeat(self.spec.output_channels, 1).t().float().cpu().numpy()
        )
        infer_ms = int((time.perf_counter() - infer_started) * 1000)
        peak_out = float(self.np.max(self.np.abs(output))) if output.size else 0.0
        self.output_active = True
        return output, infer_ms, peak_out

    @staticmethod
    def _speech_source(decision: VoiceActivityDecision, level_active: bool) -> str | None:
        if decision.process_block or decision.active:
            return "vad"
        if level_active:
            return "input_level"
        return None

    def _normal_callback(self, indata: Any, outdata: Any, frames: int) -> dict[str, Any]:
        mono, peak_in, input_db = self._measure_input(indata)
        decision = self.vad.process(mono)
        process_audio = should_process_audio(decision, input_db, self.input_gate_db)
        block_frame_16k = self._push_input(mono, frames)
        infer_ms = 0
        peak_out = 0.0
        if process_audio:
            output, infer_ms, peak_out = self._convert_output(block_frame_16k)
            outdata[:] = output
        else:
            outdata.fill(0)
            if self.output_active:
                self._clear_output_state()
            self.output_active = False
        return {
            "inference_delta": int(process_audio),
            "skipped_delta": int(not process_audio),
            "suppressed_delta": 0,
            "completed_delta": 0,
            "infer_ms": infer_ms,
            "peak_in": peak_in,
            "peak_out": peak_out,
            "input_db": input_db,
            "vad_probability": decision.probability,
            "speech_detected": process_audio,
            "speech_source": self._speech_source(decision, process_audio),
            "rvc_inference_active": process_audio,
            "playback_active": process_audio,
            "microphone_suppressed": False,
        }

    def _test_capture_callback(self, indata: Any, outdata: Any, frames: int) -> dict[str, Any]:
        mono, peak_in, input_db = self._measure_input(indata)
        decision = self.vad.process(mono)
        level_active = input_db >= self.input_gate_db
        speech_detected = decision.active or level_active
        inference_active = False
        playback_active = False
        completed_delta = 0
        infer_ms = 0
        peak_out = 0.0

        if speech_detected:
            if not self.test_session.utterance_active:
                self._clear_conversion_state()
                self.test_session.begin_utterance()
            else:
                self.test_session.mark_speech()
            block_frame_16k = self._push_input(mono, frames)
            output, infer_ms, _converted_peak = self._convert_output(block_frame_16k)
            self.test_session.buffer_output(output.copy())
            inference_active = True
            max_blocks = max(
                1,
                int(
                    TEST_MODE_MAX_UTTERANCE_SECONDS
                    / (self.spec.block_frame / self.spec.sample_rate)
                ),
            )
            if len(self.test_session.outputs) >= max_blocks:
                playback = self.test_session.start_playback()
                outdata[:] = playback
                peak_out = float(self.np.max(self.np.abs(playback))) if playback.size else 0.0
                playback_active = True
                completed_delta = 1
                self.vad.reset()
            else:
                outdata.fill(0)
        elif self.test_session.utterance_active:
            required_silence_callbacks = max(
                1,
                ceil(
                    TEST_MODE_END_SILENCE_SECONDS / (self.spec.block_frame / self.spec.sample_rate)
                ),
            )
            if self.test_session.mark_silence(required_silence_callbacks):
                playback = self.test_session.start_playback()
                outdata[:] = playback
                peak_out = float(self.np.max(self.np.abs(playback))) if playback.size else 0.0
                playback_active = True
                completed_delta = 1
                self.vad.reset()
            else:
                outdata.fill(0)
        else:
            outdata.fill(0)

        return {
            "inference_delta": int(inference_active),
            "skipped_delta": int(not inference_active),
            "suppressed_delta": 0,
            "completed_delta": completed_delta,
            "infer_ms": infer_ms,
            "peak_in": peak_in,
            "peak_out": peak_out,
            "input_db": input_db,
            "vad_probability": decision.probability,
            "speech_detected": speech_detected,
            "speech_source": (
                self._speech_source(decision, level_active) if speech_detected else None
            ),
            "rvc_inference_active": inference_active,
            "playback_active": playback_active,
            "microphone_suppressed": False,
        }

    def _test_suppressed_callback(self, outdata: Any) -> dict[str, Any]:
        playback_active = False
        peak_out = 0.0
        if self.test_session.phase == "playback":
            playback = self.test_session.playback_output()
            if playback is not None:
                outdata[:] = playback
                peak_out = float(self.np.max(self.np.abs(playback))) if playback.size else 0.0
                playback_active = True
            else:
                outdata.fill(0)
        elif self.test_session.phase == "tail":
            outdata.fill(0)
            if self.test_session.finish_tail_callback():
                self._clear_conversion_state()
                self.vad.reset()
        else:
            raise RuntimeError(f"invalid test mode phase: {self.test_session.phase}")
        return {
            "inference_delta": 0,
            "skipped_delta": 1,
            "suppressed_delta": 1,
            "completed_delta": 0,
            "infer_ms": 0,
            "peak_in": 0.0,
            "peak_out": peak_out,
            "input_db": -120.0,
            "vad_probability": 0.0,
            "speech_detected": False,
            "speech_source": None,
            "rvc_inference_active": False,
            "playback_active": playback_active,
            "microphone_suppressed": True,
        }

    def callback(self, indata: Any, outdata: Any, frames: int, _times: Any, status: Any) -> None:
        try:
            if status:
                with self.metrics_lock:
                    self.metrics["xruns"] += 1
            with getattr(self, "control_lock", threading.RLock()):
                bypass = bool(getattr(self, "bypass", False))
                muted = bool(getattr(self, "muted", False))
                push_to_talk_enabled = bool(
                    getattr(self, "push_to_talk_enabled", False)
                )
                push_to_talk_pressed = bool(
                    getattr(self, "push_to_talk_pressed", False)
                )
                recorder = getattr(self, "recorder", None)
            effective_muted = muted or (
                push_to_talk_enabled and not push_to_talk_pressed
            )
            if effective_muted or bypass:
                mono, peak_in, input_db = self._measure_input(indata)
                if effective_muted:
                    outdata.fill(0)
                else:
                    outdata[:] = self.np.repeat(
                        mono[:, None], self.spec.output_channels, axis=1
                    )
                peak_out = float(self.np.max(self.np.abs(outdata))) if outdata.size else 0.0
                result = {
                    "inference_delta": 0,
                    "skipped_delta": 1,
                    "suppressed_delta": int(effective_muted),
                    "completed_delta": 0,
                    "infer_ms": 0,
                    "peak_in": peak_in,
                    "peak_out": peak_out,
                    "input_db": input_db,
                    "vad_probability": 0.0,
                    "speech_detected": False,
                    "speech_source": None,
                    "rvc_inference_active": False,
                    "playback_active": bypass and not effective_muted,
                    "microphone_suppressed": effective_muted,
                }
            elif not self.test_session.enabled:
                result = self._normal_callback(indata, outdata, frames)
            elif self.test_session.phase == "capture":
                result = self._test_capture_callback(indata, outdata, frames)
            else:
                result = self._test_suppressed_callback(outdata)
            if recorder is not None:
                recorder.enqueue(select_mono_channel(self.np, indata), outdata)
            with self.metrics_lock:
                self.metrics.update(
                    callbacks=self.metrics["callbacks"] + 1,
                    inference_callbacks=(
                        self.metrics["inference_callbacks"] + result["inference_delta"]
                    ),
                    skipped_callbacks=(self.metrics["skipped_callbacks"] + result["skipped_delta"]),
                    suppressed_callbacks=(
                        self.metrics["suppressed_callbacks"] + result["suppressed_delta"]
                    ),
                    completed_utterances=(
                        self.metrics["completed_utterances"] + result["completed_delta"]
                    ),
                    infer_ms=result["infer_ms"],
                    peak_in=round(result["peak_in"], 4),
                    peak_out=round(result["peak_out"], 4),
                    input_db=round(result["input_db"], 1),
                    vad_probability=round(result["vad_probability"], 3),
                    speech_detected=result["speech_detected"],
                    speech_source=result["speech_source"],
                    rvc_inference_active=result["rvc_inference_active"],
                    test_mode=self.test_session.enabled,
                    test_phase=self.test_session.phase,
                    buffered_blocks=len(self.test_session.outputs),
                    playback_active=result["playback_active"],
                    microphone_suppressed=result["microphone_suppressed"],
                    bypass=bypass,
                    muted=muted,
                    push_to_talk_enabled=push_to_talk_enabled,
                    push_to_talk_pressed=push_to_talk_pressed,
                    recording=recorder is not None,
                )
        except Exception:  # noqa: BLE001 - PortAudio callback boundary
            outdata.fill(0)
            detail = traceback.format_exc()
            self.callback_error.append(detail)
            if self.warming:
                raise RuntimeError(detail) from None
            raise self.sd.CallbackAbort from None

    def warmup(self) -> None:
        timeline = self.np.arange(self.spec.block_frame, dtype="float32") / self.spec.sample_rate
        voiced = (0.08 * self.np.sin(2 * self.np.pi * 220.0 * timeline)).astype("float32")
        probe = self.np.repeat(voiced[:, None], self.spec.input_channels, axis=1)
        mono = select_mono_channel(self.np, probe)
        self.vad.process(mono)
        block_frame_16k = self._push_input(mono, self.spec.block_frame)
        inferred = self._infer(block_frame_16k)
        self._match_rms(inferred)
        self._crossfade(inferred)
        self.warming = False
        self.reset()

    def reset(self) -> None:
        self.callback_error.clear()
        self._clear_conversion_state()
        self.vad.reset()
        self.test_session.configure(False)
        with self.metrics_lock:
            self.metrics.update(
                callbacks=0,
                inference_callbacks=0,
                skipped_callbacks=0,
                suppressed_callbacks=0,
                xruns=0,
                infer_ms=0,
                peak_in=0.0,
                peak_out=0.0,
                input_db=-120.0,
                vad_probability=0.0,
                speech_detected=False,
                rvc_inference_active=False,
                test_mode=False,
                test_phase="off",
                buffered_blocks=0,
                completed_utterances=0,
                playback_active=False,
                microphone_suppressed=False,
                input_overruns=0,
                output_underruns=0,
                pipeline_depth=0,
                bypass=self.bypass,
                muted=self.muted,
                push_to_talk_enabled=self.push_to_talk_enabled,
                push_to_talk_pressed=self.push_to_talk_pressed,
                recording=self.recorder is not None,
            )

    def configure_control(
        self,
        *,
        bypass: bool | None,
        muted: bool | None,
        push_to_talk_enabled: bool | None = None,
        push_to_talk_pressed: bool | None = None,
    ) -> None:
        with self.control_lock:
            if bypass is not None:
                self.bypass = bypass
            if muted is not None:
                self.muted = muted
            if push_to_talk_enabled is not None:
                self.push_to_talk_enabled = push_to_talk_enabled
                if not push_to_talk_enabled:
                    self.push_to_talk_pressed = False
            if push_to_talk_pressed is not None:
                self.push_to_talk_pressed = push_to_talk_pressed
            self._clear_conversion_state()
        with self.metrics_lock:
            self.metrics.update(
                bypass=self.bypass,
                muted=self.muted,
                push_to_talk_enabled=self.push_to_talk_enabled,
                push_to_talk_pressed=self.push_to_talk_pressed,
            )

    def configure_recording(
        self,
        enabled: bool,
        *,
        directory: str | None,
        session_id: str,
    ) -> dict[str, Any]:
        with self.control_lock:
            if enabled and self.recorder is None:
                if not directory:
                    raise ValueError("recording directory is required")
                self.recorder = RealtimeAudioRecorder(
                    self.np,
                    directory,
                    session_id,
                    self.spec.sample_rate,
                    self.spec.output_channels,
                )
            elif not enabled and self.recorder is not None:
                recorder = self.recorder
                self.recorder = None
                self.recording_result = recorder.close()
        with self.metrics_lock:
            self.metrics.update(self.recording_result)
            self.metrics["recording"] = self.recorder is not None
            return dict(self.metrics)

    def close_recording(self) -> dict[str, Any]:
        return self.configure_recording(False, directory=None, session_id="")

    def configure_test_mode(self, enabled: bool) -> None:
        self._clear_conversion_state()
        self.vad.reset()
        self.test_session.configure(enabled)
        with self.metrics_lock:
            self.metrics.update(
                test_mode=enabled,
                test_phase=self.test_session.phase,
                buffered_blocks=0,
                completed_utterances=0,
                playback_active=False,
                microphone_suppressed=False,
                suppressed_callbacks=0,
            )

    def metrics_snapshot(self) -> dict[str, Any]:
        with self.metrics_lock:
            return dict(self.metrics)


class AsyncRealtimeAudioBridge:
    """Move model inference off PortAudio's real-time callback thread."""

    def __init__(
        self,
        processor: RealtimeAudioProcessor,
        *,
        slot_count: int = 4,
        stop_timeout_seconds: float = 5.0,
    ) -> None:
        if slot_count < 3:
            raise ValueError("realtime audio bridge requires at least three slots")
        if stop_timeout_seconds <= 0:
            raise ValueError("realtime audio stop timeout must be positive")
        self.processor = processor
        self.stop_timeout_seconds = stop_timeout_seconds
        self.np = processor.np
        spec = processor.spec
        self.inputs = [
            self.np.empty((spec.block_frame, spec.input_channels), dtype="float32")
            for _ in range(slot_count)
        ]
        self.outputs = [
            self.np.zeros((spec.block_frame, spec.output_channels), dtype="float32")
            for _ in range(slot_count)
        ]
        self.free: queue.Queue[int] = queue.Queue()
        self.pending: queue.Queue[int | None] = queue.Queue(maxsize=slot_count)
        self.completed: queue.Queue[int] = queue.Queue(maxsize=slot_count)
        for index in range(slot_count):
            self.free.put(index)
        self.running = threading.Event()
        self.error: str | None = None
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.running.set()
        self.thread = threading.Thread(
            target=self._work,
            name="voxweave-realtime-inference",
            daemon=True,
        )
        self.thread.start()

    def stop(self) -> None:
        self.running.clear()
        while True:
            try:
                index = self.pending.get_nowait()
            except queue.Empty:
                break
            if index is not None:
                self.free.put(index)
        try:
            self.pending.put_nowait(None)
        except queue.Full:
            pass
        if self.thread:
            self.thread.join(timeout=self.stop_timeout_seconds)
            if self.thread.is_alive():
                raise RealtimeInferenceStopTimeout(
                    "realtime inference did not stop within "
                    f"{self.stop_timeout_seconds:g} seconds"
                )

    def _publish_output(self, index: int) -> None:
        try:
            self.completed.put_nowait(index)
            return
        except queue.Full:
            pass
        try:
            stale = self.completed.get_nowait()
        except queue.Empty:
            pass
        else:
            self.free.put(stale)
        self.completed.put_nowait(index)

    def _work(self) -> None:
        while self.running.is_set() or not self.pending.empty():
            try:
                index = self.pending.get(timeout=0.1)
            except queue.Empty:
                continue
            if index is None:
                return
            try:
                self.processor.callback(
                    self.inputs[index],
                    self.outputs[index],
                    self.processor.spec.block_frame,
                    None,
                    None,
                )
            except Exception:  # noqa: BLE001 - inference thread boundary
                self.error = traceback.format_exc()
                self.outputs[index].fill(0)
                self._publish_output(index)
                self.running.clear()
                return
            self._publish_output(index)

    def _latest_output(self) -> int | None:
        try:
            latest = self.completed.get_nowait()
        except queue.Empty:
            return None
        while True:
            try:
                newer = self.completed.get_nowait()
            except queue.Empty:
                return latest
            self.free.put(latest)
            latest = newer

    def callback(self, indata: Any, outdata: Any, frames: int, _times: Any, status: Any) -> None:
        if self.error:
            outdata.fill(0)
            raise self.processor.sd.CallbackAbort from None
        if frames != self.processor.spec.block_frame:
            outdata.fill(0)
            self.error = f"unexpected realtime block size: {frames}"
            raise self.processor.sd.CallbackAbort from None

        output_index = self._latest_output()
        if output_index is None:
            outdata.fill(0)
        else:
            self.np.copyto(outdata, self.outputs[output_index])
            self.free.put(output_index)

        dropped_inputs = 0
        while True:
            try:
                stale_input = self.pending.get_nowait()
            except queue.Empty:
                break
            if stale_input is not None:
                self.free.put(stale_input)
                dropped_inputs += 1
        try:
            input_index = self.free.get_nowait()
        except queue.Empty:
            input_index = None
        if input_index is not None:
            self.np.copyto(self.inputs[input_index], indata)
            try:
                self.pending.put_nowait(input_index)
            except queue.Full:
                self.free.put(input_index)
                input_index = None

        with self.processor.metrics_lock:
            if status:
                self.processor.metrics["xruns"] += 1
            if input_index is None or dropped_inputs:
                self.processor.metrics["input_overruns"] += max(1, dropped_inputs)
            if output_index is None:
                self.processor.metrics["output_underruns"] += 1
            self.processor.metrics["pipeline_depth"] = self.pending.qsize()


def run_audio_stream(
    *,
    sd: Any,
    processor: RealtimeAudioProcessor,
    input_device: int,
    output_device: int,
    hostapi: str,
    block_seconds: float,
    test_mode: bool,
    emit: Any,
    stop_event: threading.Event,
) -> None:
    spec = processor.spec
    processor.configure_test_mode(test_mode)
    bridge = AsyncRealtimeAudioBridge(processor)
    stream = None
    try:
        bridge.start()
        stream = sd.Stream(
            device=(input_device, output_device),
            samplerate=spec.sample_rate,
            blocksize=spec.block_frame,
            channels=(spec.input_channels, spec.output_channels),
            dtype="float32",
            callback=bridge.callback,
        )
        stream.start()
        emit(
            {
                "ok": True,
                "event": "running",
                "device": str(processor.device),
                "input_device": input_device,
                "output_device": output_device,
                "hostapi": hostapi,
                "sample_rate": spec.sample_rate,
                "input_channels": spec.input_channels,
                "output_channels": spec.output_channels,
                "block_seconds": spec.block_frame / spec.sample_rate,
                "test_mode": test_mode,
                "estimated_latency_ms": int(
                    (2 * spec.block_frame + spec.sola_buffer_frame) / spec.sample_rate * 1000
                ),
                "vad": "silero-v6",
            }
        )
        while not stop_event.wait(0.2):
            if bridge.error or processor.callback_error:
                raise RuntimeError(bridge.error or processor.callback_error[-1])
            if not stream.active:
                raise RuntimeError("audio stream stopped unexpectedly")
            snapshot = processor.metrics_snapshot()
            emit(
                {
                    "ok": True,
                    "event": "metrics",
                    **snapshot,
                    "overloaded": snapshot["infer_ms"] >= block_seconds * 1000,
                }
            )
    finally:
        try:
            if stream is not None:
                try:
                    stream.abort()
                finally:
                    stream.close()
        finally:
            bridge.stop()
