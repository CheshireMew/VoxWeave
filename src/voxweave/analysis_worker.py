from __future__ import annotations

import argparse
import heapq
import json
from pathlib import Path
from typing import Any


def cluster_average_linkage(
    np: Any,
    embeddings: list[Any | None],
    anchors: list[int],
    threshold: float,
) -> tuple[list[list[int]], int]:
    """Cluster normalized embeddings with exact average linkage in O(n² log n)."""

    active: dict[int, tuple[list[int], Any, int]] = {
        cluster_id: ([anchor], embeddings[anchor].copy(), 1)
        for cluster_id, anchor in enumerate(anchors)
    }
    candidates: list[tuple[float, tuple[int, ...], tuple[int, ...], int, int]] = []
    pair_evaluations = 0

    def add_candidate(left_id: int, right_id: int) -> None:
        nonlocal pair_evaluations
        left_members, left_sum, left_size = active[left_id]
        right_members, right_sum, right_size = active[right_id]
        similarity = float(np.dot(left_sum, right_sum)) / (left_size * right_size)
        pair_evaluations += 1
        heapq.heappush(
            candidates,
            (
                -similarity,
                tuple(left_members),
                tuple(right_members),
                left_id,
                right_id,
            ),
        )

    cluster_ids = list(active)
    for left in range(len(cluster_ids)):
        for right in range(left + 1, len(cluster_ids)):
            add_candidate(cluster_ids[left], cluster_ids[right])

    next_id = len(active)
    while candidates and len(active) > 1:
        negative_score, _left_members, _right_members, left_id, right_id = heapq.heappop(
            candidates
        )
        if left_id not in active or right_id not in active:
            continue
        if -negative_score < threshold:
            break
        left_members, left_sum, left_size = active.pop(left_id)
        right_members, right_sum, right_size = active.pop(right_id)
        merged_id = next_id
        next_id += 1
        active[merged_id] = (
            sorted([*left_members, *right_members]),
            left_sum + right_sum,
            left_size + right_size,
        )
        for other_id in list(active):
            if other_id != merged_id:
                add_candidate(min(other_id, merged_id), max(other_id, merged_id))

    return sorted((value[0] for value in active.values()), key=min), pair_evaluations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audio", required=True)
    parser.add_argument("--speaker-model")
    parser.add_argument("--threshold", type=float, default=0.68)
    parser.add_argument("--ambiguity-margin", type=float, default=0.08)
    args = parser.parse_args()

    import numpy as np
    import torchaudio
    from silero_vad import get_speech_timestamps, load_silero_vad, read_audio

    audio_path = Path(args.audio)
    vad_model = load_silero_vad(onnx=True)
    waveform = read_audio(str(audio_path), sampling_rate=16000)
    bucket_count = min(512, max(1, int(waveform.numel()) // 160))
    bucket_size = max(1, (int(waveform.numel()) + bucket_count - 1) // bucket_count)
    waveform_peaks = [
        round(float(waveform[start : start + bucket_size].abs().max()), 6)
        for start in range(0, int(waveform.numel()), bucket_size)
    ]
    timestamps = get_speech_timestamps(
        waveform,
        vad_model,
        sampling_rate=16000,
        threshold=0.55,
        min_speech_duration_ms=500,
        min_silence_duration_ms=180,
        return_seconds=True,
    )
    embeddings: list[np.ndarray | None] = []
    session = None
    input_name = None
    if args.speaker_model:
        import onnxruntime as ort

        session = ort.InferenceSession(args.speaker_model, providers=["CPUExecutionProvider"])
        input_name = session.get_inputs()[0].name
    for item in timestamps:
        start = int(float(item["start"]) * 16000)
        end = int(float(item["end"]) * 16000)
        chunk = waveform[start:end]
        if session is None or chunk.numel() < 16000:
            embeddings.append(None)
            continue
        feats = torchaudio.compliance.kaldi.fbank(
            chunk.unsqueeze(0),
            num_mel_bins=80,
            frame_length=25,
            frame_shift=10,
            dither=0.0,
            sample_frequency=16000,
            window_type="hamming",
            use_energy=False,
        )
        feats = feats - feats.mean(dim=0, keepdim=True)
        embedding = session.run(None, {input_name: feats.unsqueeze(0).numpy().astype("float32")})[
            0
        ][0]
        norm = float(np.linalg.norm(embedding)) or 1.0
        embeddings.append(embedding / norm)

    valid = [index for index, embedding in enumerate(embeddings) if embedding is not None]
    anchors = [
        index
        for index in valid
        if float(timestamps[index]["end"]) - float(timestamps[index]["start"]) >= 2.0
    ]
    if not anchors:
        anchors = valid
    clusters, pair_evaluations = cluster_average_linkage(
        np, embeddings, anchors, args.threshold
    )
    centroids = []
    for cluster in clusters:
        centroid = np.mean([embeddings[index] for index in cluster], axis=0)
        centroid /= float(np.linalg.norm(centroid)) or 1.0
        centroids.append(centroid)

    speakers: list[int] = []
    similarities: list[float] = []
    overlaps: list[bool | str] = []
    for embedding in embeddings:
        if embedding is None or not centroids:
            speakers.append(0)
            similarities.append(0.0)
            overlaps.append("unknown")
            continue
        scores = [float(np.dot(embedding, centroid)) for centroid in centroids]
        ordered = sorted(scores, reverse=True)
        best = int(np.argmax(scores))
        margin = ordered[0] - ordered[1] if len(ordered) > 1 else 1.0
        unresolved = len(ordered) > 1 and (ordered[0] < 0.48 or margin < args.ambiguity_margin)
        speakers.append(best)
        similarities.append(scores[best])
        overlaps.append("unresolved" if unresolved else False)

    segments = []
    for index, item in enumerate(timestamps):
        segments.append(
            {
                "id": f"segment-{index + 1}",
                "start_seconds": round(float(item["start"]), 3),
                "end_seconds": round(float(item["end"]), 3),
                "speaker": f"speaker-{speakers[index] + 1}",
                "speaker_similarity": round(similarities[index], 6),
                "overlap": overlaps[index],
            }
        )
    print(
        json.dumps(
            {
                "ok": True,
                "vad": "silero-v6",
                "speaker_embedding": "wespeaker" if session else None,
                "speaker_count": len(centroids) if segments else 0,
                "duration_seconds": round(float(waveform.numel()) / 16000.0, 6),
                "waveform_peaks": waveform_peaks,
                "clustering": {
                    "method": "global-average-linkage",
                    "merge_threshold": args.threshold,
                    "ambiguity_margin": args.ambiguity_margin,
                    "pair_evaluations": pair_evaluations,
                },
                "segments": segments,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
