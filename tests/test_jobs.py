import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from conftest import login

from noedaeri.media import thumbnail


def new_job(client, key=None, title="테스트 작업", kind="video.thumbnail"):
    return client.post(
        "/api/jobs",
        json={
            "kind": kind,
            "title": title,
            "idempotency_key": str(key or uuid4()),
            "input": {
                "type": "upload",
                **({"extension": "png"} if kind == "image.package" else {}),
            },
            "options": {} if kind == "image.package" else {"seconds": 0},
        },
    )


def queued(app):
    client, owner = login(app)
    job = new_job(client).json()
    response = client.put(
        f"/api/jobs/{job['id']}/input",
        content=b"test",
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200
    return UUID(job["id"]), owner


def test_permissions_and_revocation(app):
    client, user = login(app, status="pending")
    assert client.get("/api/me").json()["status"] == "pending"
    assert client.get("/api/jobs").status_code == 403
    assert client.get("/api/admin/users").status_code == 403
    with app.state.db.connect() as conn:
        conn.execute("UPDATE users SET status='approved' WHERE id=%s", (user,))
    assert client.get("/api/jobs").status_code == 200
    client.headers["X-CSRF-Token"] = "wrong"
    assert new_job(client).status_code == 403
    with app.state.db.connect() as conn:
        conn.execute("UPDATE users SET status='revoked' WHERE id=%s", (user,))
    assert client.get("/api/jobs").status_code == 403


def test_idempotency_and_ownership(app):
    client, _ = login(app)
    key = uuid4()
    first = new_job(client, key).json()
    assert new_job(client, key).json()["id"] == first["id"]
    assert new_job(client, key, "다른 작업").status_code == 409
    client, _ = login(app)
    assert client.get("/api/jobs").json() == []
    assert client.get(f"/api/jobs/{first['id']}/result").status_code == 404
    assert (
        client.put(
            f"/api/jobs/{first['id']}/input",
            content=b"x",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 404
    )


def test_atomic_claim_and_fencing(app):
    job_id, _ = queued(app)
    queue = app.state.queue
    with ThreadPoolExecutor(4) as pool:
        rows = list(pool.map(lambda _: queue.claim(uuid4(), ["video.thumbnail"]), range(4)))
    claimed = [row for row in rows if row]
    assert len(claimed) == 1
    token = claimed[0]["lease_token"]
    assert not queue.finish(job_id, uuid4(), "succeeded", None)
    with app.state.db.connect() as conn:
        conn.execute("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (job_id,))
    assert not queue.heartbeat(job_id, token)
    assert not queue.finish(job_id, token, "succeeded", None)
    assert not queue.claim(uuid4(), ["video.thumbnail"])
    with app.state.db.connect() as conn:
        assert (
            conn.execute("SELECT status FROM jobs WHERE id=%s", (job_id,)).fetchone()["status"]
            == "interrupted"
        )


def test_cancel_is_confirmed_by_worker(app):
    job_id, owner = queued(app)
    queue = app.state.queue
    job = queue.claim(uuid4(), ["video.thumbnail"])
    assert queue.cancel(job_id, owner)
    assert queue.heartbeat(job_id, job["lease_token"])["cancel_requested"]
    assert queue.finish(job_id, job["lease_token"], "succeeded", None)
    with app.state.db.connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=%s", (job_id,)).fetchone()
    assert row["status"] == "cancelled"
    assert row["result_state"] == "none"


def test_real_ffmpeg_result_and_expiry(app):
    client, _ = login(app)
    storage, queue = app.state.storage, app.state.queue
    job_id = UUID(new_job(client).json()["id"])
    source = storage.root / "fixture.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=640x360:d=1",
            "-c:v",
            "libx264",
            "-y",
            str(source),
        ],
        check=True,
    )
    response = client.put(
        f"/api/jobs/{job_id}/input",
        content=source.read_bytes(),
        headers={"Content-Type": "application/octet-stream"},
    )
    source.unlink()
    assert response.status_code == 200
    job = queue.claim(uuid4(), ["video.thumbnail"])
    output = storage.path("results", job_id, "thumbnail.jpg")
    thumbnail(storage.path("uploads", job_id, "input"), output, 0, 30, lambda: True)
    assert output.read_bytes().startswith(b"\xff\xd8")
    assert queue.finish(job_id, job["lease_token"], "succeeded", None)
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 200
    storage.cleanup(app.state.db)
    assert not storage.path("uploads", job_id, "input").exists()
    assert output.exists()
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET expires_at=%s WHERE id=%s",
            (datetime.now(UTC) - timedelta(seconds=1), job_id),
        )
    assert client.get(f"/api/jobs/{job_id}/result").status_code == 410
    storage.cleanup(app.state.db)
    assert not output.exists()
    assert client.get("/api/jobs").json()[0]["result_state"] == "expired"


