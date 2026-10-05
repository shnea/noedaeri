import io
import os
import socket
import subprocess
import sys
import threading
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import uvicorn
from conftest import login
from test_jobs import new_job


@pytest.mark.parametrize("kind", ["video.thumbnail", "video.package", "image.package"])
def test_worker_process_calls_api_and_finishes(app, kind):
    client, _ = login(app)
    job_id = new_job(client, kind=kind).json()["id"]
    source = app.state.storage.root / "sample.mp4"
    if kind == "image.package":
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
            f"/api/jobs/{job_id}/input",
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
            job = client.get("/api/jobs").json()[0]
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded"
        if kind == "video.thumbnail":
            assert job["result"]["type"] == "artifact"
            assert client.get(f"/api/jobs/{job_id}/result").content.startswith(b"\xff\xd8")
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
