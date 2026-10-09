import io
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import uvicorn
from conftest import login
from test_jobs import new_job


@pytest.mark.parametrize(
    "kind",
    [
        "video.thumbnail",
        "video.package",
        "image.package",
        "platform",
        pytest.param(
            "ocr.recognize",
            marks=pytest.mark.skipif(
                os.environ.get("RUN_OCR_SMOKE") != "1",
                reason="Requires the native Vision OCR helper",
            ),
        ),
        pytest.param(
            "stt.transcribe",
            marks=pytest.mark.skipif(
                os.environ.get("RUN_STT_SMOKE") != "1",
                reason="Requires the pinned native STT runtime and models",
            ),
        ),
        pytest.param(
            "videotoolbox",
            marks=pytest.mark.skipif(
                os.environ.get("NOEDAERI_TEST_VIDEOTOOLBOX") != "1",
                reason="Requires a macOS hardware encoder; enable explicitly",
            ),
        ),
        pytest.param(
            "auto",
            marks=pytest.mark.skipif(
                os.environ.get("NOEDAERI_TEST_VIDEOTOOLBOX") != "1",
                reason="Requires a macOS hardware encoder; enable explicitly",
            ),
        ),
    ],
)
def test_worker_process_calls_api_and_finishes(app, kind):
    encoder = "h264_videotoolbox" if kind == "videotoolbox" else "libx264"
    automatic = kind == "auto"
    if kind in {"videotoolbox", "auto"}:
        kind = "video.package"
    is_platform = kind == "platform"
    if is_platform:
        from test_integration import create, platform

        kind = "video.thumbnail"
        client = platform(app)
        job_id = create(client).json()["id"]
    else:
        if kind == "ocr.recognize":
            app.state.settings = replace(
                app.state.settings, ocr_enabled=True, storage_limit=128 * 1024**2
            )
            app.state.storage.settings = app.state.settings
        if kind == "stt.transcribe":
            app.state.settings = replace(app.state.settings, stt_enabled=True)
        client, _ = login(app)
        if kind == "ocr.recognize":
            from test_ocr import create_ocr

            job_id = create_ocr(client).json()["id"]
        elif kind == "stt.transcribe":
            from test_stt import create_stt

            job_id = create_stt(client).json()["id"]
        else:
            job_id = new_job(client, kind=kind).json()["id"]
    prefix = "/api/v1" if is_platform else "/api"
    source = app.state.storage.root / "sample.mp4"
    if kind == "ocr.recognize":
        from test_ocr import fixture_image

        source.write_bytes(fixture_image())
    elif kind == "stt.transcribe":
        source.write_bytes(Path("models/stt/test_wavs/ko.wav").read_bytes())
    elif kind == "image.package":
        from PIL import Image

        Image.new("RGBA", (1800, 1200), (20, 90, 60, 100)).save(source, format="PNG")
    else:
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=green:s=1280x720:d=7",
                "-c:v",
                "libx264",
                "-y",
                str(source),
            ],
            check=True,
        )
    assert (
        client.put(
            f"{prefix}/jobs/{job_id}/input",
            content=source.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 200
    )
    source.unlink()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    settings = app.state.settings
    env = dict(
        os.environ,
        DATABASE_URL=settings.database_url,
        WORKER_API_KEY=settings.worker_key,
        PUBLIC_ORIGIN="https://testserver",
        WORKER_API_ORIGIN=f"http://127.0.0.1:{port}",
        STORAGE_ROOT=str(settings.storage_root),
        PLATFORM_OIDC_REDIRECT_URI="https://testserver/auth/callback",
        PYTHONPATH=str(Path("src").resolve()),
        FFMPEG_VIDEO_ENCODER="auto" if automatic else encoder,
        OCR_ENABLED="1" if kind == "ocr.recognize" else "0",
        STT_ENABLED="1" if kind == "stt.transcribe" else "0",
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "noedaeri.worker"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            job = client.get(prefix + "/jobs").json()[0]
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded"
        if kind == "video.thumbnail":
            assert job["result"]["type"] == "artifact"
            assert client.get(f"{prefix}/jobs/{job_id}/result").content.startswith(b"\xff\xd8")
            if is_platform:
                app.state.webhooks.collect()
                assert app.state.webhooks.dispatch_one()
                response = client.post(
                    f"{prefix}/jobs/{job_id}/receipt", json={"event_id": job["terminal_event_id"]}
                )
                assert response.status_code == 200
                app.state.storage.cleanup(app.state.db)
                assert client.get(f"{prefix}/jobs/{job_id}/result").status_code == 410
        elif kind == "ocr.recognize":
            result = job["result"]
            assert result["type"] == "ocr_recognize" and result["line_count"] >= 2
            assert result["peak_memory_bytes"] > 0 and job["output_reserved"] == 0
            base = f"/api/jobs/{job_id}/files/"
            data = client.get(base + "text.json").json()
            assert "문자 인식 테스트" in data["text"] and "Hello OCR 2026" in data["text"]
            assert data["coordinate_system"] == "normalized_top_left"
            assert all(0 <= row["confidence"] <= 1 for row in data["lines"])
            assert all(
                0 <= value <= 1 for row in data["lines"] for value in row["bounding_box"].values()
            )
            assert client.get(base + "text.txt").text.strip() == data["text"]
            archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job_id}/result").content))
            assert set(archive.namelist()) == {"text.json", "text.txt"}
            assert client.get(base + "ocr-input.png").status_code == 404
            assert client.get("/api/tasks?service=ocr").json()[0]["id"] == job_id
            assert not list((settings.storage_root / "jobs" / job_id).iterdir())
            app.state.storage.cleanup(app.state.db)
            assert not (settings.storage_root / "uploads" / job_id).exists()
            login(app)
            assert client.get(base + "text.json").status_code == 404
            with app.state.db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET expires_at=now()-interval '1 second' WHERE id=%s",
                    (UUID(job_id),),
                )
            app.state.storage.cleanup(app.state.db)
            assert not (settings.storage_root / "results" / job_id).exists()
        elif kind == "stt.transcribe":
            result = job["result"]
            assert result["type"] == "stt_transcribe"
            assert result["segment_count"] >= 1
            assert result["peak_memory_bytes"] > 0
            assert job["output_reserved"] == 0
            base = f"/api/jobs/{job_id}/files/"
            transcript = client.get(base + "transcript.json").json()
            assert "편할" in transcript["text"]
            assert all(0 <= row["start"] < row["end"] <= 4.608 for row in transcript["segments"])
            assert client.get(base + "transcript.txt").text.strip() == transcript["text"]
            archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job_id}/result").content))
            assert set(archive.namelist()) == {"transcript.json", "transcript.txt"}
            assert client.get(base + "audio.wav").status_code == 404
            tasks = client.get("/api/tasks?service=stt").json()
            assert any(row["id"] == job_id for row in tasks)
            app.state.storage.cleanup(app.state.db)
            assert not (settings.storage_root / "uploads" / job_id).exists()
            login(app)
            assert client.get(base + "transcript.json").status_code == 404
            with app.state.db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET expires_at=now()-interval '1 second' WHERE id=%s",
                    (UUID(job_id),),
                )
            app.state.storage.cleanup(app.state.db)
            assert not (settings.storage_root / "results" / job_id).exists()
        elif kind == "image.package":
            result = job["result"]
            assert result["type"] == "image_package"
            assert result["preview"]["width"] == 1600
            base = f"/api/jobs/{job_id}/files/"
            assert client.get(base + "preview.webp").headers["content-type"] == "image/webp"
            assert client.get(base + "metadata.json").json() == result
            archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job_id}/result").content))
            assert set(archive.namelist()) == {"thumbnail.jpg", "preview.webp", "metadata.json"}
            login(app)
            assert client.get(base + "preview.webp").status_code == 404
            with app.state.db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET expires_at=%s WHERE id=%s",
                    (datetime.now(UTC) - timedelta(seconds=1), UUID(job_id)),
                )
            app.state.storage.cleanup(app.state.db)
            assert not (settings.storage_root / "results" / job_id).exists()
        else:
            result = job["result"]
            assert result["type"] == "video_package"
            assert result["video_encoder"] == ("h264_videotoolbox" if automatic else encoder)
            assert result["hardware_fallback"] is False
            assert result["duration_seconds"] == 7
            assert result["total_bytes"] == sum(result["file_sizes"].values())
            assert result["total_bytes"] <= result["estimated_output_bytes"]
            assert job["output_reserved"] == 0
            assert result["variants"][0]["video_bitrate"] == 1_200_000
            assert result["variants"][0]["bytes"] > 0
            assert [item["label"] for item in result["variants"]] == ["480p", "720p"]
            base = f"/api/jobs/{job_id}/files/"
            master = client.get(base + "master.m3u8")
            assert master.status_code == 200
            assert "1080p" not in master.text
            assert "RESOLUTION=1280x720" in master.text
            for variant in result["variants"]:
                playlist = client.get(base + variant["playlist"])
                assert "#EXT-X-ENDLIST" in playlist.text
                segments = [
                    line for line in playlist.text.splitlines() if line and not line.startswith("#")
                ]
                assert len(segments) == 2
                for name in segments:
                    assert "/" not in name
                    assert client.get(base + name).status_code == 200
            archive = zipfile.ZipFile(io.BytesIO(client.get(f"/api/jobs/{job_id}/result").content))
            assert "master.m3u8" in archive.namelist()
            assert client.get(base + "unlisted.ts").status_code == 404
            login(app)
            assert client.get(base + "master.m3u8").status_code == 404
            with app.state.db.connect() as conn:
                conn.execute(
                    "UPDATE jobs SET expires_at=%s WHERE id=%s",
                    (datetime.now(UTC) - timedelta(seconds=1), UUID(job_id)),
                )
            app.state.storage.cleanup(app.state.db)
            assert not (settings.storage_root / "results" / job_id).exists()
    finally:
        process.terminate()
        process.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
