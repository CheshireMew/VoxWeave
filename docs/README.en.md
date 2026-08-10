# VoxWeave

[简体中文](../README.md)

VoxWeave is a local-first, high-quality offline and realtime RVC voice-conversion workstation. The desktop app, CLI, and loopback HTTP/WebSocket API are clients of one backend service and one persistent state boundary.

Version 0.1 supports audio, singing, video, realtime microphones, batch folders, watched folders, source separation, VAD, speaker clustering, selected-speaker conversion, and up to four preview variants. Results and realtime sessions record the exact model and index hashes. Training, GPT-SoVITS, and virtual audio devices are intentionally out of scope.

Windows 11 with NVIDIA CUDA is the currently validated platform. Linux and macOS boundaries are present in source but have not yet been validated on physical machines.

## Run from source

Python 3.12, Git, and FFmpeg are required. On Windows, choose a data directory outside the source checkout:

```powershell
.\scripts\bootstrap.ps1 `
  -DataRoot D:\Tools\VoxWeave `
  -RvcRoot E:\path\to\Retrieval-based-Voice-Conversion-WebUI `
  -RvcPython E:\path\to\Retrieval-based-Voice-Conversion-WebUI\.venv\Scripts\python.exe `
  -Ffmpeg D:\path\to\ffmpeg.exe `
  -Ffprobe D:\path\to\ffprobe.exe
.\scripts\run.ps1
```

On Windows, you can also double-click `VoxWeave.bat` in the repository root. It delegates to the same PowerShell launcher and does not maintain a second environment or service path.

The selected data root owns the Python environment, caches, temporary files, state, downloads, and generated artifacts. If no external RVC checkout is supplied, call `runtime.install` after bootstrapping; the service installs the pinned upstream revision into the data root.

`requirements.lock` is the single validated Windows/Python 3.12 dependency set used by both the bootstrap and CI.

## Automation

Always inspect the live contract before invoking an operation:

```powershell
.\scripts\voxweave.ps1 --json describe
.\scripts\voxweave.ps1 --json execute runtime.inspect --arguments '{}'
```

Requests use `voxweave-control v1`. Long operations return a `task_id` immediately and can be observed through `task.get` or the authenticated loopback WebSocket. Outputs are never overwritten unless `overwrite: true` is explicit. See [the protocol reference](PROTOCOL.md) and [architecture](ARCHITECTURE.md).

The Live Voice page lists the Windows audio hosts and devices reported by the configured RVC runtime. It prefers the default Windows WASAPI input and output, falling back to another host only when WASAPI is unavailable. The selected model, audio host, input/output devices, pitch, algorithms, ratios, microphone activation level, latency profile, and test mode are persisted as one user preference record. Devices are restored by host and name rather than unstable PortAudio IDs. Microphones are captured as mono. Silero VAD detects speech, while an input-level fallback keeps audible blocks flowing when VAD misses it. Normal and test modes now use the same configurable activation level, so background noise cannot enter through a separate fixed gate. In test mode, speech is converted and buffered without playback; once the user stops speaking, the complete utterance is played. Microphone input is discarded throughout playback and until the speaker tail has cleared. Toggling test mode does not reload the prepared model. The page exposes speech detection, conversion output, and input/output meters. Its 0.5-second default can be changed to a 1.0-second stable block when inference is slower. Input and output must belong to the same host API, and headphones are recommended for normal continuous mode. Automation uses `realtime.devices`, `realtime.start`, `realtime.status`, and `realtime.stop`. A live session does not occupy the offline worker, but it owns the GPU: it cannot start while a task is running, and newly submitted tasks remain queued until live conversion stops.

After updating the source, run `.\scripts\voxweave.ps1 service stop` before `.\scripts\run.ps1` to reload an existing background service through its authenticated shutdown path.

VoxWeave never removes intermediate artifacts automatically. Use the Settings archive action with its confirmation dialog, or explicitly submit `storage.archive`, to move artifacts from finished tasks and rewrite persisted task paths. Same-volume archives are directory moves; cross-volume archives are copied and verified before their source copy is removed.

Structured JSON logs are rotated under the data root at 10 MB with five retained files. The service-owned diagnostics snapshot includes runtime, models, the realtime session, tasks, recent events, storage totals, and log inventory without embedding model or media contents.

VoxWeave does not distribute voice models. Local files are indexed in place; catalog entries require an HTTPS source, exact sizes and SHA-256 hashes, and an SPDX license. See [the model policy](../MODEL_POLICY.md).

## License

VoxWeave source is licensed under [AGPL-3.0-only](../LICENSE). Dependencies, optional inference components, and models retain their own terms; see [third-party notices](../THIRD_PARTY_NOTICES.md).
