import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
from conftest import login
from test_jobs import new_job

from noedaeri.integration import PLATFORM_OWNER


def platform(app):
    client = app.state.client
    client.cookies.clear()
    client.headers.clear()
    client.headers["X-Noedaeri-API-Key"] = app.state.settings.integration_key
    return client


def create(client, key=None):
    return client.post(
        "/api/v1/jobs",
        json={
            "kind": "video.thumbnail",
            "title": "Platform fixture",
            "idempotency_key": str(key or uuid4()),
            "input": {"type": "upload"},
            "options": {},
        },
    )


def complete(app, status="succeeded"):
    client = platform(app)
    job = create(client).json()
    path = f"/api/v1/jobs/{job['id']}"
    assert (
        client.put(
            path + "/input",
            content=b"fixture",
            headers={"Content-Type": "application/octet-stream"},
        ).status_code
        == 200
    )
    claimed = app.state.queue.claim(uuid4(), ["video.thumbnail"])
    output = app.state.storage.path("results", UUID(job["id"]), "thumbnail.jpg")
    output.parent.mkdir(parents=True)
    output.write_bytes(b"generated fixture")
    assert app.state.queue.finish(
        UUID(job["id"]),
        claimed["lease_token"],
        status,
        None,
        {"type": "artifact", "name": "thumbnail.jpg"},
    )
    return client, path, client.get(path).json()


def test_platform_auth_separation_idempotency_and_admin_visibility(app):
    client, _ = login(app)
    web = new_job(client).json()
    assert client.get("/api/v1/services").status_code == 401
    client = platform(app)
    assert client.get("/api/services").status_code == 401
    key = uuid4()
    first = create(client, key).json()
    assert first["origin"] == "platform"
    assert create(client, key).json()["id"] == first["id"]
    assert client.get("/api/v1/jobs/" + web["id"]).status_code == 404
    assert client.get("/api/admin/users").status_code == 401
    client, _ = login(app)
    assert client.get("/api/jobs/" + first["id"]).status_code == 404
    client, _ = login(app, role="admin")
    assert client.get("/api/jobs/" + first["id"]).status_code == 200
    assert any(row["id"] == first["id"] for row in client.get("/api/jobs").json())
    assert all(row["id"] != str(PLATFORM_OWNER) for row in client.get("/api/admin/users").json())


def test_webhook_signature_durable_retry_and_receipt(app):
    client, path, job = complete(app)
    remaining = datetime.fromisoformat(job["expires_at"]) - datetime.now(UTC)
    assert remaining.total_seconds() > 6 * 86400
    hooks = app.state.webhooks
    calls = []

    def receiver(request):
        calls.append(request)
        expected = hmac.new(
            app.state.settings.webhook_secret.encode(),
            request.headers["X-Noedaeri-Timestamp"].encode() + b"." + request.content,
            hashlib.sha256,
        ).hexdigest()
        assert request.headers["X-Noedaeri-Signature"] == "sha256=" + expected
        return httpx.Response(500 if len(calls) == 1 else 204)

    hooks.transport = httpx.MockTransport(receiver)
    hooks.collect()
    hooks.collect()
    assert hooks.dispatch_one()
    assert client.get(path).json()["delivery"]["attempts"] == 1
    assert not hooks.dispatch_one()  # persisted backoff
    with app.state.db.connect() as conn:
        conn.execute("UPDATE deliveries SET next_attempt_at=now()")
    assert hooks.dispatch_one()
    assert calls[0].content == calls[1].content
    event = json.loads(calls[0].content)
    assert event["job_id"] == job["id"] and event["event_id"] == job["terminal_event_id"]
    assert event["type"] == "job.succeeded"
    assert client.get(path + "/result").content == b"generated fixture"
    assert client.post(path + "/receipt", json={"event_id": str(uuid4())}).status_code == 409
    receipt = {"event_id": event["event_id"]}
    assert client.post(path + "/receipt", json=receipt).status_code == 200
    assert client.post(path + "/receipt", json=receipt).status_code == 200
    assert client.get(path + "/result").status_code == 410
    app.state.storage.cleanup(app.state.db)
    assert not (app.state.storage.root / "results" / job["id"]).exists()
    assert client.get(path).json()["received_at"]
    assert client.get(path).json()["delivery"]["state"] == "acknowledged"
    assert json.loads(calls[0].content)["job"]["result"]  # immutable despite cleanup


def test_delivery_exhaustion_manual_retry_and_no_redirect(app):
    client, path, job = complete(app, "failed")
    hooks = app.state.webhooks
    calls = []

    def receiver(request):
        calls.append(request)
        return httpx.Response(302, headers={"location": "https://other.example/"})

    hooks.transport = httpx.MockTransport(receiver)
    hooks.collect()
    for _ in range(8):
        with app.state.db.connect() as conn:
            conn.execute("UPDATE deliveries SET next_attempt_at=now()")
        assert hooks.dispatch_one()
    assert not hooks.dispatch_one()
    assert len(calls) == 8
    assert client.get(path).json()["delivery"]["state"] == "failed"
    assert (
        client.post(path + "/receipt", json={"event_id": job["terminal_event_id"]}).status_code
        == 409
    )
    client, _ = login(app, role="admin")
    assert client.post("/api/admin/jobs/" + job["id"] + "/webhook-retry").status_code == 200
    hooks.transport = httpx.MockTransport(lambda request: httpx.Response(204))
    assert hooks.dispatch_one()


