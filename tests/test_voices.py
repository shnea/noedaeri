import io
import os
import socket
import subprocess
import sys
import threading
import time
import wave
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import uvicorn
from conftest import login

from noedaeri.execution import native_compute_lock
from noedaeri.media import JobCancelled, MediaError, native_compute_slot
from noedaeri.tts import normalize_reference, synthesize


def enabled(app):
    app.state.settings = replace(app.state.settings, tts_enabled=True)
    return login(app)[0]


def profile(client, kind="preset", **extra):
    return client.post(
        "/api/voices",
        json={
            "name": "테스트 목소리",
            "kind": kind,
            "idempotency_key": str(uuid4()),
            **({"speaker": "Sohee"} if kind == "preset" else {"reference_text": "안녕하세요."}),
            **extra,
        },
    )


def speech(client, voice, **extra):
    return client.post(
        "/api/jobs",
        json={
            "kind": "tts.synthesize",
            "title": "음성 테스트",
            "idempotency_key": str(uuid4()),
            "options": {"voice_id": voice["id"]},
            "input": {
                "type": "text",
                "text": "안녕하세요.",
                "requester_id": voice["requester_id"],
                "project": voice["project"],
                "environment": voice["environment"],
            },
            **extra,
        },
    )


def wav(duration=4, rate=24000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"\0\0" * rate * duration)
    return buffer.getvalue()


def register(app, client):
    voice = profile(client, "clone").json()
    job_id = voice["registration_job_id"]
    assert (
        client.put(
            f"/api/jobs/{job_id}/input",
            content=wav(),
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 200
    )
    job = app.state.queue.claim(uuid4(), ["tts.voice.register"])
    return voice, job


def finish(app, job, status="succeeded"):
    return app.state.client.post(
        f"/internal/jobs/{job['id']}/finish",
        headers={"Authorization": "Bearer " + app.state.settings.worker_key},
        json={
            "token": str(job["lease_token"]),
            "status": status,
            "result": {"duration_seconds": 4},
        },
    )


def test_preset_crud_idempotency_and_scope(app):
    client = enabled(app)
    key = str(uuid4())
    voice = profile(client, idempotency_key=key).json()
    assert voice["status"] == "ready"
    assert profile(client, idempotency_key=key).json()["id"] == voice["id"]
    assert profile(client, idempotency_key=key, name="변경").status_code == 409
    assert speech(client, voice).status_code == 201
    assert client.delete(f"/api/voices/{voice['id']}").status_code == 409
    assert (
        speech(
            client, voice, input={"type": "text", "text": "x", "requester_id": "other"}
        ).status_code
        == 403
    )
    assert (
        client.patch(f"/api/voices/{voice['id']}", json={"name": "별명"}).json()["name"] == "별명"
    )
    task = client.get("/api/tasks").json()[0]
    assert task["service"] == "tts"
    assert app.state.queue.cancel(UUID(task["id"]), UUID(voice["owner_id"]))
    assert client.delete(f"/api/voices/{voice['id']}").json()["deleted"]
    assert speech(client, voice).status_code == 404


def test_owner_and_approval_gate(app):
    client = enabled(app)
    voice = profile(client).json()
    login(app)
    assert client.get(f"/api/voices/{voice['id']}").status_code == 404
    assert speech(client, voice).status_code == 403
    login(app, status="pending")
    assert client.get("/api/voices").status_code == 403
    assert profile(client).status_code == 403


def test_clone_activation_survives_temporary_result_cleanup(app):
    client = enabled(app)
    voice, job = register(app, client)
    assert speech(client, voice).status_code == 409
    path = app.state.storage.path("results", job["id"], "reference.wav")
    normalize_reference(app.state.storage.path("uploads", job["id"], "input"), path, lambda: True)
    assert finish(app, job).status_code == 200
    ready = client.get(f"/api/voices/{voice['id']}").json()
    assert ready["status"] == "ready"
    assert client.get(f"/api/voices/{voice['id']}/sample").status_code == 200
    assert (
        speech(client, ready, options={"voice_id": voice["id"], "instruct": "happy"}).status_code
        == 422
    )
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET expires_at=%s WHERE id=%s",
            (datetime.now(UTC) - timedelta(seconds=1), job["id"]),
        )
    app.state.storage.cleanup(app.state.db)
    assert not path.exists()
    assert app.state.voices.sample(voice["id"]).is_file()
    assert client.delete(f"/api/voices/{voice['id']}").json()["deleted"]
    assert not app.state.voices.sample(voice["id"]).exists()


