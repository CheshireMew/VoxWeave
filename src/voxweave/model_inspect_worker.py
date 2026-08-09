from __future__ import annotations

import json
import sys

import torch


def main(paths: list[str]) -> int:
    results = {}
    for path in paths:
        try:
            checkpoint = torch.load(path, map_location="cpu", weights_only=True)
            config = checkpoint.get("config") or []
            results[path] = {
                "version": checkpoint.get("version", "v1"),
                "f0": int(checkpoint.get("f0", 1)),
                "sample_rate": int(config[-1]) if config else None,
                "keys": sorted(checkpoint.keys()),
                "status": "ready",
            }
        except Exception as error:  # noqa: BLE001 - untrusted model validation boundary
            results[path] = {"status": "invalid", "error": str(error)}
    print(json.dumps(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
