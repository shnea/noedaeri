import json
import os
import subprocess
import zipfile
from dataclasses import replace
from uuid import uuid4

import pytest
from conftest import login

from noedaeri import media
from noedaeri.worker import execute_job


def request_data(subtitles=None):
    return {
        "kind": "video.package",
        "title": "영상 자막 테스트",
        "idempotency_key": str(uuid4()),
        "input": {"type": "upload"},
        "options": {"seconds": 0, **({"subtitles": subtitles} if subtitles else {})},
    }


def test_admission_capabilities_and_legacy_idempotency(app, monkeypatch):
    client, _ = login(app)
    payload = request_data()
    first = client.post("/api/jobs", json=payload)
    assert first.status_code == 201
    job_id = first.json()["id"]
    # Requests created before the optional field existed still deduplicate.
    with app.state.db.connect() as conn:
        conn.execute("UPDATE jobs SET options=%s WHERE id=%s", ('{"seconds":0}', job_id))
    repeated = client.post("/api/jobs", json=payload)
    assert repeated.status_code == 201 and repeated.json()["id"] == job_id
    changed = {**payload, "options": {"subtitles": {"mode": "sidecar"}}}
    assert client.post("/api/jobs", json=changed).status_code == 503
    app.state.settings = replace(app.state.settings, stt_enabled=True)
    assert client.post("/api/jobs", json=changed).status_code == 409
    monkeypatch.setattr(media, "subtitle_renderer_available", lambda: False)
    burned = request_data({"mode": "burned"})
    assert client.post("/api/jobs", json=burned).json()["detail"] == "subtitle_renderer_unavailable"
    sidecar = client.post("/api/jobs", json=request_data({"mode": "sidecar", "language": "ko"}))
    assert sidecar.status_code == 201
    assert sidecar.json()["options"]["subtitles"] == {
        "mode": "sidecar",
        "language": "ko",
        "use_itn": True,
    }
    assert any(
        row["id"] == sidecar.json()["id"] and row["service"] == "ffmpeg"
        for row in client.get("/api/tasks").json()
    )
    invalid = request_data({"mode": "burned", "filter": "movie=remote"})
    assert client.post("/api/jobs", json=invalid).status_code == 422


def test_finish_requires_subtitle_files_and_matching_mode(app):
    client, _ = login(app)
    app.state.settings = replace(app.state.settings, stt_enabled=True)
    created = client.post("/api/jobs", json=request_data({"mode": "sidecar"})).json()
    assert (
        client.put(
            f"/api/jobs/{created['id']}/input",
            content=b"fixture",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 200
    )
    job = app.state.queue.claim(uuid4(), ["video.package"])
    folder = app.state.storage.path("results", job["id"], "video.zip").parent
    folder.mkdir(parents=True)
    names = ["master.m3u8", "thumbnail.jpg", "video.zip"]
    for name in names:
        (folder / name).write_bytes(b"fixture")
    result = {"type": "video_package", "files": names}
    headers = {"Authorization": "Bearer " + app.state.settings.worker_key}

    def finish():
        return client.post(
            f"/internal/jobs/{job['id']}/finish",
            headers=headers,
            json={"token": str(job["lease_token"]), "status": "succeeded", "result": result},
        )

    assert finish().status_code == 409
    names.extend(["transcript.json", "transcript.txt", "subtitles.srt", "subtitles.vtt"])
    for name in names[3:]:
        (folder / name).write_bytes(b"fixture")
    result["subtitles"] = {
        "mode": "burned",
        "language": "auto",
        "timing": "vad_proportional",
        "cue_count": 1,
        "srt": "subtitles.srt",
        "vtt": "subtitles.vtt",
        "transcript": "transcript.json",
    }
    assert finish().status_code == 409
    result["subtitles"]["mode"] = "sidecar"
    assert finish().status_code == 200
    response = client.get(f"/api/jobs/{created['id']}/files/subtitles.vtt")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/vtt")


@pytest.mark.parametrize("mode,fallback", [("sidecar", False), ("burned", False), ("burned", True)])
def test_native_package_subtitles_all_resolutions(tmp_path, monkeypatch, mode, fallback):
    if mode == "burned" and not media.subtitle_renderer_available():
        pytest.skip("libass FFmpeg required")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=960x720:d=2",
            "-c:v",
            "libx264",
            "-y",
            str(source),
        ],
        check=True,
    )
    folder = tmp_path / "quoted ' folder"
    calls, reservations, stages = [], [], []
    if fallback:
        original = media.run_process
        monkeypatch.setattr(media.platform, "system", lambda: "Darwin")

        def failed_hardware(args, *other, **kwargs):
            if "-c:v" in args and args[args.index("-c:v") + 1] == "h264_videotoolbox":
                raise media.MediaError("hardware_encoding_failed")
            return original(args, *other, **kwargs)

        monkeypatch.setattr(media, "run_process", failed_hardware)

    def prepare(reserve, alive):
        calls.append("transcribe_once")
        assert alive()
        reserve(32 * 1024**2)
        (folder / "subtitles.srt").write_text(
            "1\n00:00:00,000 --> 00:00:01,900\n안녕하세요 Hello\n", encoding="utf-8"
        )
        (folder / "subtitles.vtt").write_text(
            "WEBVTT\n\n00:00.000 --> 00:01.900\n안녕하세요 Hello\n", encoding="utf-8"
        )
        (folder / "transcript.json").write_text("{}")
        (folder / "transcript.txt").write_text("안녕하세요 Hello")
        return {"cue_count": 1, "language": "ko", "timing": "vad_proportional"}

    result = media.video_package(
        source,
        folder,
        0,
        30,
        lambda: True,
        stages.append,
        reservations.append,
        encoder="auto" if fallback else "libx264",
        subtitle_mode=mode,
        prepare_subtitles=prepare,
    )
    assert calls == ["transcribe_once"]
    assert reservations[-1] > reservations[0] and "packaging" in stages
    assert [row["label"] for row in result["variants"]] == ["480p", "720p"]
    assert result["subtitles"]["mode"] == mode
    assert result["hardware_fallback"] is fallback
    with zipfile.ZipFile(folder / "video.zip") as archive:
        assert set(archive.namelist()) == set(result["files"]) - {"video.zip"}
        assert "subtitles.zip" not in archive.namelist()
        assert {"subtitles.srt", "subtitles.vtt", "transcript.json", "transcript.txt"} <= set(
            archive.namelist()
        )
    for variant in result["variants"]:
        segment = next(folder.glob(variant["label"] + "-*.ts"))
        pixels = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(segment),
                "-ss",
                "0.5",
                "-frames:v",
                "1",
                "-vf",
                "scale=320:240,format=gray",
                "-f",
                "rawvideo",
                "-",
            ]
        )
        assert len(pixels) == 320 * 240
        bright = sum(value > 150 for value in pixels[320 * 160 :])
        assert bright > 20 if mode == "burned" else bright == 0