def test_cancelled_or_expired_registration_cannot_activate(app):
    client = enabled(app)
    voice, job = register(app, client)
    path = app.state.storage.path("results", job["id"], "reference.wav")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav())
    assert app.state.queue.cancel(job["id"], job["owner_id"])
    assert finish(app, job).status_code == 200
    assert client.get(f"/api/voices/{voice['id']}").json()["status"] == "cancelled"
    assert not app.state.voices.sample(voice["id"]).exists()
    voice, job = register(app, client)
    path = app.state.storage.path("results", job["id"], "reference.wav")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(wav())
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (job["id"],)
        )
    assert finish(app, job).status_code == 409
    assert not app.state.voices.sample(voice["id"]).exists()


def test_reference_validation_and_quota(app, tmp_path):
    client = enabled(app)
    voice, job = register(app, client)
    path = app.state.storage.path("results", job["id"], "reference.wav")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"invalid")
    assert finish(app, job).status_code == 409
    source = tmp_path / "short.wav"
    source.write_bytes(wav(1))
    with pytest.raises(MediaError, match="unsupported_media"):
        normalize_reference(source, tmp_path / "output.wav", lambda: True)
    # Presets don't reserve sample storage.
    assert profile(client, speaker="unknown").status_code == 422


def test_platform_requester_required_and_cross_requester_rejection(app):
    enabled(app)
    client = app.state.client
    client.cookies.clear()
    client.headers["X-Noedaeri-API-Key"] = app.state.settings.integration_key
    payload = {
        "name": "플랫폼 목소리",
        "kind": "preset",
        "speaker": "Sohee",
        "idempotency_key": str(uuid4()),
    }
    assert client.post("/api/v1/voices", json=payload).status_code == 422
    voice = client.post("/api/v1/voices", json={**payload, "requester_id": "caller-a"}).json()
    assert client.get("/api/v1/voices").status_code == 422
    assert client.get(f"/api/v1/voices/{voice['id']}?requester_id=caller-b").status_code == 404
    task = {
        "kind": "tts.synthesize",
        "title": "플랫폼",
        "idempotency_key": str(uuid4()),
        "input": {"type": "text", "text": "hello"},
        "options": {"voice_id": voice["id"]},
    }
    assert client.post("/api/v1/jobs", json=task).status_code == 422
    task["input"]["requester_id"] = "caller-b"
    assert client.post("/api/v1/jobs", json=task).status_code == 404
    task["input"]["requester_id"] = "caller-a"
    job = client.post("/api/v1/jobs", json=task).json()
    assert job["status"] == "queued"
    claimed = app.state.queue.claim(uuid4(), ["tts.synthesize"])
    output = app.state.storage.path("results", claimed["id"], "speech.wav")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(wav())
    assert finish(app, claimed).status_code == 200
    app.state.webhooks.collect()
    while app.state.webhooks.dispatch_one():
        pass

    result = client.get(f"/api/v1/jobs/{job['id']}").json()
    assert result["delivery"]["state"] == "delivered"
    assert result["result"]["duration_seconds"] == 4


def test_required_voice_mount_refuses_local_fallback(app, tmp_path, monkeypatch):
    client = enabled(app)
    app.state.voices.settings = replace(app.state.settings, voice_mount_root=tmp_path)
    monkeypatch.setattr("noedaeri.voice_storage.os.path.ismount", lambda path: False)
    assert profile(client, "clone").status_code == 503
    assert not app.state.settings.voice_root.exists()
    voice = profile(client).json()
    assert speech(client, voice).status_code == 201


