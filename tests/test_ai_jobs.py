from dataclasses import replace
from uuid import uuid4

import httpx
import pytest


@pytest.fixture
def mock_n8n(monkeypatch):
    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, **kwargs):
            if "timeout" in str(json):
                raise httpx.TimeoutException("mock timeout")
            if "fail" in str(json):
                return httpx.Response(
                    500, request=httpx.Request("POST", url), json={"error": "failed"}
                )

            req_id = json.get("request_id", "req-test")
            task_type = json.get("task_type", "chat.general")
            return httpx.Response(
                200,
                request=httpx.Request("POST", url),
                json={
                    "request_id": req_id,
                    "task_type": task_type,
                    "project": json.get("project", "default"),
                    "environment": json.get("environment", "production"),
                    "status": "completed",
                    "ai_result": f"Answer for {task_type}",
                    "provider": "openrouter",
                    "model": "openrouter/free",
                    "model_tier": "L1",
                    "usage": {
                        "prompt_tokens": 15,
                        "completion_tokens": 25,
                        "total_tokens": 40,
                    },
                },
            )

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)


def setup_ai_settings(app):
    app.state.settings = replace(
        app.state.settings,
        n8n_ai_webhook_url="https://mock-n8n.example/webhook/noedaeri-ai",
        n8n_compute_context_ready=True,
    )
    return app.state.client, app.state.settings


def test_ai_job_sync_execution(app, mock_n8n):
    client, settings = setup_ai_settings(app)
    headers = {"X-Noedaeri-API-Key": settings.integration_key}

    payload = {
        "request_id": f"req-{uuid4()}",
        "task_type": "blog.tags",
        "prompt": "블로그 태그를 생성해줘.",
        "project": "blog",
        "environment": "production",
        "sync": True,
    }
    resp = client.post("/api/v1/ai/jobs", headers=headers, json=payload)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["request_id"] == payload["request_id"]
    assert data["status"] == "succeeded"
    assert data["reused"] is False
    assert data["result"]["ai_result"] == "Answer for blog.tags"
    history = client.get("/api/v1/tasks", headers=headers).json()
    assert history[0]["source"] == "ai"
    assert history[0]["id"] == data["id"]
    assert history[0]["data"]["result"] == data["result"]


def test_n8n_completed_failure_is_not_success_or_uncertain_execution(app, mock_n8n, monkeypatch):
    calls = []

    async def failed_workflow(self, url, json=None, **kwargs):
        calls.append(json["request_id"])
        return httpx.Response(
            200,
            request=httpx.Request("POST", url),
            json={"status": "failed", "error_code": "all_providers_exhausted", "result": None},
        )

    monkeypatch.setattr(httpx.AsyncClient, "post", failed_workflow)
    client, settings = setup_ai_settings(app)
    headers = {"X-Noedaeri-API-Key": settings.integration_key}
    payload = {"request_id": "completed-failure", "task_type": "blog.tags", "prompt": "fixture"}
    response = client.post("/api/v1/ai/jobs", headers=headers, json=payload)
    assert response.status_code == 424
    assert response.json()["detail"] == "n8n_workflow_failed"
    reused = client.post("/api/v1/ai/jobs", headers=headers, json=payload).json()
    assert reused["status"] == "failed" and reused["result"] is None and reused["reused"]
    assert len(calls) == 1
    with app.state.db.connect() as conn:
        row = conn.execute("SELECT state FROM compute_requests WHERE source='ai'").fetchone()
        assert row["state"] == "released"


def test_ai_job_idempotency_and_reused(app, mock_n8n):
    client, settings = setup_ai_settings(app)
    headers = {"X-Noedaeri-API-Key": settings.integration_key}

    req_id = f"req-idem-{uuid4()}"
    payload = {
        "request_id": req_id,
        "task_type": "portfolio.search",
        "prompt": "포트폴리오 내용 검색",
        "project": "portfolio",
        "environment": "production",
        "sync": True,
    }

    # First execution
    resp1 = client.post("/api/v1/ai/jobs", headers=headers, json=payload)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["reused"] is False

    # Second execution with same (project, environment, request_id)
    resp2 = client.post("/api/v1/ai/jobs", headers=headers, json=payload)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["reused"] is True
    assert data2["id"] == data1["id"]
    assert data2["result"] == data1["result"]


