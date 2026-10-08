"""Native speech jobs: normalize reference audio, or launch an isolated MLX model."""

import hashlib
import json
from pathlib import Path

from .media import MediaError, run_process
from .voice_storage import voice_storage_online

ROOT = Path(__file__).resolve().parents[2]


def normalize_reference(source, output, alive):
    common = [
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "wav,mp3,flac,ogg,mov,aac",
        "-threads",
        "1",
    ]
    raw = run_process(
        [
            "ffprobe",
            *common,
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=sample_rate:format=duration",
            "-of",
            "json",
            str(source),
        ],
        15,
        alive,
        capture=True,
    )
    try:
        info = json.loads(raw)
        duration = float(info["format"]["duration"])
        if not info["streams"] or not 3 <= duration <= 30:
            raise ValueError()
    except (KeyError, ValueError, TypeError):
        raise MediaError("unsupported_media") from None
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            *common,
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-dn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-c:a",
            "pcm_s16le",
            "-t",
            "30",
            str(output),
        ],
        60,
        alive,
    )
    return {"duration_seconds": duration}


def synthesize(settings, storage, job, voice, alive, stage):
    python = ROOT / "tts_runtime/.venv/bin/python"
    model = ROOT / "models/tts" / ("custom" if voice["kind"] == "preset" else "base")
    if not python.is_file() or not (model / "config.json").is_file():
        raise MediaError("tts_not_configured")
    reference = settings.voice_root / str(voice["id"]) / "reference.wav"
    if voice["kind"] == "clone":
        if not voice_storage_online(settings):
            raise MediaError("voice_storage_unavailable")
        if (
            not reference.is_file()
            or reference.is_symlink()
            or hashlib.sha256(reference.read_bytes()).hexdigest() != voice["sample_sha256"]
        ):
            raise MediaError("voice_sample_missing")
    output = storage.path("results", job["id"], "speech.wav")
    payload = {
        "model": str(model),
        "output": str(output),
        "text": job["input"]["text"],
        "language": job["input"]["language"],
        "kind": voice["kind"],
        "speaker": voice["speaker"],
        "instruct": job["options"]["instruct"],
        "reference": str(reference),
        "reference_text": voice["reference_text"],
        "memory_limit": settings.tts_memory_limit,
    }
    request = storage.path("jobs", job["id"], "speech-request.json")
    request.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    request.write_text(json.dumps(payload))
    request.chmod(0o600)
    stage("tts_loading_and_synthesis")
    try:
        raw = run_process(
            [str(python), str(ROOT / "scripts/tts_inference.py"), str(request)],
            settings.tts_timeout,
            alive,
            capture=True,
            failure_code="tts_generation_failed",
        )
        result = json.loads(raw)
        if "error" in result:
            raise MediaError(result["error"])
        return result
    finally:
        request.unlink(missing_ok=True)
