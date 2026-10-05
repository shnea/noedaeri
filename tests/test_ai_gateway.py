import json
from dataclasses import replace

import httpx
import pytest
from conftest import login

from noedaeri.ai_gateway import EXHAUSTED_MESSAGE, providers, rotation

HOSTS = {
    "openrouter.ai": "openrouter",
    "api.groq.com": "groq",
    "generativelanguage.googleapis.com": "gemini",
    "api.mistral.ai": "mistral",
}


def ok(text="answer", usage=True):
    body = {"model": "served-model", "choices": [{"message": {"content": text}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 11, "completion_tokens": 7}
    return httpx.Response(200, json=body)


@pytest.fixture
def ai(app):
    gateway = app.state.gateway
    settings = replace(
        app.state.settings,
        openrouter_key="or-key",
        groq_key="groq-key",
        gemini_key="gemini-key",
        mistral_key="mistral-key",
    )
    gateway.providers = providers(settings)
    calls = []
    replies = {}

    def handler(request):
        name = HOSTS[request.url.host]
        calls.append((name, json.loads(request.content), request.headers["Authorization"]))
        reply = replies.get(name, ok)
        return reply() if callable(reply) else reply

    gateway.transport = httpx.MockTransport(handler)
    client = app.state.client
    headers = {"X-Noedaeri-Raya-Key": app.state.settings.raya_key}

    def generate(**body):
        payload = {"task_type": "chat.general", "prompt": "hello", "cache": False, **body}
        return client.post("/api/ai/v1/generate", json=payload, headers=headers)

    return generate, calls, replies, gateway


def usage_rows(app):
    with app.state.db.connect() as conn:
        return conn.execute("SELECT * FROM ai_usage ORDER BY created_at").fetchall()


def test_rotation_follows_requested_ring():
    assert rotation("L1") == ("L1", "FALLBACK", "L3", "L2")
    assert rotation("L2") == ("L2", "L1", "FALLBACK", "L3")
    assert rotation("L3") == ("L3", "L2", "L1", "FALLBACK")


def test_requires_internal_key(app):
    response = app.state.client.post(
        "/api/ai/v1/generate", json={"task_type": "chat.general", "prompt": "x"}
    )
    assert response.status_code == 401


def test_requested_tier_calls_matching_provider_and_records_usage(app, ai):
    generate, calls, _, _ = ai
    response = generate(tier="L2", instruction="be brief", request_id="req-1")
    assert response.status_code == 200
    data = response.json()
    assert data["provider"] == "groq" and data["slot"] == "L2" and data["text"] == "answer"
    assert data["usage"] == {"input_tokens": 11, "output_tokens": 7}
    name, sent, auth = calls[0]
    assert name == "groq" and auth == "Bearer groq-key"
    assert sent["messages"][0] == {"role": "system", "content": "be brief"}
    [row] = usage_rows(app)
    assert row["status"] == "succeeded" and row["provider"] == "groq"
    assert row["request_id"] == "req-1" and row["input_tokens"] == 11


def test_raya_unavailable_starts_from_l1(app, ai):
    generate, calls, _, _ = ai
    data = generate().json()
    assert data["raya_error"] == "raya_not_configured" and data["recommended_tier"] is None
    assert calls[0][0] == "openrouter"


def test_exhausted_provider_falls_through_and_cools_down(app, ai):
    generate, calls, replies, gateway = ai
    replies["openrouter"] = httpx.Response(429, headers={"Retry-After": "30"})
    data = generate(tier="L1").json()
    assert data["provider"] == "mistral"
    assert [a["result"] for a in data["attempts"]] == ["exhausted", "succeeded"]
    assert data["attempts"][0]["http_status"] == 429
    assert 0 < gateway.status()["providers"][0]["cooldown_seconds"] <= 30
    calls.clear()
    second = generate(tier="L1").json()
    assert second["attempts"][0]["result"] == "cooldown"
    assert [c[0] for c in calls] == ["mistral"]


def test_all_exhausted_reports_no_tokens(app, ai):
    generate, calls, replies, _ = ai
    for name in ("openrouter", "groq", "gemini", "mistral"):
        replies[name] = httpx.Response(429)
    response = generate(tier="L3")
    assert response.status_code == 503
    data = response.json()
    assert data["detail"] == "ai_tokens_exhausted" and data["message"] == EXHAUSTED_MESSAGE
    assert [c[0] for c in calls] == ["gemini", "groq", "openrouter", "mistral"]
    assert usage_rows(app)[-1]["status"] == "exhausted"


def test_errors_are_not_reported_as_exhaustion(app, ai):
    generate, _, replies, _ = ai
    replies["openrouter"] = httpx.Response(429)
    replies["groq"] = httpx.Response(500)
    replies["gemini"] = httpx.Response(400)
    replies["mistral"] = httpx.Response(402)
    response = generate(tier="L2")
    assert response.status_code == 502
    assert response.json()["detail"] == "ai_providers_failed"
    assert usage_rows(app)[-1]["status"] == "failed"


def test_missing_token_counts_stay_unknown(app, ai):
    generate, _, replies, _ = ai
    replies["openrouter"] = lambda: ok(usage=False)
    data = generate(tier="L1").json()
    assert data["usage"] == {"input_tokens": None, "output_tokens": None}
    assert usage_rows(app)[-1]["input_tokens"] is None


def test_cache_returns_previous_result_without_provider_call(app, ai):
    generate, calls, _, _ = ai
    first = generate(tier="L1", cache=True).json()
    second = generate(tier="L1", cache=True).json()
    assert not first["cache_hit"] and second["cache_hit"]
    assert second["text"] == first["text"] and len(calls) == 1
    assert [r["status"] for r in usage_rows(app)] == ["succeeded", "cache_hit"]


def test_image_requests_skip_providers_without_vision(app, ai):
    generate, calls, _, gateway = ai
    gateway.providers["L2"] = replace(gateway.providers["L2"], vision_model="")
    image = "data:image/png;base64,iVBORw0KGgo="
    data = generate(tier="L2", images=[image]).json()
    assert data["attempts"][0] == {"slot": "L2", "provider": "groq", "result": "no_vision"}
    name, sent, _ = calls[0]
    assert name == "openrouter"
    assert sent["messages"][-1]["content"][1] == {"type": "image_url", "image_url": {"url": image}}


def test_rejects_non_image_references(app, ai):
    generate, _, _, _ = ai
    assert generate(images=["file:///etc/passwd"]).status_code == 422


def test_usage_summary_is_admin_only(app, ai):
    generate, _, _, _ = ai
    generate(tier="L1")
    client, _ = login(app)
    assert client.get("/api/admin/ai/usage").status_code == 403
    client, _ = login(app, role="admin")
    data = client.get("/api/admin/ai/usage").json()
    assert data["totals"]["requests"] == 1 and data["by_provider"][0]["provider"] == "openrouter"
    status = client.get("/api/admin/ai/status").json()
    assert [p["slot"] for p in status["providers"]] == ["L1", "L2", "L3", "FALLBACK"]
    assert "or-key" not in json.dumps(status)


def test_platform_key_access_and_service_tag(app, ai):
    _, calls, _, _ = ai
    client = app.state.client
    key = app.state.settings.integration_key
    # Reject when platform key is missing or invalid.
    assert (
        client.post(
            "/api/v1/ai/generate", json={"task_type": "chat.general", "prompt": "hi"}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/ai/generate",
            json={"task_type": "chat.general", "prompt": "hi"},
            headers={"X-Noedaeri-API-Key": "wrong"},
        ).status_code
        == 401
    )

    # Allowed with valid platform key; records service='platform'.
    res = client.post(
        "/api/v1/ai/generate",
        json={"task_type": "chat.general", "prompt": "hi", "tier": "L1"},
        headers={"X-Noedaeri-API-Key": key},
    )
    assert res.status_code == 200
    [row] = [r for r in usage_rows(app) if r["service"] == "platform"]
    assert row["status"] == "succeeded"


def test_web_session_access_and_service_tag(app, ai):
    client, _ = login(app)
    res = client.post(
        "/api/ai/generate",
        json={"task_type": "chat.general", "prompt": "web hi", "tier": "L1"},
    )
    assert res.status_code == 200
    [row] = [r for r in usage_rows(app) if r["service"] == "web"]
    assert row["status"] == "succeeded"
