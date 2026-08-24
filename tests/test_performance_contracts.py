from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np

from voxweave.analysis_worker import cluster_average_linkage
from voxweave.database import Database
from voxweave.hashing import FileVerificationLedger, sha256_file
from voxweave.model_registry import ModelRegistry
from voxweave.task_manager import TaskManager
from voxweave.verified_download import DownloadSpec, download_verified


def test_model_snapshot_hashes_each_large_file_once_per_execution(monkeypatch, tmp_path) -> None:
    model_path = tmp_path / "voice.pth"
    index_path = tmp_path / "voice.index"
    model_path.write_bytes(b"model" * 100)
    index_path.write_bytes(b"index" * 100)
    record = {
        "id": "voice",
        "model_path": str(model_path),
        "model_sha256": sha256_file(model_path),
        "index_path": str(index_path),
        "index_sha256": sha256_file(index_path),
    }
    import voxweave.model_registry as registry_module

    original = registry_module.verify_file
    calls = []

    def counted(path, **kwargs):
        calls.append(Path(path))
        return original(path, **kwargs)

    monkeypatch.setattr(registry_module, "verify_file", counted)
    for _ in range(4):
        ModelRegistry.verify_snapshot(record)

    assert calls == [model_path, index_path]


def test_task_file_ledger_reuses_digest_only_while_identity_is_unchanged(
    monkeypatch, tmp_path
) -> None:
    path = tmp_path / "media.wav"
    path.write_bytes(b"stable-content")
    import voxweave.hashing as hashing_module

    original = hashing_module.verify_file
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(hashing_module, "verify_file", counted)
    ledger = FileVerificationLedger()
    first = ledger.verify(path)
    second = ledger.verify(path)

    assert first is second
    assert calls == 1


def test_download_hash_is_accumulated_while_streaming(monkeypatch, tmp_path) -> None:
    payload = b"verified download" * 100

    class Response:
        headers = {"Content-Length": str(len(payload))}

        def __init__(self) -> None:
            self.stream = io.BytesIO(payload)

        def __enter__(self):
            return self

        def __exit__(self, *_args) -> None:
            return None

        def read(self, size: int) -> bytes:
            return self.stream.read(size)

    monkeypatch.setattr(
        "voxweave.verified_download.urllib.request.urlopen",
        lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setattr(
        "voxweave.verified_download.sha256_file",
        lambda _path: (_ for _ in ()).throw(AssertionError("download was reread")),
    )
    target = tmp_path / "artifact.bin"
    verified = download_verified(
        DownloadSpec(
            "https://example.invalid/artifact.bin",
            target.name,
            len(payload),
            hashlib.sha256(payload).hexdigest(),
        ),
        target,
        cancelled=lambda: False,
        progress=lambda *_args: None,
        progress_start=0.0,
        progress_end=1.0,
    )

    assert target.read_bytes() == payload
    assert verified.path == target.resolve()


def test_database_reuses_configured_connections(tmp_path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    connection_ids = set()
    for _ in range(500):
        with database.connect() as connection:
            connection_ids.add(id(connection))
            connection.execute("SELECT 1").fetchone()
    database.close()

    assert len(connection_ids) == 1


def test_task_event_waiter_wakes_without_polling(tmp_path, monkeypatch) -> None:
    database = Database(tmp_path / "state.sqlite3")
    manager = TaskManager(database)
    release = threading.Event()
    running = threading.Event()

    def work(_arguments, _context):
        running.set()
        release.wait(timeout=2)
        return {"done": True}

    manager.register("test.work", work)
    manager.start()
    submitted = manager.submit("test.work", {})
    assert running.wait(timeout=2)
    after_id = max(event["id"] for event in manager.events(submitted["id"]))
    original_events = manager.repository.events
    query_count = 0

    def counted_events(*args, **kwargs):
        nonlocal query_count
        query_count += 1
        return original_events(*args, **kwargs)

    monkeypatch.setattr(manager.repository, "events", counted_events)
    received = []
    waiter = threading.Thread(
        target=lambda: received.extend(
            manager.wait_events(submitted["id"], after_id, 500, 2.0)
        )
    )
    waiter.start()
    time.sleep(0.1)
    assert waiter.is_alive()
    release.set()
    waiter.join(timeout=2)
    manager.shutdown()
    database.close()

    assert not waiter.is_alive()
    assert received[-1]["state"] == "completed"
    assert query_count <= 3
    assert manager._task_event_generation == {}
    assert manager._task_event_waiters == {}


def test_average_linkage_pair_work_is_quadratic() -> None:
    rng = np.random.default_rng(7)
    base = rng.normal(size=128).astype("float32")
    base /= np.linalg.norm(base)
    embeddings = []
    for _ in range(200):
        value = base + rng.normal(scale=0.01, size=128).astype("float32")
        embeddings.append(value / np.linalg.norm(value))

    started = time.perf_counter()
    clusters, pair_evaluations = cluster_average_linkage(
        np, embeddings, list(range(200)), 0.72
    )

    assert len(clusters) == 1
    assert pair_evaluations <= 2 * 200 * 200
    assert time.perf_counter() - started < 2.0


def test_service_import_does_not_load_heavy_media_math() -> None:
    root = Path(__file__).resolve().parents[1]
    media_processing = (root / "src" / "voxweave" / "media_processing.py").read_text(
        encoding="utf-8"
    )
    assert "numpy" not in media_processing
    assert "scipy" not in media_processing
    assert "soundfile" not in media_processing
    assert "engine.convert(" not in media_processing
    assert "_convert_long_audio_local" not in media_processing
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import voxweave.conversion_runner; "
                "print(','.join(sorted({'numpy','scipy','soundfile'} & set(sys.modules))))"
            ),
        ],
        cwd=root,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    assert completed.stdout.strip() == ""