def test_concurrent_dispatch_and_cancel_notification(app):
    client = platform(app)
    job = create(client).json()
    path = "/api/v1/jobs/" + job["id"]
    client.put(
        path + "/input", content=b"fixture", headers={"Content-Type": "application/octet-stream"}
    )
    assert client.post(path + "/cancel").status_code == 200
    hooks = app.state.webhooks
    hooks.collect()
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(lambda _: hooks.dispatch_one(), range(2)))
    assert outcomes.count(True) == 1
    with app.state.db.connect() as conn:
        row = conn.execute("SELECT body FROM deliveries").fetchone()
    assert json.loads(row["body"])["type"] == "job.cancelled"


def test_receipt_before_dispatch_preserves_event(app):
    client, path, job = complete(app)
    assert (
        client.post(path + "/receipt", json={"event_id": job["terminal_event_id"]}).status_code
        == 200
    )
    app.state.storage.cleanup(app.state.db)
    with app.state.db.connect() as conn:
        row = conn.execute("SELECT body,state FROM deliveries").fetchone()
    assert row["state"] == "acknowledged"
    assert json.loads(row["body"])["job"]["result"]["name"] == "thumbnail.jpg"


def test_webhook_example_rejects_tampering_staleness_and_wrong_event(app):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "platform_example", Path("examples/platform_client.py")
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    import pytest

    from noedaeri.integration import signature

    _, _, job = complete(app)
    app.state.webhooks.collect()
    with app.state.db.connect() as conn:
        body = conn.execute("SELECT body FROM deliveries").fetchone()["body"].encode()
    secret = app.state.settings.webhook_secret
    headers = {
        "X-Noedaeri-Timestamp": "1000",
        "X-Noedaeri-Event-ID": job["terminal_event_id"],
        "X-Noedaeri-Signature": "sha256=" + signature(secret, "1000", body),
    }
    assert example.verify_webhook(body, headers, secret, now=1001)["job_id"] == job["id"]
    with pytest.raises(ValueError):
        example.verify_webhook(body + b" ", headers, secret, now=1001)
    with pytest.raises(ValueError):
        example.verify_webhook(body, headers, secret, now=1400)
    headers["X-Noedaeri-Event-ID"] = str(uuid4())
    with pytest.raises(ValueError):
        example.verify_webhook(body, headers, secret, now=1001)


def test_upload_timeout_and_recovery_emit_failure_events(app):
    client = platform(app)
    first = create(client).json()
    second = create(client).json()
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET updated_at=now()-interval '1 hour' WHERE id=%s", (first["id"],)
        )
        conn.execute(
            "UPDATE jobs SET status='interrupted',execution_guarded=true,"
            "error_code='lease_lost' WHERE id=%s",
            (second["id"],),
        )
    app.state.storage.cleanup(app.state.db)
    app.state.webhooks.collect()
    for job in (first, second):
        row = client.get("/api/v1/jobs/" + job["id"]).json()
        assert row["status"] == "failed" and row["delivery"]["state"] == "pending"


def test_public_contract_and_no_internal_schema(app):
    client = app.state.client
    schema = client.get("/integrations/openapi.json")
    assert schema.status_code == 200
    spec = schema.json()
    assert all(path.startswith("/api/v1/") for path in spec["paths"])
    assert "/api/v1/jobs/{job_id}/receipt" in spec["paths"]
    assert spec["security"] == [{"PlatformKey": []}]
    assert client.get("/integrations/SERVICE_INTEGRATION.md").status_code == 200
    assert client.get("/api/integrations/guide").status_code == 401


def test_disabled_platform_rejects_before_creating_jobs(app):
    from dataclasses import replace

    from fastapi.testclient import TestClient

    from noedaeri.api import create_app

    settings = replace(app.state.settings, webhook_url="")
    isolated = create_app(settings)
    with TestClient(isolated, base_url="https://testserver") as client:
        client.headers["X-Noedaeri-API-Key"] = settings.integration_key
        assert client.get("/api/v1/services").status_code == 200
        assert create(client).status_code == 503
        client.headers["X-Noedaeri-API-Key"] = "incorrect"
        assert client.get("/api/v1/jobs").status_code == 401
        assert client.get("/api/v1/jobs/" + str(uuid4())).status_code == 401


def test_platform_ttl_without_receipt_and_upload_cancellation(app):
    client, path, job = complete(app)
    app.state.webhooks.collect()
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET expires_at=now()-interval '1 second' WHERE id=%s", (job["id"],)
        )
    app.state.storage.cleanup(app.state.db)
    assert (
        client.post(path + "/receipt", json={"event_id": job["terminal_event_id"]}).status_code
        == 410
    )
    assert not (app.state.storage.root / "results" / job["id"]).exists()
    new = create(client).json()
    assert client.post("/api/v1/jobs/" + new["id"] + "/cancel").status_code == 200
    assert client.get("/api/v1/jobs/" + new["id"]).json()["status"] == "cancelled"


def test_non_ascii_key_is_rejected_without_server_error(app):
    assert (
        app.state.client.get(
            "/api/v1/services", headers={b"X-Noedaeri-API-Key": b"\xe9"}
        ).status_code
        == 401
    )
