import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import uvicorn
from conftest import login
from test_jobs import new_job


def test_worker_process_calls_api_and_finishes(app):
    client, _ = login(app)
    job_id = new_job(client).json()["id"]
    source = app.state.storage.root / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=320x180:d=1",
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
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            job = client.get("/api/jobs").json()[0]
            if job["status"] in {"succeeded", "failed"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded"
        assert job["result"]["type"] == "artifact"
        assert client.get(f"/api/jobs/{job_id}/result").content.startswith(b"\xff\xd8")
    finally:
        process.terminate()
        process.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
