# Changelog

## Unreleased

- Replaced timestamp-based file hash caching with authoritative, stable-handle SHA-256 reads so restored timestamps or concurrent writes cannot reuse stale model, checkpoint, archive, or media identities.
- Made realtime audio shutdown bounded and fatal on timeout; an unhealthy resident worker can no longer report a clean stop, reuse its prepared processor, or accept another session.
- Added a clean-commit Windows x64 release pipeline that locks the complete PyInstaller build graph, compares the actual PyInstaller module inventory with the license authority, collects CPython/PyInstaller/Python-package licenses, includes Qt/PySide LGPL source and replacement instructions, creates a deterministic ZIP with SHA-256, and verifies a fresh extracted tree file by file.
- Moved release work outside the repository and limited output to one directory per version and commit, preventing repeated builds from silently growing `.archive`.
- Aligned the Chinese, English, and Japanese release, runtime, and licensing descriptions with the actual portable ZIP boundary.

## 0.2.0 — 2026-08-13

- Added a Windows EXE distribution with the VoxWeave application icon, a reproducible PyInstaller build, and a runtime-focused package allowlist that excludes models and development files.
- Added first-run discovery of existing VoxWeave, RVC, Python, and FFmpeg installations; missing runtime components are downloaded only after user confirmation, and successful verification is reused on later launches.
- Added a Chinese voice catalog with existing-model discovery, localized names, compact one-line downloads, real progress reporting, model/index hash validation, and recommended male and female conversion parameters.
- Fixed realtime test-mode playback and audio routing, added model-specific pitch defaults, changed the input gate default to -30 dB, and retained user-selected realtime settings across restarts.
- Kept executable-local pointers and verification state beside the application while storing large runtimes, models, downloads, logs, caches, and task artifacts in the selected data directory.

- Added service-owned realtime microphone conversion with resident RVC inference, Windows audio-device routing, SOLA block stitching, three latency budgets, persistent session recovery, live performance metrics, protocol operations, and a bilingual desktop page.
- Unified settings writes under the service, made task state/event commits transactional, and moved restart recovery into the task manager.
- Changed watched-folder identity to path plus SHA-256, isolated per-file failures, and exposed scan and batch work as cancellable tasks.
- Added staged runtime/model publication, atomic final media publication with crash recovery, structured rotating logs, service-owned diagnostics, and explicit verified artifact archiving.
- Added conversion-result Schema validation, global task events, stale GUI response rejection, ready-model filtering, safe task errors/results, and preset hash confirmation.
- Split GUI pages, GUI request/presentation code, and media processing by ownership boundaries; CI now rejects QML warnings and uses the locked dependency set.

## 0.1.0 — 2026-08-09

- Added one local backend shared by the PySide6/QML desktop app, CLI, loopback HTTP API, and authenticated task WebSocket.
- Added persistent tasks, cancellation, interruption, hash-verified stage resume, batch folders, and watched folders.
- Added safe RVC model inspection, recursive external scans, index pairing, presets, HTTPS/catalog imports, and public v1 contracts.
- Added clean speech, mixed speech, singing, selected-speaker, preview matrix, audio, and stream-copy video pipelines.
- Added low-energy long-audio chunking with one-load RVC batch inference for full-length media.
- Added Simplified Chinese and English resources, source bootstraps, diagnostics, license notices, model policy, schemas, and Windows CI.

Windows CUDA is the only platform validated for this release. The portable ZIP includes the desktop application's CPython/PySide/Qt runtime, but no installer, managed RVC runtime, FFmpeg, voice model, or telemetry.