def test_ai_job_project_and_env_separation(app, mock_n8n):
    client, settings = setup_ai_settings(app)
    headers = {"X-Noedaeri-API-Key": settings.integration_key}

    common_req_id = f"req-sep-{uuid4()}"
    payload_prod = {
        "request_id": common_req_id,
        "task_type": "document.analyze",
        "prompt": "문서 분석",
        "project": "portfolio",
        "environment": "production",
        "sync": True,
    }
    payload_stage = {
        "request_id": common_req_id,
        "task_type": "document.analyze",
        "prompt": "문서 분석",
        "project": "portfolio",
        "environment": "staging",
        "sync": True,
    }

    resp1 = client.post("/api/v1/ai/jobs", headers=headers, json=payload_prod)
    resp2 = client.post("/api/v1/ai/jobs", headers=headers, json=payload_stage)
    assert resp1.status_code == 200
    assert resp2.status_code == 200
    assert resp1.json()["id"] != resp2.json()["id"]


def test_ai_job_query_and_list(app, mock_n8n):
    client, settings = setup_ai_settings(app)
    headers = {"X-Noedaeri-API-Key": settings.integration_key}

    req_id = f"req-list-{uuid4()}"
    payload = {
        "request_id": req_id,
        "task_type": "ui.render",
        "prompt": "UI 화면 생성",
        "project": "uibuilder",
        "environment": "production",
        "sync": True,
    }
    created = client.post("/api/v1/ai/jobs", headers=headers, json=payload).json()
    job_id = created["id"]

    # Get single job
    get_resp = client.get(f"/api/v1/ai/jobs/{job_id}", headers=headers)
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == job_id

    # List jobs
    list_resp = client.get("/api/v1/ai/jobs?project=uibuilder", headers=headers)
    assert list_resp.status_code == 200
    jobs = list_resp.json()
    assert any(j["id"] == job_id for j in jobs)


def test_ai_usage_query_and_internal_report(app, mock_n8n):
    client, settings = setup_ai_settings(app)
    headers = {"X-Noedaeri-API-Key": settings.integration_key}

    req_id = f"req-usage-{uuid4()}"
    payload = {
        "request_id": req_id,
        "task_type": "code.analyze",
        "prompt": "코드 리뷰",
        "project": "codehub",
        "environment": "production",
        "sync": True,
    }
    client.post("/api/v1/ai/jobs", headers=headers, json=payload)

    # Query usage
    usage_resp = client.get("/api/v1/ai/usage?project=codehub", headers=headers)
    assert usage_resp.status_code == 200
    data = usage_resp.json()
    assert "summary" in data
    assert "records" in data
    assert any(r["request_id"] == req_id for r in data["records"])
    summary_item = next(s for s in data["summary"] if s["task_type"] == "code.analyze")
    assert summary_item["total_tokens"] >= 40

    # Test internal usage endpoint
    int_headers = {"X-Worker-Key": settings.worker_key}
    report_payload = {
        "request_id": f"req-int-{uuid4()}",
        "task_type": "chat.general",
        "provider": "google",
        "model": "models/gemini-3.8-flash",
        "project": "internal_test",
        "environment": "production",
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
        "model_tier": "L3",
    }
    int_resp = client.post("/internal/ai/usage", headers=int_headers, json=report_payload)
    assert int_resp.status_code == 201
    assert int_resp.json() == {"accepted": True}

    # Duplicate internal report should be ignored without error
    int_resp2 = client.post("/internal/ai/usage", headers=int_headers, json=report_payload)
    assert int_resp2.status_code == 201


def test_ai_job_auth_required(app):
    client = app.state.client
    # Missing key
    resp = client.post(
        "/api/v1/ai/jobs", json={"request_id": "test", "task_type": "test", "prompt": "test"}
    )
    assert resp.status_code == 401
