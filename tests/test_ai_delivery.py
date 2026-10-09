import importlib.util
import json
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import httpx
from conftest import login

from noedaeri.ai_jobs import cleanup_ai_results
from noedaeri.integration import Webhooks


def configured(app):
    app.state.settings = replace(
        app.state.settings,
        ai_webhook_url="https://receiver.example/ai-completion",
        n8n_ai_webhook_url="https://workflow.example/ai",
        n8n_compute_context_ready=True,
    )
    hooks = Webhooks(
        app.state.db,
        app.state.settings,
        table="ai_deliveries",
        url=app.state.settings.ai_webhook_url,
    )
    return hooks


def platform(app):
    client = app.state.client
    client.cookies.clear()
    client.headers.clear()
    client.headers["X-Noedaeri-API-Key"] = app.state.settings.integration_key
    return client


def payload(**extra):
    return {
        "request_id": str(uuid4()),
        "text": "synthetic original",
        "target_language": "en",
        "notify": True,
        **extra,
    }


def test_ai_notification_admission_scope_and_idempotency(app):
    client = platform(app)
    body = payload()
    assert client.post("/api/v1/translations", json=body).status_code == 503
    hooks = configured(app)
    response = client.post("/api/v1/translations", json=body)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["notify"] and job["terminal_event_id"]
    assert client.post("/api/v1/translations", json=body).json()["id"] == job["id"]
    assert client.post("/api/v1/translations", json={**body, "notify": False}).status_code == 409
    path = "/api/v1/ai/jobs/" + job["id"]
    assert client.get(path).json()["delivery"]["state"] == "waiting"
    assert client.get("/api/v1/tasks?service=translation").json()[0]["data"]["delivery"][
        "configured"
    ]
    assert client.post(path + "/cancel").status_code == 200
    hooks.collect()
    hooks.collect()
    with app.state.db.connect() as c:
        rows = c.execute("SELECT body FROM ai_deliveries").fetchall()
    assert len(rows) == 1
    assert json.loads(rows[0]["body"])["type"] == "ai.job.cancelled"
    client, _ = login(app)
    assert client.post("/api/translations", json=payload()).status_code == 422
    assert client.get("/api/ai/jobs/" + job["id"]).status_code == 404


def test_ai_completion_signature_retry_receipt_and_cleanup(app, monkeypatch):
    hooks = configured(app)

    async def run(settings, data):
        return {
            "result": {"translated_text": "synthetic translated"},
            "provider": "fixture",
            "model": "fixture",
            "usage": {},
        }

    monkeypatch.setattr("noedaeri.ai_jobs.run_n8n_workflow", run)
    client = platform(app)
    response = client.post(
        "/api/v1/ai/jobs",
        json={
            "request_id": str(uuid4()),
            "task_type": "text.translate",
            "prompt": "synthetic original",
            "input": {"target_language": "en"},
            "notify": True,
        },
    )
    assert response.status_code == 200, response.text
    job = response.json()
    path = "/api/v1/ai/jobs/" + job["id"]
    calls = []

    def receiver(request):
        calls.append(request)
        return httpx.Response(500 if len(calls) == 1 else 204)

    hooks.transport = httpx.MockTransport(receiver)
    hooks.collect()
    hooks.collect()
    assert hooks.dispatch_one()
    assert not hooks.dispatch_one()
    with app.state.db.connect() as c:
        c.execute("UPDATE ai_deliveries SET next_attempt_at=now()")
    assert hooks.dispatch_one()
    assert calls[0].content == calls[1].content
    assert str(calls[0].url) == "https://receiver.example/ai-completion"
    spec = importlib.util.spec_from_file_location("example", Path("examples/platform_client.py"))
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    event = example.verify_webhook(
        calls[0].content, calls[0].headers, app.state.settings.webhook_secret
    )
    assert event["type"] == "ai.job.succeeded" and event["source"] == "ai"
    assert "synthetic original" not in calls[0].content.decode()
    assert "synthetic translated" not in calls[0].content.decode()
    assert "result" not in event["job"]
    assert client.get(path).json()["delivery"]["state"] == "delivered"
    assert client.get(path + "/translation.txt").text == "synthetic translated"
    assert client.post(path + "/receipt", json={"event_id": str(uuid4())}).status_code == 409
    body = {"event_id": event["event_id"]}
    assert client.post(path + "/receipt", json=body).status_code == 200
    assert client.post(path + "/receipt", json=body).status_code == 200
    assert client.get(path).json()["result"] is None
    assert client.get(path + "/translation.txt").status_code == 410
    cleanup_ai_results(app.state.db)
    with app.state.db.connect() as c:
        row = c.execute(
            "SELECT prompt,input,result FROM ai_jobs WHERE id=%s", (job["id"],)
        ).fetchone()
        assert row["prompt"] == "" and row["input"] == {} and row["result"] is None
    assert client.get(path).json()["delivery"]["state"] == "acknowledged"
    assert json.loads(calls[0].content)["event_id"] == event["event_id"]


