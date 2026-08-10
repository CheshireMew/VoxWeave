from __future__ import annotations

import threading
import time
import traceback
from dataclasses import dataclass
from typing import Any

VAD_SAMPLE_RATE = 16000
VAD_WINDOW_SAMPLES = 512
LEVEL_GATE_DB = -50.0


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


def should_process_audio(decision: VoiceActivityDecision, input_db: float) -> bool:
    return decision.process_block or input_db >= LEVEL_GATE_DB


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


class HalfDuplexGate:
    """Discard the input block captured while the preceding output block plays."""

    def __init__(self) -> None:
        self.enabled = False
        self._suppress_next_input = False

    def configure(self, enabled: bool) -> None:
        self.enabled = enabled
        self._suppress_next_input = False

    def begin_callback(self) -> bool:
        if not self.enabled or not self._suppress_next_input:
            return True
        self._suppress_next_input = False
        return False

    def output_started(self) -> None:
        if self.enabled:
            self._suppress_next_input = True


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
        self.vad = StreamingVoiceActivityDetector(
            torch=torch,
            transforms=transforms,
            model=vad_model,
            input_sample_rate=spec.sample_rate,
            threshold=vad_threshold,
        )
        self.output_active = False
        self.half_duplex = HalfDuplexGate()
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
            "microphone_suppressed": False,
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

    def callback(self, indata: Any, outdata: Any, frames: int, _times: Any, status: Any) -> None:
        try:
            if status:
                with self.metrics_lock:
                    self.metrics["xruns"] += 1
            microphone_enabled = self.half_duplex.begin_callback()
            if microphone_enabled:
                mono = select_mono_channel(self.np, indata)
                peak_in = float(self.np.max(self.np.abs(mono))) if mono.size else 0.0
                rms = float(self.np.sqrt(self.np.mean(self.np.square(mono), dtype=self.np.float64)))
                input_db = float(20 * self.np.log10(max(rms, 1e-6)))
                decision = self.vad.process(mono)
                process_audio = should_process_audio(decision, input_db)
            else:
                mono = self.np.zeros(frames, dtype="float32")
                peak_in = 0.0
                input_db = -120.0
                decision = VoiceActivityDecision(False, False, 0.0)
                process_audio = False
                self.vad.reset()
            block_frame_16k = self._push_input(mono, frames)
            infer_ms = 0
            peak_out = 0.0
            inference_callbacks = self.metrics["inference_callbacks"]
            skipped_callbacks = self.metrics["skipped_callbacks"]
            suppressed_callbacks = self.metrics["suppressed_callbacks"]
            if process_audio:
                infer_started = time.perf_counter()
                inferred = self._infer(block_frame_16k)
                self._match_rms(inferred)
                output = (
                    self._crossfade(inferred)
                    .repeat(self.spec.output_channels, 1)
                    .t()
                    .float()
                    .cpu()
                    .numpy()
                )
                outdata[:] = output
                infer_ms = int((time.perf_counter() - infer_started) * 1000)
                peak_out = float(self.np.max(self.np.abs(output))) if output.size else 0.0
                inference_callbacks += 1
                self.output_active = True
                self.half_duplex.output_started()
            else:
                outdata.fill(0)
                skipped_callbacks += 1
                if not microphone_enabled:
                    suppressed_callbacks += 1
                if self.output_active:
                    self._clear_output_state()
                self.output_active = False
            with self.metrics_lock:
                self.metrics.update(
                    callbacks=self.metrics["callbacks"] + 1,
                    inference_callbacks=inference_callbacks,
                    skipped_callbacks=skipped_callbacks,
                    suppressed_callbacks=suppressed_callbacks,
                    infer_ms=infer_ms,
                    peak_in=round(peak_in, 4),
                    peak_out=round(peak_out, 4),
                    input_db=round(input_db, 1),
                    vad_probability=round(decision.probability, 3),
                    speech_detected=process_audio,
                    speech_source=(
                        "vad"
                        if decision.process_block
                        else ("input_level" if process_audio else None)
                    ),
                    rvc_inference_active=process_audio,
                    test_mode=self.half_duplex.enabled,
                    microphone_suppressed=not microphone_enabled,
                )
        except Exception:  # noqa: BLE001 - PortAudio callback boundary
            outdata.fill(0)
            detail = traceback.format_exc()
            self.callback_error.append(detail)
            if self.warming:
                raise RuntimeError(detail) from None
            raise self.sd.CallbackAbort from None

    def warmup(self) -> None:
        probe = self.np.zeros((self.spec.block_frame, self.spec.input_channels), dtype="float32")
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
        self.input_wav.zero_()
        self.input_wav_resampled.zero_()
        self.sola_buffer.zero_()
        self._clear_output_state()
        self.vad.reset()
        self.output_active = False
        self.half_duplex.configure(False)
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
                microphone_suppressed=False,
            )

    def configure_test_mode(self, enabled: bool) -> None:
        self.half_duplex.configure(enabled)
        with self.metrics_lock:
            self.metrics.update(
                test_mode=enabled,
                microphone_suppressed=False,
                suppressed_callbacks=0,
            )

    def metrics_snapshot(self) -> dict[str, Any]:
        with self.metrics_lock:
            return dict(self.metrics)


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
    stream = sd.Stream(
        device=(input_device, output_device),
        samplerate=spec.sample_rate,
        blocksize=spec.block_frame,
        channels=(spec.input_channels, spec.output_channels),
        dtype="float32",
        callback=processor.callback,
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
                (spec.block_frame + spec.sola_buffer_frame) / spec.sample_rate * 1000
            ),
            "vad": "silero-v6",
        }
    )
    try:
        while not stop_event.wait(0.2):
            if processor.callback_error:
                raise RuntimeError(processor.callback_error[-1])
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
        stream.abort()
        stream.close()
