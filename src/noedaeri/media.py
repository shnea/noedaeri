import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path


class MediaError(Exception):
    pass


class JobCancelled(Exception):
    pass


def run_process(args: list[str], timeout: float, alive: Callable[[], bool], capture=False):
    # No inherited credentials, shell, network protocols, or unbounded stderr buffers.
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C", "AV_LOG_FORCE_NOCOLOR": "1"}
    with subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    ) as process:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if not alive():
                    raise JobCancelled()
                if time.monotonic() >= deadline:
                    raise MediaError("processing_timeout")
                try:
                    output, _ = process.communicate(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if process.returncode:
                raise MediaError("invalid_media_or_conversion_failed")
            return output
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def thumbnail(source: Path, output: Path, seconds: float, timeout: int, alive: Callable[[], bool]):
    # Force container demuxers: playlists and nested external references are not accepted.
    common = [
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-format_whitelist",
        "mov,matroska,webm",
        "-threads",
        "1",
    ]
    raw = run_process(
        [
            "ffprobe",
            *common,
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(source),
        ],
        min(timeout, 15),
        alive,
        capture=True,
    )
    try:
        data = json.loads(raw)
        stream = data["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
        duration = float(data["format"]["duration"])
        if not (
            0 < width <= 4096
            and 0 < height <= 4096
            and width * height <= 8_500_000
            and 0 < duration <= 3600
            and 0 <= seconds < duration
        ):
            raise ValueError()
    except (KeyError, ValueError, IndexError, TypeError) as error:
        raise MediaError("unsupported_media") from error
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    run_process(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            *common,
            "-ss",
            str(seconds),
            "-i",
            str(source),
            "-map",
            "0:v:0",
            "-an",
            "-sn",
            "-dn",
            "-frames:v",
            "1",
            "-vf",
            "scale=w='min(480,iw)':h='min(320,ih)':force_original_aspect_ratio=decrease",
            "-threads",
            "1",
            "-q:v",
            "3",
            "-fs",
            "2097152",
            str(output),
        ],
        timeout,
        alive,
    )
    if not output.is_file() or output.stat().st_size < 4:
        raise MediaError("result_missing")