def test_ai_delivery_exhaustion_admin_retry_and_disabled_target(app):
    hooks = configured(app)
    client = platform(app)
    job = client.post("/api/v1/translations", json=payload()).json()
    with app.state.db.connect() as c:
        c.execute(
            "UPDATE ai_jobs SET status='failed',finished_at=now(),error_code='fixture' WHERE id=%s",
            (job["id"],),
        )
    hooks.collect()
    disabled = Webhooks(app.state.db, app.state.settings, table="ai_deliveries", url="")
    assert not disabled.dispatch_one()
    hooks.transport = httpx.MockTransport(
        lambda request: httpx.Response(302, headers={"Location": "https://other.example/"})
    )
    for _ in range(8):
        with app.state.db.connect() as c:
            c.execute("UPDATE ai_deliveries SET next_attempt_at=now()")
        assert hooks.dispatch_one()
    assert not hooks.dispatch_one()
    client, _ = login(app)
    retry = "/api/admin/ai/jobs/" + job["id"] + "/webhook-retry"
    assert client.post(retry).status_code == 403
    client, _ = login(app, role="admin")
    assert client.post(retry).status_code == 200
    hooks.transport = httpx.MockTransport(lambda request: httpx.Response(204))
    assert hooks.dispatch_one()
    assert client.get("/api/ai/jobs/" + job["id"]).json()["delivery"]["state"] == "delivered"


def test_platform_client_new_service_contracts_and_public_material(app):
    spec = importlib.util.spec_from_file_location("example", Path("examples/platform_client.py"))
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    calls = []
    client = example.PlatformClient("https://noedaeri.example", "synthetic-key")
    client.client.close()

    def receiver(request):
        assert request.headers["X-Noedaeri-API-Key"] == "synthetic-key"
        calls.append((request.url.path, json.loads(request.content) if request.content else None))
        return httpx.Response(
            202 if request.url.path == "/api/v1/translations" else 200, json={"id": "fixture"}
        )

    client.client = httpx.Client(
        base_url="https://noedaeri.example",
        headers={"X-Noedaeri-API-Key": "synthetic-key"},
        transport=httpx.MockTransport(receiver),
    )
    client.create(
        kind="pdf.extract",
        title="fixture",
        request_id=uuid4(),
        extension="pdf",
        options={"mode": "auto", "language": "ko"},
    )
    assert calls[-1][1]["options"] == {"mode": "auto", "language": "ko"}
    assert calls[-1][1]["input"] == {"type": "upload", "extension": "pdf"}
    client.create(
        kind="video.subtitles",
        title="fixture",
        request_id=uuid4(),
        options={"language": "auto", "use_itn": True},
    )
    assert "seconds" not in calls[-1][1]["options"]
    client.translate(
        request_id="fixture",
        text="hello",
        target_language="ko",
        project="fixture",
        environment="test",
    )
    assert calls[-1][1]["notify"] is True
    assert calls[-1][1]["source_language"] == "auto"
    identifier = uuid4()
    client.ai_job(identifier)
    client.ai_receipt(identifier, uuid4())
    assert calls[-1][0] == f"/api/v1/ai/jobs/{identifier}/receipt"
    client.close()
    http = app.state.client
    for url in ("/integrations/PLATFORM_HANDOFF.md", "/examples/platform-client.py"):
        response = http.get(url)
        assert response.status_code == 200 and "notify" in response.text
    paths = http.get("/integrations/openapi.json").json()["paths"]
    assert "/api/v1/translations" in paths and "/api/v1/ai/jobs/{job_id}/receipt" in paths


