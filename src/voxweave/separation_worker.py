from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from .runtime_contract import runtime_contract
except ImportError:
    from runtime_contract import runtime_contract  # type: ignore[no-redef]


def main() -> int:
    separation = runtime_contract().source_separation
    parser = argparse.ArgumentParser(description="Isolated RVC PyMSS adapter for VoxWeave")
    parser.add_argument("--rvc-root", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=separation.model_id)
    arguments = parser.parse_args()

    rvc_root = Path(arguments.rvc_root).resolve()
    input_path = Path(arguments.input).resolve()
    output_root = Path(arguments.output).resolve()
    vocals_root = output_root / "vocals"
    instrumental_root = output_root / "instrumental"
    request_path = output_root / "pymss-request.json"
    output_root.mkdir(parents=True, exist_ok=True)

    os.chdir(rvc_root)
    sys.path.insert(0, str(rvc_root))
    sys.argv[:] = [sys.argv[0]]
    from tools.pymss_webui import _pymss_worker_main  # noqa: PLC0415

    request = {
        "model_id": arguments.model,
        "input_paths": [str(input_path)],
        "output_format": "wav",
        "desired_root": str(vocals_root),
        "secondary_root": str(instrumental_root),
        "model_dtype": "auto",
        "allow_fp32_retry": False,
    }
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return_code = _pymss_worker_main(str(request_path))
    vocals = sorted(vocals_root.glob("*.wav"))
    instrumentals = sorted(instrumental_root.glob("*.wav"))
    result = {
        "ok": return_code == 0 and len(vocals) == 1 and len(instrumentals) == 1,
        "backend": separation.backend,
        "model_id": arguments.model,
        "model_source": separation.source,
        "code_license": separation.code_license_spdx,
        "vocals": [str(path) for path in vocals],
        "instrumental": [str(path) for path in instrumentals],
    }
    if not result["ok"]:
        result["error"] = (
            f"PyMSS returned {return_code} and produced {len(vocals)}/{len(instrumentals)} stems"
        )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
