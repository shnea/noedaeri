import json
import math
import os
import platform
import signal
import subprocess
import time
from collections.abc import Callable
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from .execution import (
    hardware_encoder_lock,
    inherited_compute_lock,
    inherited_encoder_lock,
    inherited_lock,
    native_compute_lock,
)


class MediaError(Exception):
    pass


class JobCancelled(Exception):
    pass


def run_process(
    args: list[str],
    timeout: float,
    alive: Callable[[], bool],
    capture=False,
    failure_code="invalid_media_or_conversion_failed",
    cwd=None,
):
    # No inherited credentials, shell, network protocols, or unbounded stderr buffers.
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C", "AV_LOG_FORCE_NOCOLOR": "1"}
    if args[0] in {"ffmpeg", "ffprobe"}:
        binary = os.environ.get("FFMPEG_BINARY" if args[0] == "ffmpeg" else "FFPROBE_BINARY")
        if binary:
            args = [binary, *args[1:]]
    with subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
        cwd=cwd,
        pass_fds=tuple(
            fd
            for fd in (
                inherited_lock.get(),
                inherited_encoder_lock.get(),
                inherited_compute_lock.get(),
            )
            if fd is not None
        ),
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
                raise MediaError(failure_code)
            return output
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


def subtitle_renderer_available():
    return _subtitle_renderer_available(os.environ.get("FFMPEG_BINARY", "ffmpeg"))


@lru_cache(maxsize=4)
def _subtitle_renderer_available(binary):
    try:
        output = run_process([binary, "-hide_banner", "-filters"], 5, lambda: True, capture=True)
        return any(
            len(row.split()) > 1 and row.split()[1] == b"subtitles" for row in output.splitlines()
        )
    except (OSError, MediaError):
        return False


@contextmanager
def native_compute_slot(root, alive, stage, wait_seconds):
    deadline = time.monotonic() + wait_seconds
    waiting = False
    while True:
        if not alive():
            raise JobCancelled()
        lock = native_compute_lock(root)
        try:
            lock.__enter__()
        except BlockingIOError:
            if not waiting:
                stage("waiting_native_compute")
                waiting = True
            if time.monotonic() >= deadline:
                raise MediaError("processing_timeout") from None
            time.sleep(0.2)
            continue
        try:
            yield
        finally:
            lock.__exit__(None, None, None)
        return


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


@contextmanager
def video_encoding_slot(root, encoder, alive, remaining, stage):
    if encoder == "libx264":
        yield
        return
    waiting = False
    while True:
        if not alive():
            raise JobCancelled()
        remaining()
        # Catch contention only during acquisition, never retry a failed conversion.
        lock = hardware_encoder_lock(root)
        try:
            lock.__enter__()
        except BlockingIOError:
            if not waiting:
                stage("waiting_hardware_encoder")
                waiting = True
            time.sleep(0.2)
            continue
        try:
            yield
        finally:
            lock.__exit__(None, None, None)
        return