def test_unknown_kind_and_worker_capabilities(app):
    job_id, _ = queued(app)
    assert app.state.queue.claim(uuid4(), ["tts.synthesize"]) is None
    assert app.state.queue.claim(uuid4(), ["video.thumbnail"])["id"] == job_id
    client = app.state.client
    assert (
        client.post(
            "/internal/claim", json={"worker_id": str(uuid4()), "kinds": ["video.thumbnail"]}
        ).status_code
        == 401
    )


def test_non_file_service_uses_same_queue(app, monkeypatch):
    from typing import Literal

    from pydantic import BaseModel

    from noedaeri.services import SERVICES, Service

    class TextInput(BaseModel):
        type: Literal["text"]
        text: str

    class EmptyOptions(BaseModel):
        pass

    monkeypatch.setitem(
        SERVICES,
        "text.echo",
        Service("text.echo", "test", "텍스트 테스트", "text", EmptyOptions, input_model=TextInput),
    )
    client, _ = login(app)
    response = client.post(
        "/api/jobs",
        json={
            "kind": "text.echo",
            "title": "파일 없는 작업",
            "idempotency_key": str(uuid4()),
            "input": {"type": "text", "text": "hello"},
            "options": {},
        },
    )
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    claimed = app.state.queue.claim(uuid4(), ["text.echo"])
    assert claimed["input"]["text"] == "hello"
    assert app.state.queue.finish(
        claimed["id"],
        claimed["lease_token"],
        "succeeded",
        None,
        {"type": "json", "value": {"text": "hello"}},
    )
    assert client.get(f"/api/jobs/{claimed['id']}/result").json()["value"]["text"] == "hello"


def test_execution_capacity_includes_input_files(app):
    from dataclasses import replace

    from noedaeri.storage import Storage

    storage = Storage(replace(app.state.settings, storage_limit=10, upload_limit=0))
    source = storage.path("uploads", uuid4(), "input")
    source.parent.mkdir(parents=True)
    source.write_bytes(b"12345678901")
    assert not storage.available(None)


def test_integration_guide_requires_current_approval(app):
    client = app.state.client
    assert client.get("/api/integrations/guide").status_code == 401
    client, user = login(app, status="pending")
    assert client.get("/api/integrations/guide").status_code == 403
    with app.state.db.connect() as conn:
        conn.execute("UPDATE users SET status='approved' WHERE id=%s", (user,))
    response = client.get("/api/integrations/guide")
    assert response.status_code == 200
    from pathlib import Path

    assert response.content == Path("docs/SERVICE_INTEGRATION.md").read_bytes()
    assert "video.package" in response.text
    assert "text/markdown" in response.headers["content-type"]


def test_upload_exact_limit_and_overflow_cleanup(app):
    client, _ = login(app)
    first = new_job(client).json()
    limit = app.state.settings.upload_limit
    payload = b"x" * limit
    headers = {"Content-Type": "application/octet-stream"}
    assert (
        client.put(f"/api/jobs/{first['id']}/input", content=payload, headers=headers).status_code
        == 200
    )
    second = new_job(client).json()
    url = f"/api/jobs/{second['id']}/input"
    response = client.put(url, content=payload + b"x", headers=headers)
    assert response.status_code == 413
    assert not app.state.storage.path("uploads", UUID(second["id"]), "input").exists()
    assert client.put(url, content=b"retry", headers=headers).status_code == 200


def test_large_upload_defaults_and_proxy_agree(tmp_path):
    import importlib.util
    from pathlib import Path

    from noedaeri.config import Settings

    settings = Settings(
        database_url="unused",
        worker_key="unused",
        public_origin="https://example.invalid",
        storage_root=tmp_path,
    )
    assert settings.upload_limit == 5 * 1024**3
    assert settings.storage_limit == 20 * 1024**3
    spec = importlib.util.spec_from_file_location("manage_fixture", Path("scripts/manage.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    path = module.nginx_config(
        {
            "STATE_ROOT": str(tmp_path),
            "INGRESS_PORT": "12345",
            "WORKER_API_ORIGIN": "http://127.0.0.1:8000",
        }
    )
    assert "client_max_body_size 5368709120;" in path.read_text()
    assert "proxy_read_timeout 3600s;" in path.read_text()