def test_pipeline_cancellation_does_not_start_encoding(tmp_path, monkeypatch):
    monkeypatch.setattr(
        media,
        "run_process",
        lambda *args, **kwargs: json.dumps(
            {
                "format": {"duration": 2},
                "streams": [{"width": 640, "height": 480}],
            }
        ),
    )
    stages = []

    def cancelled(*args):
        raise media.JobCancelled()

    with pytest.raises(media.JobCancelled):
        media.video_package(
            tmp_path / "input",
            tmp_path / "out",
            0,
            30,
            lambda: True,
            stages.append,
            subtitle_mode="sidecar",
            prepare_subtitles=cancelled,
        )
    assert not any(stage.startswith("encoding_") for stage in stages)


def test_subtitle_preparation_shares_whole_video_deadline(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(media.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        media,
        "run_process",
        lambda *args, **kwargs: json.dumps(
            {
                "format": {"duration": 2},
                "streams": [{"width": 640, "height": 480}],
            }
        ),
    )

    def too_slow(reserve, alive):
        clock[0] = 31
        alive()

    with pytest.raises(media.MediaError, match="processing_timeout"):
        media.video_package(
            tmp_path / "input",
            tmp_path / "out",
            0,
            30,
            lambda: True,
            lambda _: None,
            subtitle_mode="sidecar",
            prepare_subtitles=too_slow,
        )


def test_worker_uses_one_pipeline_and_separate_subtitle_options(app, monkeypatch):
    calls = []
    subtitle_options = {"mode": "sidecar", "language": "ko", "use_itn": False}
    job = {
        "id": str(uuid4()),
        "kind": "video.package",
        "options": {"seconds": 0, "subtitles": subtitle_options},
    }

    def captions(settings, storage, task, alive, stage, reserve, *, archive, align_video):
        assert task["options"] == subtitle_options and not archive and align_video
        calls.append(task["id"])
        return {"cue_count": 0, "language": "ko", "timing": "vad_proportional"}

    def package(*args, **kwargs):
        assert kwargs["subtitle_mode"] == "sidecar"
        return kwargs["prepare_subtitles"](lambda size: None, lambda: True)

    monkeypatch.setattr("noedaeri.worker.create_subtitles", captions)
    monkeypatch.setattr("noedaeri.worker.video_package", package)
    settings = replace(app.state.settings, stt_enabled=True)
    execute_job(
        settings,
        app.state.storage,
        job,
        None,
        None,
        app.state.storage.root / "input",
        app.state.storage.path("results", job["id"], "thumbnail.jpg"),
        lambda: True,
        lambda _: None,
        lambda _: None,
    )
    assert calls == [job["id"]]


@pytest.mark.skipif(os.environ.get("RUN_STT_SMOKE") != "1", reason="Native STT opt-in")
def test_real_speech_to_burned_hls(tmp_path):
    from test_stt import setup_source

    if not media.subtitle_renderer_available():
        pytest.skip("libass FFmpeg required")
    audio, video = tmp_path / "speech.aiff", tmp_path / "speech.mp4"
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
            "color=s=640x480:r=10:d=10",
            "-itsoffset",
            "2",
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
    job.update(
        kind="video.package",
        options={"seconds": 0, "subtitles": {"mode": "burned", "language": "en", "use_itn": True}},
    )
    stages = []
    result = execute_job(
        replace(settings, stt_enabled=True, video_encoder="libx264"),
        storage,
        job,
        None,
        None,
        storage.path("uploads", job["id"], "input"),
        storage.path("results", job["id"], "thumbnail.jpg"),
        lambda: True,
        stages.append,
        lambda _: None,
    )
    assert result["subtitles"]["cue_count"] > 0
    transcript = json.loads(storage.path("results", job["id"], "transcript.json").read_text())
    # Preserve delayed audio on the video timeline rather than shifting speech to zero.
    assert transcript["segments"][0]["start"] >= 1.8
    assert stages.count("stt_transcribing") == 1
    assert result["subtitles"]["mode"] == "burned" and result["type"] == "video_package"
    assert not storage.path("jobs", job["id"], "audio.wav").exists()
    assert not storage.path("results", job["id"], "subtitles.zip").exists()
