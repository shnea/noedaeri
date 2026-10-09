import json
import os
from unittest.mock import patch

import pytest
from test_stt import setup_source, silent_wave

from noedaeri.media import MediaError
from noedaeri.subtitles import create_subtitles, subtitle_cues, timestamp


def test_cues_timing_wrapping_and_invalid():
    data = {
        "duration_seconds": 100,
        "segments": [
            {"start": 1, "end": 31, "text": "가나다라" * 100},
            {"start": 32, "end": 33, "text": "<script> --> &"},
        ],
    }
    cues = subtitle_cues(data)
    assert len(cues) > 2 and cues[0]["start"] == 1 and cues[-1]["end"] == 33
    assert all(len(line) <= 42 for cue in cues for line in cue["text"].splitlines())
    assert all(cue["end"] - cue["start"] <= 6.1 for cue in cues)
    assert timestamp(3661.123, ",") == "01:01:01,123"
    assert timestamp(59.9999, ".") == "00:01:00.000"
    for end in (float("nan"), 101, -1):
        data["segments"][0]["end"] = end
        with pytest.raises(MediaError):
            subtitle_cues(data)


def test_subtitle_export_escaping_and_empty(tmp_path):
    settings, storage, job = setup_source(tmp_path, silent_wave())
    folder = storage.path("results", job["id"], "transcript.json").parent
    folder.mkdir()

    def transcribe(*args):
        (folder / "transcript.json").write_text(
            json.dumps(
                {
                    "duration_seconds": 1,
                    "segments": [{"start": 0, "end": 1, "text": "<b>hello</b> --> &"}],
                }
            )
        )
        (folder / "transcript.txt").write_text("fixture")
        (folder / "transcript.zip").write_bytes(b"fixture")
        return {"duration_seconds": 1, "model": "fixture"}

    with patch("noedaeri.subtitles.transcribe", transcribe):
        result = create_subtitles(
            settings, storage, job, lambda: True, lambda _: None, lambda _: None
        )
    assert result["type"] == "video_subtitles" and result["cue_count"] == 1
    assert "00:00:00,000 --> 00:00:01,000" in (folder / "subtitles.srt").read_text()
    assert "&lt;b&gt;hello&lt;/b&gt; --&gt; &amp;" in (folder / "subtitles.vtt").read_text()
    assert not (folder / "transcript.zip").exists()


@pytest.mark.skipif(os.environ.get("RUN_STT_SMOKE") != "1", reason="Native STT opt-in")
def test_native_subtitles_silence(tmp_path):
    settings, storage, job = setup_source(tmp_path, silent_wave())
    result = create_subtitles(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
    assert result["cue_count"] == 0
    assert storage.path("results", job["id"], "subtitles.vtt").read_text() == "WEBVTT\n\n"


@pytest.mark.skipif(os.environ.get("RUN_STT_SMOKE") != "1", reason="Native STT opt-in")
def test_native_subtitles_spoken_video(tmp_path):
    import subprocess
    import zipfile

    audio, video = tmp_path / "fixture.aiff", tmp_path / "fixture.mp4"
    subprocess.run(
        [
            "say",
            "-v",
            "Samantha",
            "-o",
            str(audio),
            "Welcome to the library. Please return your books tomorrow.",
        ],
        check=True,
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=320x180:r=10",
            "-i",
            str(audio),
            "-shortest",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-y",
            str(video),
        ],
        check=True,
    )
    settings, storage, job = setup_source(tmp_path / "storage", video.read_bytes())
    job["options"]["language"] = "en"
    result = create_subtitles(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
    assert result["cue_count"] > 0
    folder = storage.path("results", job["id"], "subtitles.srt").parent
    assert " --> " in (folder / "subtitles.srt").read_text()
    with zipfile.ZipFile(folder / "subtitles.zip") as archive:
        assert set(archive.namelist()) == {
            "transcript.json",
            "transcript.txt",
            "subtitles.srt",
            "subtitles.vtt",
        }
    assert not storage.path("jobs", job["id"], "audio.wav").exists()