def video_package(
    source,
    folder,
    seconds,
    timeout,
    alive,
    stage,
    reserve=lambda size: None,
    *,
    encoder="auto",
    resource_root=None,
    subtitle_mode="none",
    prepare_subtitles=None,
):
    """Produce flat, relative HLS paths suitable for authenticated delivery or export."""
    if encoder not in {"auto", "libx264", "h264_videotoolbox"}:
        raise MediaError("unsupported_video_encoder")
    if subtitle_mode not in {"none", "sidecar", "burned"}:
        raise MediaError("invalid_job_options")
    if subtitle_mode != "none" and prepare_subtitles is None:
        raise MediaError("stt_not_configured")
    if subtitle_mode == "burned" and not subtitle_renderer_available():
        raise MediaError("subtitle_renderer_unavailable")
    source, folder = Path(source).resolve(), Path(folder).resolve()
    try:
        folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    except FileExistsError:
        raise MediaError("worker_failed") from None
    deadline = time.monotonic() + timeout

    def remaining():
        budget = deadline - time.monotonic()
        if budget <= 0:
            raise MediaError("processing_timeout")
        return budget

    stage("probing")
    raw = run_process(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            "mov,matroska,webm",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,sample_aspect_ratio:stream_side_data=rotation:format=duration",
            "-of",
            "json",
            str(source),
        ],
        min(15, remaining()),
        alive,
        capture=True,
    )
    try:
        probed = json.loads(raw)
        duration = float(probed["format"]["duration"])
        stream = probed["streams"][0]
        width, height = int(stream["width"]), int(stream["height"])
    except (ValueError, KeyError, IndexError, TypeError):
        raise MediaError("unsupported_media") from None
    if not math.isfinite(duration) or not 0 < duration <= 3600:
        raise MediaError("unsupported_media")
    if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 8_500_000):
        raise MediaError("unsupported_media")
    sar = stream.get("sample_aspect_ratio", "1:1")
    if sar not in {"N/A", "0:1"}:
        numerator, denominator = (int(value) for value in sar.split(":"))
        if (
            denominator <= 0
            or not math.isfinite(numerator / denominator)
            or not 0.1 <= numerator / denominator <= 10
        ):
            raise MediaError("unsupported_media")
        width = round(width * numerator / denominator)
    # Portrait videos use their short edge for the quality label too.
    rotation = next(
        (int(item["rotation"]) for item in stream.get("side_data_list", []) if "rotation" in item),
        0,
    )
    if abs(rotation) % 180 == 90:
        width, height = height, width
    short = min(width, height)
    levels = [n for n in (480, 720, 1080) if n <= short] or [short - short % 2]
    rates = [1200 if n <= 480 else 2800 if n <= 720 else 5000 for n in levels]
    # Conservative MPEG-TS overhead plus a second copy in the uncompressed ZIP.
    estimate = (
        math.ceil(duration * sum((rate + 128) * 1000 for rate in rates) / 8 * 2 * 1.3)
        + 4 * 1024 * 1024
    )
    stage("reserving")
    reserve(estimate)
    subtitle_result = None
    if subtitle_mode != "none":

        def subtitle_alive():
            remaining()
            return alive()

        subtitle_result = prepare_subtitles(lambda size: reserve(estimate + size), subtitle_alive)
        remaining()
        if not subtitle_alive():
            raise JobCancelled()
    stage("thumbnail")
    thumbnail(source, folder / "thumbnail.jpg", seconds, remaining(), alive)
    master = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-INDEPENDENT-SEGMENTS"]
    variants = []
    selected_encoder = (
        ("h264_videotoolbox" if platform.system() == "Darwin" else "libx264")
        if encoder == "auto"
        else encoder
    )
    hardware_fallback = False

    def encode_variants():
        encoder_options = (
            ["-preset", "veryfast", "-keyint_min", "180", "-sc_threshold", "0"]
            if selected_encoder == "libx264"
            else ["-allow_sw", "0"]
        )
        with video_encoding_slot(
            resource_root or folder.parent, selected_encoder, alive, remaining, stage
        ):
            for level in levels:
                if level < 2:
                    raise MediaError("unsupported_media")
                scale = min(level / short, 4096 / width, 4096 / height)
                w, h = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
                name = f"{level}p.m3u8"
                bitrate = 1200 if level <= 480 else 2800 if level <= 720 else 5000
                stage(f"encoding_{level}p")
                filters = f"scale={w}:{h},setsar=1"
                if subtitle_mode == "burned" and subtitle_result["cue_count"]:
                    # Fixed local filename and style; no caller-controlled filter expression.
                    filters += (
                        ",subtitles=filename=subtitles.srt:"
                        "force_style='FontName=Arial,FontSize=20,Alignment=2,"
                        "Outline=2,Shadow=0,MarginV=16'"
                    )
                run_process(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-y",
                        "-v",
                        "error",
                        "-protocol_whitelist",
                        "file",
                        "-format_whitelist",
                        "mov,matroska,webm",
                        "-threads",
                        "1",
                        *(
                            ["-hwaccel", "videotoolbox"]
                            if selected_encoder == "h264_videotoolbox"
                            else []
                        ),
                        "-i",
                        str(source),
                        "-map",
                        "0:v:0",
                        "-map",
                        "0:a:0?",
                        "-sn",
                        "-dn",
                        "-vf",
                        filters,
                        "-r",
                        "30",
                        "-c:v",
                        selected_encoder,
                        *encoder_options,
                        "-pix_fmt",
                        "yuv420p",
                        "-threads",
                        "2",
                        "-b:v",
                        f"{bitrate}k",
                        "-maxrate",
                        f"{bitrate}k",
                        "-bufsize",
                        f"{bitrate * 2}k",
                        "-g",
                        "180",
                        "-force_key_frames",
                        "expr:gte(t,n_forced*6)",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "128k",
                        "-ac",
                        "2",
                        "-f",
                        "hls",
                        "-hls_time",
                        "6",
                        "-hls_list_size",
                        "0",
                        "-hls_playlist_type",
                        "vod",
                        "-hls_flags",
                        "independent_segments",
                        "-hls_segment_filename",
                        str(folder / f"{level}p-%05d.ts"),
                        str(folder / name),
                    ],
                    remaining(),
                    alive,
                    failure_code=(
                        "hardware_encoding_failed"
                        if selected_encoder == "h264_videotoolbox"
                        else "invalid_media_or_conversion_failed"
                    ),
                    cwd=folder,
                )
                master.extend(
                    [
                        f"#EXT-X-STREAM-INF:BANDWIDTH={(bitrate + 128) * 1100},RESOLUTION={w}x{h}",
                        name,
                    ]
                )
                variants.append(
                    {
                        "label": f"{level}p",
                        "width": w,
                        "height": h,
                        "playlist": name,
                        "video_bitrate": bitrate * 1000,
                        "bandwidth": (bitrate + 128) * 1100,
                        "bytes": sum(p.stat().st_size for p in folder.glob(f"{level}p*")),
                    }
                )

    try:
        encode_variants()
    except MediaError as error:
        if encoder != "auto" or str(error) != "hardware_encoding_failed":
            raise
        # run_process has reaped the hardware child and released its slot. Remove
        # only this attempt's rendition files before one full CPU attempt.
        for level in levels:
            for partial in folder.glob(f"{level}p*"):
                partial.unlink()
        if not alive():
            raise JobCancelled() from error
        remaining()
        selected_encoder = "libx264"
        hardware_fallback = True
        variants.clear()
        del master[3:]
        stage("cpu_fallback")
        encode_variants()
    (folder / "master.m3u8").write_text("\n".join(master) + "\n")
    stage("packaging")
    import zipfile

    files = sorted(p.name for p in folder.iterdir() if p.is_file())
    with zipfile.ZipFile(folder / "video.zip", "w", compression=zipfile.ZIP_STORED) as archive:
        for name in files:
            # Chunking allows cancellation and space checks during the export copy.
            with (folder / name).open("rb") as source_file, archive.open(name, "w") as target:
                while chunk := source_file.read(1024 * 1024):
                    if not alive():
                        raise JobCancelled()
                    remaining()
                    target.write(chunk)
    return {
        "type": "video_package",
        "video_encoder": selected_encoder,
        "hardware_fallback": hardware_fallback,
        "duration_seconds": duration,
        "frame_rate": 30,
        "estimated_output_bytes": estimate,
        "total_bytes": sum(p.stat().st_size for p in folder.iterdir() if p.is_file()),
        "file_sizes": {p.name: p.stat().st_size for p in folder.iterdir() if p.is_file()},
        "master": "master.m3u8",
        "thumbnail": "thumbnail.jpg",
        "download": "video.zip",
        "variants": variants,
        "files": [*files, "video.zip"],
        **(
            {
                "subtitles": {
                    "mode": subtitle_mode,
                    "language": subtitle_result["language"],
                    "timing": subtitle_result["timing"],
                    "cue_count": subtitle_result["cue_count"],
                    "srt": "subtitles.srt",
                    "vtt": "subtitles.vtt",
                    "transcript": "transcript.json",
                }
            }
            if subtitle_result is not None
            else {}
        ),
    }
