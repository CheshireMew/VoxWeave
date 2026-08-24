from __future__ import annotations

import argparse
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

try:
    from .runtime_contract import runtime_contract
except ImportError:
    from runtime_contract import runtime_contract  # type: ignore[no-redef]


def main() -> int:
    parser = argparse.ArgumentParser(description="Download pinned RVC runtime assets")
    parser.add_argument("--rvc-root", required=True)
    parser.add_argument("--with-separation", action="store_true")
    parser.add_argument("--speaker-root")
    arguments = parser.parse_args()
    contract = runtime_contract()
    runtime_assets = contract.runtime_assets
    separation = contract.source_separation
    speaker = contract.speaker_embedding
    assets = Path(arguments.rvc_root).resolve() / "assets"
    patterns = list(runtime_assets.base_patterns)
    if arguments.with_separation:
        patterns.extend(separation.weight_files)
    snapshot_download(
        repo_id=runtime_assets.repo_id,
        revision=runtime_assets.revision,
        allow_patterns=patterns,
        local_dir=assets,
    )
    hf_hub_download(
        repo_id=runtime_assets.repo_id,
        filename=runtime_assets.rmvpe_filename,
        revision=runtime_assets.revision,
        local_dir=assets / runtime_assets.rmvpe_directory,
    )
    if arguments.speaker_root:
        hf_hub_download(
            repo_id=speaker.repo_id,
            filename=speaker.filename,
            revision=speaker.revision,
            local_dir=Path(arguments.speaker_root).resolve(),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
