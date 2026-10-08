"""Download verified official STT assets; never execute or extract archive paths."""

import hashlib
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = {
    "sensevoice.tar.bz2": (
        "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
        "7d1efa2138a65b0b488df37f8b89e3d91a60676e416f515b952358d83dfd347e",
    ),
    "silero_vad.onnx": (
        "silero_vad.onnx",
        "9e2449e1087496d8d4caba907f23e0bd3f78d91fa552479bb9c23ac09cbb1fd6",
    ),
}
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"


def install():
    target = ROOT / "models/stt"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target) as folder:
        stage = Path(folder)
        for name, (asset, digest) in ASSETS.items():
            path = stage / name
            with (
                urllib.request.urlopen(BASE + asset, timeout=60) as response,
                path.open("wb") as out,
            ):
                shutil.copyfileobj(response, out)
            with path.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != digest:
                    raise ValueError("STT asset checksum mismatch")
        wanted = {"model.int8.onnx", "tokens.txt", "test_wavs/ko.wav", "test_wavs/en.wav"}
        found = set()
        with tarfile.open(stage / "sensevoice.tar.bz2") as archive:
            for member in archive:
                relative = member.name.partition("/")[2]
                if relative not in wanted or not member.isfile():
                    continue
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, destination.open("wb") as out:
                    shutil.copyfileobj(source, out)
                found.add(relative)
        if found != wanted:
            raise ValueError("STT archive missing required files")
        shutil.copyfile(stage / "silero_vad.onnx", target / "silero_vad.onnx")
    print("Verified SenseVoice INT8 and Silero VAD installed")


if __name__ == "__main__":
    install()
