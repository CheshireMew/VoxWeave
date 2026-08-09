from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def configure(rvc_root: Path, model_root: Path | None = None) -> None:
    os.chdir(rvc_root)
    worker_directory = Path(__file__).resolve().parent
    sys.path[:] = [
        value for value in sys.path if not value or Path(value).resolve() != worker_directory
    ]
    sys.path.insert(0, str(rvc_root))
    assets = rvc_root / "assets"
    os.environ["weight_root"] = str(model_root or assets / "weights")
    os.environ.setdefault("weight_pymss_root", str(assets / "pymss_weights"))
    os.environ.setdefault("index_root", str(rvc_root / "logs"))
    os.environ.setdefault("outside_index_root", str(assets / "indices"))
    os.environ.setdefault("rmvpe_root", str(assets / "rmvpe"))
    os.environ.setdefault("RVC_CUDA_GRAPH", "0")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")


def doctor(arguments: argparse.Namespace) -> int:
    root = Path(arguments.rvc_root).resolve()
    configure(root)
    import torch  # noqa: PLC0415

    required = [
        root / "configs" / "config.py",
        root / "infer" / "vc" / "modules.py",
        root / "assets" / "hubert_base" / "pytorch_model.bin",
        root / "assets" / "rmvpe" / "rmvpe.pt",
    ]
    payload = {
        "ok": all(path.is_file() for path in required),
        "command": "doctor",
        "rvc_root": str(root),
        "required": [{"path": str(path), "exists": path.is_file()} for path in required],
        "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
    }
    emit(payload)
    return 0 if payload["ok"] else 1


def _convert_items(
    arguments: argparse.Namespace, items: list[dict[str, str]]
) -> tuple[list[dict[str, Any]], str]:
    root = Path(arguments.rvc_root).resolve()
    model_path = Path(arguments.model).resolve()
    index_path = Path(arguments.index).resolve() if arguments.index else None
    if not model_path.is_file():
        raise FileNotFoundError(f"model file not found: {model_path}")
    if index_path and not index_path.is_file():
        raise FileNotFoundError(f"index file not found: {index_path}")
    normalized_items = []
    for item in items:
        input_path = Path(item["input"]).resolve()
        output_path = Path(item["output"]).resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"input file not found: {input_path}")
        if output_path.exists() and not arguments.overwrite:
            raise FileExistsError(output_path)
        if output_path.suffix.casefold() not in {".wav", ".flac"}:
            raise ValueError("RVC worker output must be WAV or FLAC")
        normalized_items.append((input_path, output_path))

    configure(root, model_path.parent)
    if arguments.gpu is not None:
        os.environ["CUDA_VISIBLE_DEVICES"] = str(arguments.gpu)
    original_argv = sys.argv[:]
    sys.argv = [original_argv[0], "--pycmd", sys.executable, "--noautoopen"]
    try:
        with contextlib.redirect_stdout(sys.stderr):
            import soundfile as sf  # noqa: PLC0415
            from configs.config import Config  # noqa: PLC0415
            from infer.vc.modules import VC  # noqa: PLC0415

            config = Config()
            converter = VC(config)
            converter.get_vc(model_path.name)
        results = []
        for input_path, output_path in normalized_items:
            started = time.perf_counter()
            with contextlib.redirect_stdout(sys.stderr):
                status, result = converter.vc_single(
                    0,
                    str(input_path),
                    arguments.pitch,
                    arguments.f0,
                    str(index_path) if index_path else "",
                    arguments.index_rate if index_path else 0.0,
                    0,
                    arguments.rms_mix_rate,
                    arguments.protect,
                )
            elapsed = time.perf_counter() - started
            if not result or result[0] is None or result[1] is None:
                raise RuntimeError(str(status))
            sample_rate, audio = result
            output_path.parent.mkdir(parents=True, exist_ok=True)
            sf.write(
                output_path,
                audio,
                sample_rate,
                format="WAV" if output_path.suffix.casefold() == ".wav" else "FLAC",
            )
            results.append(
                {
                    "input": str(input_path),
                    "output": str(output_path),
                    "sample_rate": sample_rate,
                    "duration_seconds": round(len(audio) / sample_rate, 3),
                    "elapsed_seconds": round(elapsed, 3),
                    "status": str(status),
                }
            )
    finally:
        sys.argv = original_argv
    return results, str(config.device)


def convert(arguments: argparse.Namespace) -> int:
    results, device = _convert_items(
        arguments, [{"input": arguments.input, "output": arguments.output}]
    )
    result = results[0]
    emit(
        {
            "ok": True,
            "command": "convert",
            **result,
            "model": str(Path(arguments.model).resolve()),
            "index": str(Path(arguments.index).resolve()) if arguments.index else None,
            "pitch": arguments.pitch,
            "f0": arguments.f0,
            "device": device,
        }
    )
    return 0


def convert_batch(arguments: argparse.Namespace) -> int:
    manifest_path = Path(arguments.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("protocol") != "voxweave-rvc-batch" or manifest.get("version") != 1:
        raise ValueError("unsupported RVC batch manifest")
    items = manifest.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("RVC batch manifest must contain items")
    results, device = _convert_items(arguments, items)
    emit(
        {
            "ok": True,
            "command": "convert-batch",
            "manifest": str(manifest_path),
            "model": str(Path(arguments.model).resolve()),
            "index": str(Path(arguments.index).resolve()) if arguments.index else None,
            "pitch": arguments.pitch,
            "f0": arguments.f0,
            "device": device,
            "results": results,
        }
    )
    return 0


def _add_conversion_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", required=True)
    parser.add_argument("--index")
    parser.add_argument("--pitch", type=int, default=0)
    parser.add_argument("--f0", choices=("rmvpe", "fcpe", "pm"), default="rmvpe")
    parser.add_argument("--index-rate", type=float, default=0.72)
    parser.add_argument("--rms-mix-rate", type=float, default=0.25)
    parser.add_argument("--protect", type=float, default=0.33)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--overwrite", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Pinned official RVC adapter for VoxWeave")
    parser.add_argument("--rvc-root", required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.set_defaults(handler=doctor)
    conversion = commands.add_parser("convert")
    conversion.add_argument("--input", required=True)
    conversion.add_argument("--output", required=True)
    _add_conversion_arguments(conversion)
    conversion.set_defaults(handler=convert)
    batch = commands.add_parser("convert-batch")
    batch.add_argument("--manifest", required=True)
    _add_conversion_arguments(batch)
    batch.set_defaults(handler=convert_batch)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        return int(arguments.handler(arguments))
    except Exception as error:  # noqa: BLE001 - isolated engine boundary
        emit({"ok": False, "error_type": type(error).__name__, "error": str(error)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
