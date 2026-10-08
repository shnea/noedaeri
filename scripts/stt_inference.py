"""Credential-free, streaming-file SenseVoice + Silero VAD subprocess."""

import contextlib
import json
import resource
import sys
from pathlib import Path

import numpy as np
import sherpa_onnx
import soundfile as sf

RATE = 16000
RESULT_LIMIT = 4 * 1024**2


def infer(payload):
    model_root = Path(payload["models"])
    with sf.SoundFile(payload["source"]) as audio:
        duration = audio.frames / audio.samplerate
        if audio.samplerate != RATE or audio.channels != 1 or duration <= 0:
            raise ValueError("unsupported_media")
        if duration > payload["max_duration"]:
            raise ValueError("stt_duration_exceeded")
        recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model_root / "model.int8.onnx"),
            tokens=str(model_root / "tokens.txt"),
            num_threads=payload["num_threads"],
            provider="cpu",
            language="" if payload["language"] == "auto" else payload["language"],
            use_itn=payload["use_itn"],
        )
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(model_root / "silero_vad.onnx")
        config.silero_vad.min_silence_duration = 0.5
        config.silero_vad.max_speech_duration = 30
        config.sample_rate = RATE
        vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
        segments, size = [], 0

        def consume():
            nonlocal size
            while not vad.empty():
                segment = vad.front
                samples = segment.samples
                start = segment.start / RATE
                end = min(duration, start + len(samples) / RATE)
                vad.pop()
                stream = recognizer.create_stream()
                stream.accept_waveform(RATE, samples)
                recognizer.decode_stream(stream)
                text = stream.result.text.strip()
                if text and start < end:
                    row = {"start": round(start, 3), "end": round(end, 3), "text": text}
                    size += len(json.dumps(row, ensure_ascii=False).encode()) + len(text.encode())
                    if size > RESULT_LIMIT - 16384 or len(segments) >= 20000:
                        raise ValueError("stt_result_too_large")
                    segments.append(row)

        window = config.silero_vad.window_size
        while audio.tell() < audio.frames:
            block = audio.read(window, dtype="float32")
            if len(block) < window:
                block = np.pad(block, (0, window - len(block)))
            vad.accept_waveform(block)
            consume()
        vad.flush()
        consume()
    transcript = {
        "version": 1,
        "model": "SenseVoiceSmall INT8",
        "language": payload["language"],
        "use_itn": payload["use_itn"],
        "timing": "vad_segments",
        "duration_seconds": round(duration, 3),
        "sample_rate": RATE,
        "text": "\n".join(row["text"] for row in segments),
        "segments": segments,
    }
    Path(payload["output"]).write_text(json.dumps(transcript, ensure_ascii=False), encoding="utf-8")
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {"peak_memory_bytes": peak if sys.platform == "darwin" else peak * 1024}


if __name__ == "__main__":
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = infer(json.loads(Path(sys.argv[1]).read_text()))
    except ValueError as error:
        code = str(error)
        result = {
            "error": code
            if code in {"stt_duration_exceeded", "stt_result_too_large"}
            else "stt_transcription_failed"
        }
    except Exception:
        result = {"error": "stt_transcription_failed"}
    print(json.dumps(result))
