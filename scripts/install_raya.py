"""Download the pinned official CPU model; never download models while serving requests."""

import hashlib
import json
from pathlib import Path

from huggingface_hub import snapshot_download

MODEL = "TextCortex/raya"
REVISION = "48c8658436c268163f31720dd6c40165894e4093"
SHA256 = "521c19623384a00a539d47bf79f1f114ef8dc7775ede9ece76fcf61dabb653ac"


def main():
    root = Path(__file__).resolve().parents[1]
    folder = root / "models/raya"
    snapshot_download(
        MODEL,
        revision=REVISION,
        local_dir=folder,
        cache_dir=root / "models/.cache",
        allow_patterns=["rl_agent_config.json", "tokenizer/*", "encoder/*", "onnx/raya.onnx"],
    )
    with (folder / "onnx/raya.onnx").open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != SHA256:
            raise RuntimeError("Raya model checksum mismatch")
    (folder / "manifest.json").write_text(
        json.dumps({"model": MODEL, "revision": REVISION, "sha256": SHA256}) + "\n"
    )
    print("Official Raya CPU model installed; pinned revision and SHA256 verified.")


if __name__ == "__main__":
    main()
