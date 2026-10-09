"""Bounded offline transcription in the existing native job execution slot."""

import json
import math
import os
import time
import zipfile
from pathlib import Path
from uuid import UUID

from .media import JobCancelled, MediaError, run_process

ROOT = Path(__file__).resolve().parents[2]
RESULT_LIMIT = 4 * 1024**2


def transcribe(settings, storage, job, alive, stage, reserve, *, normalize_timeline=False):
    runtime = ROOT / "stt_runtime/.venv/bin/python"
    models = ROOT / "models/stt"
    if not runtime.is_file() or not all(
        (models / name).is_file() for name in ("model.int8.onnx", "tokens.txt", "silero_vad.onnx")
    ):
        raise MediaError("stt_not_configured")
    deadline = time.monotonic() + settings.stt_timeout

    def remaining():
        value = deadline - time.monotonic()
        if value <= 0:
            raise MediaError("processing_timeout")
        return value

    job_id = UUID(job["id"])
    source = storage.path("uploads", job_id, "input")
    wave = storage.path("jobs", job_id, "audio.wav")
    output = storage.path("results", job_id, "transcript.json")
    wave.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Only local container formats; playlists and nested remote inputs are rejected.
    common = [
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "wav,mp3,flac,ogg,mov,matroska,webm,aac,aiff",
        "-threads",
        "1",
    ]
    stage("stt_probing")
    raw = run_process(
        [
            "ffprobe",
            *common,
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index:format=duration",
            "-of",
            "json",
            str(source),
        ],
        min(15, remaining()),
        alive,
        capture=True,
    )
    try:
        info = json.loads(raw)
        duration = float(info["format"]["duration"])
        if not info["streams"] or not math.isfinite(duration) or duration <= 0:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise MediaError("unsupported_media") from None
    if duration > settings.stt_max_duration:
        raise MediaError("stt_duration_exceeded")
    # Count decoded intermediates as well as JSON, text, and ZIP in the reservation.
    reserve(int((min(duration + 2, settings.stt_max_duration + 1)) * 32000) + 16 * 1024**2)
    stage("stt_normalizing")
    run_process(
        [
            "ffmpeg",
            *common,
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-t",
            str(settings.stt_max_duration + 1),
            "-ac",
            "1",
            "-ar",
            "16000",
            *(["-af", "aresample=async=1:first_pts=0"] if normalize_timeline else []),
            "-c:a",
            "pcm_s16le",
            "-y",
            str(wave),
        ],
        remaining(),
        alive,
    )
    payload = {
        "source": str(wave),
        "output": str(output),
        "models": str(models),
        "max_duration": settings.stt_max_duration,
        "num_threads": settings.stt_threads,
        "language": job["options"]["language"],
        "use_itn": job["options"]["use_itn"],
    }
    request_file = wave.with_name("transcription-request.json")
    fd = os.open(request_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(payload, file)
        stage("stt_transcribing")
        raw = run_process(
            [str(runtime), str(ROOT / "scripts/stt_inference.py"), str(request_file)],
            remaining(),
            alive,
            capture=True,
            failure_code="stt_transcription_failed",
        )
        receipt = json.loads(raw)
        if "error" in receipt:
            allowed = {"stt_duration_exceeded", "stt_result_too_large"}
            raise MediaError(
                receipt["error"] if receipt["error"] in allowed else "stt_transcription_failed"
            )
    finally:
        request_file.unlink(missing_ok=True)
        wave.unlink(missing_ok=True)
    if not output.is_file() or output.stat().st_size > RESULT_LIMIT:
        raise MediaError("stt_result_too_large")
    transcript = json.loads(output.read_text())
    stage("packaging")
    text_file = output.with_suffix(".txt")
    text_file.write_text(transcript["text"] + "\n", encoding="utf-8")
    with zipfile.ZipFile(output.with_suffix(".zip"), "w", zipfile.ZIP_DEFLATED) as archive:
        for path in (output, text_file):
            if not alive():
                raise JobCancelled()
            remaining()
            archive.write(path, path.name)
    return {
        "type": "stt_transcribe",
        "model": "SenseVoiceSmall INT8",
        "provider": "cpu",
        "language": payload["language"],
        "use_itn": payload["use_itn"],
        "duration_seconds": transcript["duration_seconds"],
        "segment_count": len(transcript["segments"]),
        "timing": "vad_segments",
        "files": ["transcript.json", "transcript.txt", "transcript.zip"],
        "elapsed_seconds": round(settings.stt_timeout - remaining(), 3),
        "peak_memory_bytes": receipt["peak_memory_bytes"],
    }
