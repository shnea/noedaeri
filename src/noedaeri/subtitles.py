"""Bounded SRT/VTT export from coarse VAD transcription timings."""

import html
import json
import math
import time
import zipfile
from uuid import UUID

from .media import JobCancelled, MediaError
from .stt import RESULT_LIMIT, transcribe


def subtitle_cues(transcript):
    duration = transcript["duration_seconds"]
    if not math.isfinite(duration) or duration <= 0:
        raise MediaError("unsupported_media")
    cues, previous = [], 0
    for row in transcript["segments"]:
        start, end = row["start"], row["end"]
        if not (
            math.isfinite(start) and math.isfinite(end) and previous <= start < end <= duration
        ):
            raise MediaError("unsupported_media")
        previous = end
        text = " ".join(row["text"].replace("\x00", "").split())
        if not text:
            continue
        # These are proportional subdivisions, not forced word alignment.
        pieces = min(len(text), max(math.ceil(len(text) / 84), math.ceil((end - start) / 6)))
        for index in range(pieces):
            chunk = text[index * len(text) // pieces : (index + 1) * len(text) // pieces].strip()
            if not chunk:
                continue
            chunk = "\n".join(chunk[i : i + 42] for i in range(0, len(chunk), 42))
            cue_start = round(start + (end - start) * index / pieces, 3)
            cue_end = round(min(start + (end - start) * (index + 1) / pieces, cue_start + 6), 3)
            if cue_end <= cue_start:
                continue
            cues.append({"start": cue_start, "end": cue_end, "text": chunk})
            if len(cues) > 100000:
                raise MediaError("stt_result_too_large")
    return cues


def timestamp(value, separator):
    milliseconds = round(value * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"


def create_subtitles(settings, storage, job, alive, stage, reserve):
    deadline = time.monotonic() + settings.stt_timeout
    result = transcribe(
        settings, storage, job, alive, stage, lambda size: reserve(size + 24 * 1024**2)
    )
    folder = storage.path("results", UUID(job["id"]), "transcript.json").parent
    transcript = json.loads((folder / "transcript.json").read_text())
    stage("subtitle_exporting")
    cues = subtitle_cues(transcript)
    srt, vtt = [], ["WEBVTT\n"]
    for index, cue in enumerate(cues, 1):
        if not alive():
            raise JobCancelled()
        if time.monotonic() >= deadline:
            raise MediaError("processing_timeout")
        # Escape subtitle markup after splitting, including '-->' and HTML-like input.
        text = html.escape(cue["text"], quote=False)
        srt.append(
            f"{index}\n{timestamp(cue['start'], ',')} --> {timestamp(cue['end'], ',')}\n{text}\n"
        )
        vtt.append(f"{timestamp(cue['start'], '.')} --> {timestamp(cue['end'], '.')}\n{text}\n")
    for name, parts in (("subtitles.srt", srt), ("subtitles.vtt", vtt)):
        content = "\n".join(parts) + "\n"
        if len(content.encode()) > RESULT_LIMIT:
            raise MediaError("stt_result_too_large")
        (folder / name).write_text(content, encoding="utf-8")
    names = ["transcript.json", "transcript.txt", "subtitles.srt", "subtitles.vtt"]
    stage("packaging")
    with zipfile.ZipFile(folder / "subtitles.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            if not alive():
                raise JobCancelled()
            if time.monotonic() >= deadline:
                raise MediaError("processing_timeout")
            archive.write(folder / name, name)
    (folder / "transcript.zip").unlink(missing_ok=True)
    return {
        **result,
        "type": "video_subtitles",
        "timing": "vad_proportional",
        "cue_count": len(cues),
        "files": [*names, "subtitles.zip"],
        "elapsed_seconds": round(settings.stt_timeout - (deadline - time.monotonic()), 3),
    }
