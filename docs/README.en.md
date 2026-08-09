# VoxWeave

[简体中文](../README.md)

VoxWeave is a local-first, high-quality offline RVC voice-conversion workstation. The desktop app, CLI, and loopback HTTP/WebSocket API are clients of one backend service, one task database, and one serialized inference queue.

Version 0.1 supports audio, singing, video, batch folders, watched folders, source separation, VAD, speaker clustering, selected-speaker conversion, and up to four preview variants. Every result records the exact model and index hashes. Training, GPT-SoVITS, realtime microphone conversion, and virtual audio devices are intentionally out of scope.

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

## Automation

Always inspect the live contract before invoking an operation:

```powershell
.\scripts\voxweave.ps1 --json describe
.\scripts\voxweave.ps1 --json execute runtime.inspect --arguments '{}'
```

Requests use `voxweave-control v1`. Long operations return a `task_id` immediately and can be observed through `task.get` or the authenticated loopback WebSocket. Outputs are never overwritten unless `overwrite: true` is explicit. See [the protocol reference](PROTOCOL.md) and [architecture](ARCHITECTURE.md).

VoxWeave does not distribute voice models. Local files are indexed in place; catalog entries require an HTTPS source, exact sizes and SHA-256 hashes, and an SPDX license. See [the model policy](../MODEL_POLICY.md).

## License

VoxWeave source is licensed under [AGPL-3.0-only](../LICENSE). Dependencies, optional inference components, and models retain their own terms; see [third-party notices](../THIRD_PARTY_NOTICES.md).