def test_reference_quota_and_upload_limit(app):
    client = enabled(app)
    voice = profile(client, "clone").json()
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE voices SET sample_bytes=%s WHERE id=%s",
            (app.state.settings.voice_storage_limit, voice["id"]),
        )
    assert profile(client, "clone").status_code == 507
    assert (
        client.put(
            f"/api/jobs/{voice['registration_job_id']}/input",
            content=b"x" * (app.state.settings.upload_limit + 1),
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 413
    )


@pytest.mark.parametrize(
    "kind",
    [
        "clone",
        pytest.param(
            "preset",
            marks=pytest.mark.skipif(
                os.environ.get("RUN_TTS_SMOKE") != "1", reason="opt-in MLX worker generation"
            ),
        ),
    ],
)
def test_tts_worker_http_flow(app, kind):
    client = enabled(app)
    app.state.storage.settings = replace(app.state.settings, storage_limit=128 * 1024**2)
    voice = profile(client, kind).json()
    if kind == "clone":
        job_id = voice["registration_job_id"]
        assert (
            client.put(
                f"/api/jobs/{job_id}/input",
                content=wav(),
                headers={"Content-Type": "application/octet-stream"},
            ).status_code
            == 200
        )
    else:
        job_id = speech(client, voice).json()["id"]
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off")
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    settings = app.state.settings
    env = dict(
        os.environ,
        WORKER_API_KEY=settings.worker_key,
        DATABASE_URL=settings.database_url,
        PUBLIC_ORIGIN="https://testserver",
        WORKER_API_ORIGIN=f"http://127.0.0.1:{listener.getsockname()[1]}",
        STORAGE_ROOT=str(settings.storage_root),
        VOICE_STORAGE_ROOT=str(settings.voice_root),
        TTS_ENABLED="1",
        PLATFORM_OIDC_REDIRECT_URI="https://testserver/auth/callback",
        PYTHONPATH=str(Path("src").resolve()),
    )
    env.pop("VOICE_STORAGE_MOUNT_ROOT", None)
    env.pop("SERVICE_STORAGE_MOUNT_ROOT", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "noedaeri.worker"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            assert process.poll() is None, "TTS worker exited before processing"
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            time.sleep(0.1)
        assert job["status"] == "succeeded", job.get("error_code")
        assert client.get(f"/api/jobs/{job_id}/result").content.startswith(b"RIFF")
        if kind == "clone":
            assert client.get(f"/api/voices/{voice['id']}").json()["status"] == "ready"
        else:
            assert job["result"]["duration_seconds"] > 0
    finally:
        process.terminate()
        process.wait(timeout=10)
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()


def test_native_compute_contention_timeout_and_cancellation(tmp_path):
    with native_compute_lock(tmp_path):
        with pytest.raises(MediaError, match="processing_timeout"):
            with native_compute_slot(tmp_path, lambda: True, lambda stage: None, 0):
                pytest.fail("must not execute while reserved")
        with pytest.raises(JobCancelled):
            with native_compute_slot(tmp_path, lambda: False, lambda stage: None, 10):
                pytest.fail("must not run after cancellation")
    with native_compute_slot(tmp_path, lambda: True, lambda stage: None, 1):
        pass


@pytest.mark.skipif(
    __import__("os").environ.get("RUN_TTS_SMOKE") != "1", reason="opt-in native MLX smoke"
)
def test_real_preset_and_clone_generation(app):
    client = enabled(app)
    settings = app.state.settings
    voice = profile(client).json()
    response = speech(
        client,
        voice,
        input={
            "type": "text",
            "text": "안녕하세요. 뇌대리에서 등록한 목소리로 음성을 만드는 테스트입니다. "
            "오늘도 좋은 하루 보내세요.",
        },
    )
    assert response.status_code == 201
    job = app.state.queue.claim(uuid4(), ["tts.synthesize"])
    stages = []
    result = synthesize(
        settings,
        app.state.storage,
        job,
        {**voice, "sample_sha256": None},
        lambda: True,
        stages.append,
    )
    assert result["duration_seconds"] > 0
    # MLX's allocator target permits temporary overshoot; it isn't an RSS limit.
    assert result["peak_memory_bytes"] < settings.tts_memory_limit * 1.25
    print("TTS preset verified", result)
    with app.state.db.connect() as conn:
        conn.execute("UPDATE jobs SET status='cancelled' WHERE id=%s", (job["id"],))
    cloned, registration = register(app, client)
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE voices SET reference_text=%s WHERE id=%s", (job["input"]["text"], cloned["id"])
        )
    original = app.state.storage.path("results", job["id"], "speech.wav")
    normalized = app.state.storage.path("results", registration["id"], "reference.wav")
    raw = original.read_bytes()
    app.state.storage.path("uploads", registration["id"], "input").write_bytes(raw)
    normalize_reference(
        app.state.storage.path("uploads", registration["id"], "input"), normalized, lambda: True
    )
    assert finish(app, registration).status_code == 200
    with app.state.db.connect() as conn:
        clone = conn.execute("SELECT * FROM voices WHERE id=%s", (cloned["id"],)).fetchone()
    assert speech(client, cloned).status_code == 201
    cloned_job = app.state.queue.claim(uuid4(), ["tts.synthesize"])
    result = synthesize(settings, app.state.storage, cloned_job, clone, lambda: True, stages.append)
    assert result["duration_seconds"] > 0
    assert result["peak_memory_bytes"] < settings.tts_memory_limit * 1.25
    print("TTS preset and clone verified", result)
