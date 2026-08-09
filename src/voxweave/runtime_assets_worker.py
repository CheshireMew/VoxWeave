from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

ASSET_REVISION = "e6d0c1a17da07c33557852f9dfa2bd44cc75737d"
WESPEAKER_REVISION = "f0c48c298fd835726c27956a5d617bad7115627e"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download pinned RVC runtime assets")
    parser.add_argument("--rvc-root", required=True)
    parser.add_argument("--with-separation", action="store_true")
    parser.add_argument("--speaker-root")
    arguments = parser.parse_args()
    assets = Path(arguments.rvc_root).resolve() / "assets"
    patterns = ["hubert_base/*"]
    if arguments.with_separation:
        patterns.extend(
            [
                "pymss_weights/model_bs_roformer_ep_368_sdr_12.9628.ckpt",
                "pymss_weights/model_bs_roformer_ep_368_sdr_12.9628.yaml",
            ]
        )
    snapshot_download(
        repo_id="lj1995/VoiceConversionWebUI",
        revision=ASSET_REVISION,
        allow_patterns=patterns,
        local_dir=assets,
    )
    hf_hub_download(
        repo_id="lj1995/VoiceConversionWebUI",
        filename="rmvpe.pt",
        revision=ASSET_REVISION,
        local_dir=assets / "rmvpe",
    )
    if arguments.speaker_root:
        hf_hub_download(
            repo_id="Wespeaker/wespeaker-resnet34-LM",
            filename="voxceleb_resnet34_LM.onnx",
            revision=WESPEAKER_REVISION,
            local_dir=Path(arguments.speaker_root).resolve(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
