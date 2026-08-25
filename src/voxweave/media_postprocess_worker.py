from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operation", required=True)
    parser.add_argument("--request", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from voxweave.media_postprocess import (
        align,
        dereverb,
        finalize_long,
        finalize_selected,
        prepare_long,
        prepare_selected,
    )

    if args.operation == "align":
        result = align(
            Path(request["converted"]), Path(request["original"]), Path(request["output"])
        )
    elif args.operation == "dereverb":
        result = dereverb(
            Path(request["input"]),
            Path(request["output"]),
            float(request["strength"]),
        )
    elif args.operation == "prepare-long":
        result = prepare_long(Path(request["input"]), Path(request["chunk_dir"]))
    elif args.operation == "finalize-long":
        result = finalize_long(request["manifest"], Path(request["output"]))
    elif args.operation == "prepare-selected":
        result = prepare_selected(
            Path(request["input"]),
            Path(request["output"]),
            Path(request["work_dir"]),
            list(request["segments"]),
            set(request["selected_speakers"]),
            str(request["overlap_policy"]),
            set(request["selected_segment_ids"])
            if request.get("selected_segment_ids") is not None
            else None,
        )
    elif args.operation == "finalize-selected":
        result = finalize_selected(request["manifest"])
    else:
        raise ValueError(f"unknown media postprocess operation: {args.operation}")
    print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
