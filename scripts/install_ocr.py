"""Build the native Vision helper; no external model or daemon is installed."""

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def install():
    source = ROOT / "scripts/ocr_inference.swift"
    target = ROOT / "ocr_runtime/bin"
    target.mkdir(parents=True, exist_ok=True)
    cache = ROOT / "tmp/ocr-check/cache"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target) as folder:
        binary = Path(folder) / "vision-ocr"
        subprocess.run(
            [
                "xcrun",
                "swiftc",
                "-O",
                "-module-cache-path",
                str(cache),
                str(source),
                "-o",
                str(binary),
            ],
            check=True,
        )
        probe = json.loads(subprocess.check_output([str(binary), "--probe"], timeout=30))
        if probe.get("revision") != 3 or not {
            "ko-KR",
            "en-US",
            "ja-JP",
            "zh-Hans",
            "zh-Hant",
        } <= set(probe.get("languages", [])):
            raise ValueError("Required Vision recognition languages unavailable")
        os.replace(binary, target / "vision-ocr")
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "binary_sha256": hashlib.sha256((target / "vision-ocr").read_bytes()).hexdigest(),
                "revision": 3,
            }
        )
    )
    print("Native Apple Vision OCR installed; required languages verified")


if __name__ == "__main__":
    install()
