import io
import json
import os
import wave
from dataclasses import replace
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from conftest import login

from noedaeri.config import Settings
from noedaeri.media import JobCancelled, MediaError
from noedaeri.storage import Storage
from noedaeri.stt import transcribe


def create_stt(client, key=None, options=None, kind="stt.transcribe"):
    return client.post(
        "/api/jobs",
        json={
            "kind": kind,
            "title": "음성 인식 검수",
            "idempotency_key": str(key or uuid4()),
            "input": {"type": "upload"},
            "options": options or {},
        },
    )


@pytest.mark.parametrize(
    "kind,service", [("stt.transcribe", "stt"), ("video.subtitles", "subtitles")]
)
def test_stt_catalog_validation_and_idempotency(app, kind, service):
    client, _ = login(app)
    catalog = {row["kind"]: row for row in client.get("/api/services").json()}
    assert catalog[kind]["available"] is False
    assert create_stt(client, kind=kind).status_code == 503
    app.state.settings = replace(app.state.settings, stt_enabled=True)
    assert create_stt(client, kind=kind, options={"language": "de"}).status_code == 422
    assert create_stt(client, kind=kind, options={"seconds": 0}).status_code == 422
    key = uuid4()
    first = create_stt(client, key, kind=kind).json()
    assert first["options"] == {"language": "auto", "use_itn": True}
    assert create_stt(client, key, kind=kind).json()["id"] == first["id"]
    assert create_stt(client, key, {"language": "ko"}, kind=kind).status_code == 409
    items = client.get(f"/api/tasks?service={service}").json()
    assert items[0]["id"] == first["id"] and items[0]["service"] == service


@pytest.mark.parametrize("kind", ["stt.transcribe", "video.subtitles"])
def test_stt_callback_result_receipt(app, kind):
    from test_integration import platform

    app.state.settings = replace(app.state.settings, stt_enabled=True)
    client = platform(app)
    response = client.post(
        "/api/v1/jobs",
        json={
            "kind": kind,
            "title": "외부 음성 인식",
            "input": {"type": "upload"},
            "options": {"language": "ko"},
            "idempotency_key": str(uuid4()),
        },
    )
    key = UUID(response.json()["id"])
    assert (
        client.put(
            f"/api/v1/jobs/{key}/input",
            content=b"fixture",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 200
    )
    claimed = app.state.queue.claim(uuid4(), [kind])
    names = (
        ["transcript.json", "transcript.txt", "transcript.zip"]
        if kind == "stt.transcribe"
        else [
            "transcript.json",
            "transcript.txt",
            "subtitles.srt",
            "subtitles.vtt",
            "subtitles.zip",
        ]
    )
    for name in names:
        path = app.state.storage.path("results", key, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    worker = {"Authorization": "Bearer " + app.state.settings.worker_key}
    finished = client.post(
        f"/internal/jobs/{key}/finish",
        headers=worker,
        json={
            "token": str(claimed["lease_token"]),
            "status": "succeeded",
            "result": {"type": kind.replace(".", "_"), "files": names, "segment_count": 1},
        },
    )
    assert finished.status_code == 200
    if kind == "video.subtitles":
        assert (
            client.get(f"/api/v1/jobs/{key}/files/subtitles.vtt")
            .headers["content-type"]
            .startswith("text/vtt")
        )
        assert client.get(f"/api/v1/jobs/{key}/result").headers["content-type"] == "application/zip"
    app.state.webhooks.collect()
    assert app.state.webhooks.dispatch_one()
    job = client.get(f"/api/v1/jobs/{key}").json()
    assert job["delivery"]["state"] == "delivered"
    assert (
        client.post(
            f"/api/v1/jobs/{key}/receipt",
            json={
                "event_id": job["terminal_event_id"],
            },
        ).status_code
        == 200
    )
    app.state.storage.cleanup(app.state.db)
    assert client.get(f"/api/v1/jobs/{key}/result").status_code == 410


def setup_source(tmp_path, audio):
    settings = Settings(database_url="", worker_key="", public_origin="", storage_root=tmp_path)
    storage = Storage(settings)
    key = uuid4()
    path = storage.path("uploads", key, "input")
    path.parent.mkdir()
    path.write_bytes(audio)
    return settings, storage, {"id": str(key), "options": {"language": "auto", "use_itn": True}}


def silent_wave(seconds=1):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(16000)
        file.writeframes(b"\x00\x00" * (16000 * seconds))
    return buffer.getvalue()


@pytest.mark.skipif(os.environ.get("RUN_STT_SMOKE") != "1", reason="Native STT opt-in")
def test_native_stt_silence_and_rejected_inputs(tmp_path):
    settings, storage, job = setup_source(tmp_path, silent_wave())
    result = transcribe(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
    assert result["segment_count"] == 0
    transcript = json.loads(storage.path("results", job["id"], "transcript.json").read_text())
    assert transcript["text"] == "" and transcript["segments"] == []
    assert not storage.path("jobs", job["id"], "audio.wav").exists()
    with pytest.raises(JobCancelled):
        transcribe(settings, storage, job, lambda: False, lambda _: None, lambda _: None)
    with pytest.raises(MediaError, match="processing_timeout"):
        transcribe(
            replace(settings, stt_timeout=0),
            storage,
            job,
            lambda: True,
            lambda _: None,
            lambda _: None,
        )
    settings = replace(settings, stt_max_duration=1)
    storage.path("uploads", job["id"], "input").write_bytes(silent_wave(2))
    with pytest.raises(MediaError, match="stt_duration_exceeded"):
        transcribe(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
    storage.path("uploads", job["id"], "input").write_bytes(b"not audio")
    with pytest.raises(MediaError, match="invalid_media_or_conversion_failed"):
        transcribe(settings, storage, job, lambda: True, lambda _: None, lambda _: None)


def test_stt_missing_runtime_is_explicit(tmp_path, monkeypatch):
    import noedaeri.stt as module

    monkeypatch.setattr(module, "ROOT", Path(tmp_path) / "not-installed")
    settings, storage, job = setup_source(tmp_path, silent_wave())
    with pytest.raises(MediaError, match="stt_not_configured"):
        transcribe(settings, storage, job, lambda: True, lambda _: None, lambda _: None)