def test_ai_receipt_before_dispatch_and_concurrent_collect(app):
    from concurrent.futures import ThreadPoolExecutor

    hooks = configured(app)
    client = platform(app)
    job = client.post("/api/v1/translations", json=payload()).json()
    with app.state.db.connect() as c:
        c.execute(
            "UPDATE ai_jobs SET status='succeeded',finished_at=now(),result='{}' WHERE id=%s",
            (job["id"],),
        )
    with ThreadPoolExecutor(2) as pool:
        list(pool.map(lambda _: hooks.collect(), range(2)))
    path = "/api/v1/ai/jobs/" + job["id"]
    assert (
        client.post(path + "/receipt", json={"event_id": job["terminal_event_id"]}).status_code
        == 200
    )
    with app.state.db.connect() as c:
        rows = c.execute("SELECT state,body FROM ai_deliveries").fetchall()
    assert len(rows) == 1 and rows[0]["state"] == "acknowledged"
    assert json.loads(rows[0]["body"])["event_id"] == job["terminal_event_id"]
    assert not hooks.dispatch_one()


def test_ai_cancel_and_unexpected_provider_failure_emit_terminal_event(app, monkeypatch):
    hooks = configured(app)

    async def run(settings, data):
        with app.state.db.connect() as c:
            c.execute(
                "UPDATE ai_jobs SET cancel_requested=true WHERE request_id=%s", (data.request_id,)
            )
        raise RuntimeError("synthetic provider crash")

    monkeypatch.setattr("noedaeri.ai_jobs.run_n8n_workflow", run)
    client = platform(app)
    response = client.post(
        "/api/v1/ai/jobs",
        json={
            "request_id": str(uuid4()),
            "task_type": "chat.general",
            "prompt": "synthetic",
            "notify": True,
        },
    )
    assert response.status_code == 500
    hooks.collect()
    with app.state.db.connect() as c:
        row = c.execute("SELECT body FROM ai_deliveries").fetchone()
    assert json.loads(row["body"])["type"] == "ai.job.cancelled"


def test_ai_receiver_requires_fixed_https_and_credentials(monkeypatch):
    import pytest

    from noedaeri.config import Settings

    monkeypatch.setenv("DATABASE_URL", "fixture")
    monkeypatch.setenv("WORKER_API_KEY", "x" * 32)
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://fixture.example")
    monkeypatch.setenv("PLATFORM_OIDC_REDIRECT_URI", "https://fixture.example/auth/callback")
    monkeypatch.setenv("NOEDAERI_PLATFORM_API_KEY", "x" * 32)
    monkeypatch.setenv("NOEDAERI_PLATFORM_WEBHOOK_SECRET", "x" * 32)
    for url in (
        "http://receiver.example/ai",
        "https://user@receiver.example/ai",
        "https://receiver.example/ai?secret=fixture",
        "https://receiver.example/ai#fragment",
    ):
        monkeypatch.setenv("NOEDAERI_PLATFORM_AI_WEBHOOK_URL", url)
        with pytest.raises(ValueError):
            Settings.from_env()
    monkeypatch.setenv("NOEDAERI_PLATFORM_AI_WEBHOOK_URL", "https://receiver.example/ai")
    assert Settings.from_env().ai_webhook_url == "https://receiver.example/ai"
    monkeypatch.delenv("NOEDAERI_PLATFORM_WEBHOOK_SECRET")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_expired_general_ai_original_and_result_are_cleared_without_notification(app):
    from noedaeri.ai_jobs import cleanup_ai_results

    client = platform(app)
    job = client.post(
        "/api/v1/ai/jobs",
        json={
            "request_id": str(uuid4()),
            "task_type": "chat.general",
            "prompt": "synthetic original",
            "input": {"fixture": "value"},
            "sync": False,
        },
    ).json()
    with app.state.db.connect() as c:
        c.execute(
            "UPDATE ai_jobs SET status='succeeded',finished_at=now(),"
            "expires_at=now()-interval '1 second',result='{}' WHERE id=%s",
            (job["id"],),
        )
    cleanup_ai_results(app.state.db)
    with app.state.db.connect() as c:
        row = c.execute(
            "SELECT prompt,input,result,notify,status FROM ai_jobs WHERE id=%s", (job["id"],)
        ).fetchone()
        assert row == {
            "prompt": "",
            "input": {},
            "result": None,
            "notify": False,
            "status": "succeeded",
        }
    app.state.ai_webhooks.collect()
    with app.state.db.connect() as c:
        assert c.execute("SELECT count(*) AS n FROM ai_deliveries").fetchone()["n"] == 0
