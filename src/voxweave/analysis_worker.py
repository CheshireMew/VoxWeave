from __future__ import annotations

import argparse
import json
from pathlib import Path


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
    clusters = [[index] for index in anchors]
    while len(clusters) > 1:
        best_pair: tuple[int, int] | None = None
        best_score = -1.0
        for left in range(len(clusters)):
            for right in range(left + 1, len(clusters)):
                scores = [
                    float(np.dot(embeddings[a], embeddings[b]))
                    for a in clusters[left]
                    for b in clusters[right]
                ]
                score = float(np.mean(scores))
                if score > best_score:
                    best_score = score
                    best_pair = (left, right)
        if best_pair is None or best_score < args.threshold:
            break
        left, right = best_pair
        clusters[left] = sorted([*clusters[left], *clusters[right]])
        del clusters[right]
    clusters.sort(key=min)
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
                "clustering": {
                    "method": "global-average-linkage",
                    "merge_threshold": args.threshold,
                    "ambiguity_margin": args.ambiguity_margin,
                },
                "segments": segments,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
