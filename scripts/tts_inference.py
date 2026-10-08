"""Isolated, credential-free MLX TTS process. stdout is a small JSON receipt."""

import json
import os
import re
import sys
import time
from contextlib import redirect_stdout
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def main():
    import mlx.core as mx
    import numpy as np
    import soundfile as sf
    from mlx_audio.tts.utils import load_model

    payload = json.loads(Path(sys.argv[1]).read_text())
    start = time.monotonic()
    mx.set_memory_limit(payload["memory_limit"])
    mx.set_cache_limit(128 * 1024**2)
    model = load_model(payload["model"])
    mx.clear_cache()
    text = payload["text"]
    # Bound individual autoregressive segments; preserve every input character.
    sentences = re.split(r"(?<=[.!?。！？])\s+|\n+", text)
    segments = [
        s[i : i + 180] for s in sentences for i in range(0, len(s), 180) if s[i : i + 180].strip()
    ]
    total, sample_rate = 0, 24000
    output = Path(payload["output"])
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with sf.SoundFile(output, "w", samplerate=sample_rate, channels=1, subtype="PCM_16") as file:
        for segment in segments:
            args = dict(text=segment, lang_code=payload["language"], max_tokens=1800, verbose=False)
            if payload["kind"] == "preset":
                args.update(voice=payload["speaker"], instruct=payload["instruct"] or None)
            else:
                args.update(ref_audio=payload["reference"], ref_text=payload["reference_text"])
            for result in model.generate(**args):
                audio = np.asarray(result.audio).reshape(-1)
                if result.sample_rate != sample_rate or not np.isfinite(audio).all():
                    raise ValueError("invalid_audio")
                total += len(audio)
                if total > sample_rate * 600:
                    raise ValueError("output_duration_exceeded")
                file.write(audio)
    if not total:
        raise ValueError("empty_audio")
    return {
        "duration_seconds": round(total / sample_rate, 3),
        "sample_rate": sample_rate,
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "peak_memory_bytes": int(mx.get_peak_memory()),
    }


if __name__ == "__main__":
    try:
        with redirect_stdout(sys.stderr):
            receipt = main()
        print(json.dumps(receipt))
    except Exception as error:
        code = (
            "tts_memory_unavailable" if "memory" in str(error).lower() else "tts_generation_failed"
        )
        print(json.dumps({"error": code}))
