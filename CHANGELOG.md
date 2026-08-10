# Changelog

## Unreleased

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

Windows CUDA is the only platform validated for this release. No installer, bundled runtime, voice model, or telemetry is included.
