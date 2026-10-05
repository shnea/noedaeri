from dataclasses import replace

import httpx
import pytest
from conftest import login

from noedaeri.ai_indexing import cosine_similarity


@pytest.fixture
def mock_gemini_indexing(monkeypatch):
    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, **kwargs):
            if "batchEmbedContents" in url:
                reqs = json.get("requests", [])
                dim = reqs[0].get("outputDimensionality", 768) if reqs else 768
                embeddings = []
                for i, _r in enumerate(reqs):
                    # make slightly distinct vectors
                    vec = [0.01 * (i + 1)] * dim
                    embeddings.append({"values": vec})
                return httpx.Response(
                    200, request=httpx.Request("POST", url), json={"embeddings": embeddings}
                )
            else:
                dim = json.get("outputDimensionality", 768)
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={"embedding": {"values": [0.05] * dim}},
                )

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)


def setup_settings(app):
    app.state.settings = replace(
        app.state.settings,
        gemini_api_key="mock_gemini_key",
        integration_key="valid-platform-key",
    )
    return app.state.client, app.state.settings


def test_cosine_similarity():
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    assert round(cosine_similarity(v1, v2), 4) == 1.0

    v3 = [0.0, 1.0, 0.0]
    assert round(cosine_similarity(v1, v3), 4) == 0.0

    v4 = [0.5, 0.5, 0.0]
    assert cosine_similarity(v1, v4) > 0.0


def test_indexing_upsert_and_search(app, mock_gemini_indexing):
    client, settings = setup_settings(app)

    # 1. Ingest 2 documents
    req_body = {
        "request_id": "idx-req-001",
        "project": "portfolio-test",
        "environment": "production",
        "collection": "portfolio",
        "mode": "upsert",
        "documents": [
            {
                "id": "career-1",
                "title": "Backend Engineering",
                "content": "Python FastAPI and PostgreSQL architecture design.",
                "metadata": {"category": "career", "year": 2026},
            },
            {
                "id": "career-2",
                "title": "Frontend React Development",
                "content": "Modern React and UI/UX design with TypeScript.",
                "metadata": {"category": "career", "year": 2025},
            },
        ],
        "sync": True,
    }

    res = client.post(
        "/api/v1/ai/indexing",
        json=req_body,
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["request_id"] == "idx-req-001"
    assert data["status"] == "succeeded"
    assert data["indexed_count"] == 2
    assert data["mode"] == "upsert"

    # 2. Idempotency test
    reuse_res = client.post(
        "/api/v1/ai/indexing",
        json=req_body,
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    assert reuse_res.status_code == 200
    assert reuse_res.json().get("reused") is True

    # 3. Vector search
    search_res = client.post(
        "/api/v1/ai/indexing/search",
        json={
            "query": "Backend Python FastAPI",
            "collection": "portfolio",
            "project": "portfolio-test",
            "environment": "production",
            "limit": 5,
        },
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    assert search_res.status_code == 200
    search_data = search_res.json()
    assert search_data["collection"] == "portfolio"
    assert len(search_data["results"]) == 2
    assert search_data["results"][0]["document_id"] in ["career-1", "career-2"]


def test_indexing_delete_mode(app, mock_gemini_indexing):
    client, settings = setup_settings(app)

    # Ingest document
    client.post(
        "/api/v1/ai/indexing",
        json={
            "request_id": "idx-req-del-init",
            "project": "del-test",
            "environment": "production",
            "collection": "docs",
            "mode": "upsert",
            "documents": [
                {"id": "doc-to-delete", "content": "This will be deleted"},
                {"id": "doc-to-keep", "content": "This will remain"},
            ],
        },
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )

    # Delete mode
    del_res = client.post(
        "/api/v1/ai/indexing",
        json={
            "request_id": "idx-req-del-exec",
            "project": "del-test",
            "environment": "production",
            "collection": "docs",
            "mode": "delete",
            "delete_ids": ["doc-to-delete"],
        },
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    assert del_res.status_code == 200
    del_data = del_res.json()
    assert del_data["status"] == "succeeded"
    assert del_data["deleted_count"] == 1

    # Verify via search
    s_res = client.post(
        "/api/v1/ai/indexing/search",
        json={
            "query": "query",
            "collection": "docs",
            "project": "del-test",
            "environment": "production",
        },
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    docs = [r["document_id"] for r in s_res.json()["results"]]
    assert "doc-to-delete" not in docs
    assert "doc-to-keep" in docs


def test_indexing_replace_all_mode(app, mock_gemini_indexing):
    client, settings = setup_settings(app)

    # Initial docs
    client.post(
        "/api/v1/ai/indexing",
        json={
            "request_id": "idx-req-replace-init",
            "project": "replace-test",
            "environment": "production",
            "collection": "blog",
            "mode": "upsert",
            "documents": [
                {"id": "old-1", "content": "Old post 1"},
                {"id": "old-2", "content": "Old post 2"},
            ],
        },
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )

    # Replace all
    replace_res = client.post(
        "/api/v1/ai/indexing",
        json={
            "request_id": "idx-req-replace-exec",
            "project": "replace-test",
            "environment": "production",
            "collection": "blog",
            "mode": "replace_all",
            "documents": [
                {"id": "new-1", "content": "Completely new article"},
            ],
        },
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    assert replace_res.status_code == 200
    r_data = replace_res.json()
    assert r_data["deleted_count"] == 2
    assert r_data["indexed_count"] == 1

    # Verify search
    s_res = client.post(
        "/api/v1/ai/indexing/search",
        json={
            "query": "article",
            "collection": "blog",
            "project": "replace-test",
            "environment": "production",
        },
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    docs = [r["document_id"] for r in s_res.json()["results"]]
    assert docs == ["new-1"]


def test_indexing_collections_and_job_query(app, mock_gemini_indexing):
    client, settings = setup_settings(app)

    # Ingest
    res = client.post(
        "/api/v1/ai/indexing",
        json={
            "request_id": "idx-req-overview",
            "project": "overview-test",
            "environment": "production",
            "collection": "faq",
            "mode": "upsert",
            "documents": [
                {"id": "faq-1", "content": "How to use noedaeri?"},
            ],
        },
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    job_id = res.json()["id"]

    # 1. Single Job Query
    job_res = client.get(
        f"/api/v1/ai/indexing/{job_id}",
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    assert job_res.status_code == 200
    assert job_res.json()["id"] == job_id

    # 2. List Jobs Query
    list_res = client.get(
        "/api/v1/ai/indexing?collection=faq",
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    assert list_res.status_code == 200
    assert any(j["id"] == job_id for j in list_res.json())

    # 3. Collections Overview
    cols_res = client.get(
        "/api/v1/ai/indexing/collections",
        headers={"X-Noedaeri-API-Key": "valid-platform-key"},
    )
    assert cols_res.status_code == 200
    cols = {c["collection"]: c for c in cols_res.json()}
    assert "faq" in cols
    assert cols["faq"]["document_count"] >= 1


def test_indexing_auth_checks(app):
    client, settings = setup_settings(app)

    # 401 without key on v1
    res = client.post(
        "/api/v1/ai/indexing",
        json={"request_id": "auth-test", "documents": []},
    )
    assert res.status_code == 401

    # 401 without session on web route
    web_res = client.post(
        "/api/ai/indexing",
        json={"request_id": "auth-test", "documents": []},
    )
    assert web_res.status_code == 401


def ingest(client, request_id="request", path="/api/ai/indexing", **kwargs):
    return client.post(
        path,
        json={
            "request_id": request_id,
            "documents": [{"id": "shared-id", "content": "A searchable document"}],
            **kwargs,
        },
    )


def test_owner_and_scope_isolation(app, mock_gemini_indexing):
    client, _ = setup_settings(app)
    client, first_owner = login(app)
    first = ingest(client).json()
    client, second_owner = login(app)
    second = ingest(client).json()
    assert first["status"] == second["status"] == "succeeded"
    assert first["owner_id"] == str(first_owner)
    assert second["owner_id"] == str(second_owner)
    assert client.get(f"/api/ai/indexing/{first['id']}").status_code == 404
    assert len(client.get("/api/ai/indexing").json()) == 1
    cols = client.get("/api/ai/indexing/collections").json()
    assert len(cols) == 1 and cols[0]["owner_id"] == str(second_owner)
    assert (
        ingest(client, "delete", mode="delete", documents=[], delete_ids=["shared-id"]).json()[
            "deleted_count"
        ]
        == 1
    )
    with app.state.db.connect() as conn:
        assert (
            conn.execute("SELECT owner_id FROM ai_documents").fetchone()["owner_id"] == first_owner
        )
        assert conn.execute("SELECT COUNT(*) AS count FROM ai_usage").fetchone()["count"] == 2
    headers = {"X-Noedaeri-API-Key": "valid-platform-key"}
    assert client.get(f"/api/v1/ai/indexing/{first['id']}", headers=headers).status_code == 404
    assert client.get("/api/v1/ai/indexing/collections", headers=headers).json() == []
    assert client.post("/api/ai/indexing/search", json={"query": "query"}).json()["results"] == []
    client, _ = login(app, role="admin")
    assert len(client.get("/api/ai/indexing").json()) == 3
    assert client.get(f"/api/ai/indexing/{first['id']}").status_code == 200


def test_replace_failure_preserves_documents_and_sanitizes_error(
    app, mock_gemini_indexing, monkeypatch
):
    from noedaeri import ai_indexing

    client, _ = setup_settings(app)
    client, _ = login(app)
    assert ingest(client).json()["status"] == "succeeded"

    async def failing_provider(*args):
        raise RuntimeError("https://private.example/?key=secret-value")

    monkeypatch.setattr(ai_indexing, "generate_embeddings", failing_provider)
    response = ingest(
        client,
        "replacement",
        mode="replace_all",
        documents=[{"id": "new", "content": "replacement"}],
    )
    assert response.json()["status"] == "failed"
    assert "secret-value" not in response.text
    assert response.json()["error_code"] == "indexing_execution_failed"
    with app.state.db.connect() as conn:
        assert (
            conn.execute("SELECT document_id FROM ai_documents").fetchone()["document_id"]
            == "shared-id"
        )
    assert ingest(
        client,
        "replacement",
        mode="replace_all",
        documents=[{"id": "new", "content": "replacement"}],
    ).json()["reused"]


def test_concurrent_retries_call_embedding_once(app, monkeypatch):
    import asyncio
    from uuid import UUID

    from noedaeri import ai_indexing

    _, settings = setup_settings(app)
    _, owner = login(app)
    calls = 0

    async def check():
        started, release = asyncio.Event(), asyncio.Event()

        async def provider(*args):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return [[0.1] * 768], 3

        monkeypatch.setattr(ai_indexing, "generate_embeddings", provider)
        data = ai_indexing.IndexingJobCreate(
            request_id="concurrent", documents=[{"id": "doc", "content": "text"}]
        )
        initial = asyncio.create_task(
            ai_indexing.execute_indexing_job(app.state.db, settings, owner, data)
        )
        await started.wait()
        retry = await ai_indexing.execute_indexing_job(app.state.db, settings, owner, data)
        assert retry["reused"] and retry["status"] == "running"
        worker = await ai_indexing.run_indexing_job(app.state.db, settings, UUID(retry["id"]))
        assert worker["status"] == "running"
        release.set()
        completed = await initial
        assert completed["status"] == "succeeded"
        assert completed["id"] == retry["id"]

    asyncio.run(check())
    assert calls == 1


def test_request_conflict_validation_and_limits(app, mock_gemini_indexing):
    client, _ = setup_settings(app)
    client, _ = login(app)
    assert ingest(client).json()["status"] == "succeeded"
    assert ingest(client, documents=[{"id": "different", "content": "text"}]).status_code == 409
    assert (
        ingest(
            client,
            "invalid",
            documents=[{"id": "dup", "content": "a"}, {"id": "dup", "content": "b"}],
        ).status_code
        == 422
    )
    assert ingest(client, "invalid", mode="delete", documents=[]).status_code == 422
    assert ingest(client, "invalid", documents=[{"id": "blank", "content": " "}]).status_code == 422
    assert client.post("/api/ai/indexing", content=b"x" * (1024 * 1024 + 1)).status_code == 413
    assert (
        client.post("/api/ai/indexing", json={"request_id": "empty", "documents": []}).status_code
        == 422
    )
    assert (
        ingest(client, "empty-replace", mode="replace_all", documents=[]).json()["deleted_count"]
        == 1
    )


def test_async_recovery_cancel_and_retention(app, mock_gemini_indexing):
    import asyncio
    from uuid import UUID

    from noedaeri.ai_indexing import (
        IndexingJobCreate,
        cleanup_indexing_results,
        execute_indexing_job,
        run_indexing_job,
    )

    client, settings = setup_settings(app)
    client, owner = login(app)
    data = IndexingJobCreate(
        request_id="async", sync=False, documents=[{"id": "doc", "content": "text"}]
    )
    pending = asyncio.run(execute_indexing_job(app.state.db, settings, owner, data))
    assert pending["status"] == "pending" and pending["expires_at"] is None
    completed = asyncio.run(run_indexing_job(app.state.db, settings, UUID(pending["id"])))
    assert completed["status"] == "succeeded"
    from datetime import datetime

    assert (
        86399
        <= (
            datetime.fromisoformat(completed["expires_at"])
            - datetime.fromisoformat(completed["finished_at"])
        ).total_seconds()
        <= 86401
    )
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE ai_indexing_jobs SET expires_at=now()-interval '1 second' WHERE id=%s",
            (pending["id"],),
        )
    cleanup_indexing_results(app.state.db)
    expired = client.get(f"/api/ai/indexing/{pending['id']}").json()
    assert expired["result_state"] == "expired" and expired["result"] is None
    with app.state.db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM ai_documents").fetchone()["n"] == 1
    cancel_data = data.model_copy(update={"request_id": "cancel"})
    pending = asyncio.run(execute_indexing_job(app.state.db, settings, owner, cancel_data))
    assert client.post(f"/api/ai/indexing/{pending['id']}/cancel").json()["status"] == "cancelled"
    assert (
        asyncio.run(run_indexing_job(app.state.db, settings, UUID(pending["id"])))["status"]
        == "cancelled"
    )
    pending = asyncio.run(
        execute_indexing_job(
            app.state.db, settings, owner, data.model_copy(update={"request_id": "interrupted"})
        )
    )
    with app.state.db.connect() as conn:
        conn.execute("UPDATE ai_indexing_jobs SET status='running' WHERE id=%s", (pending["id"],))
    recovered = asyncio.run(run_indexing_job(app.state.db, settings, UUID(pending["id"])))
    assert recovered["status"] == "failed"
    assert recovered["error_code"] == "indexing_execution_interrupted"


def test_openapi_and_pending_approval(app):
    client, _ = setup_settings(app)
    client, _ = login(app, status="pending")
    assert ingest(client).status_code == 403
    assert client.get("/api/ai/indexing/collections").status_code == 403
    client, _ = login(app, role="admin")
    schema = client.get("/integrations/openapi.json").json()
    paths = schema["paths"]
    assert paths["/api/v1/ai/indexing"]["post"]["requestBody"]["content"]["application/json"][
        "schema"
    ]["properties"]["documents"]["items"]["properties"]["content"]
    assert "202" in paths["/api/v1/ai/indexing"]["post"]["responses"]


def test_admin_searches_platform_collection_without_granting_cross_owner_access(
    app, mock_gemini_indexing
):
    from noedaeri.integration import PLATFORM_OWNER

    client, _ = setup_settings(app)
    headers = {"X-Noedaeri-API-Key": "valid-platform-key"}
    assert (
        client.post(
            "/api/v1/ai/indexing",
            json={
                "request_id": "platform",
                "documents": [{"id": "platform-doc", "content": "text"}],
            },
            headers=headers,
        ).json()["status"]
        == "succeeded"
    )
    query = {"query": "text", "owner_id": str(PLATFORM_OWNER)}
    client, own_id = login(app)
    assert client.post("/api/ai/indexing/search", json=query).status_code == 403
    assert (
        client.post(
            "/api/v1/ai/indexing/search",
            json={"query": "text", "owner_id": str(own_id)},
            headers=headers,
        ).status_code
        == 403
    )
    client, _ = login(app, role="admin")
    response = client.post("/api/ai/indexing/search", json=query)
    assert response.status_code == 200
    assert response.json()["owner_id"] == str(PLATFORM_OWNER)
    assert response.json()["results"][0]["document_id"] == "platform-doc"


def test_n8n_execution_key_can_search_platform_index_without_web_session(app, mock_gemini_indexing):
    from dataclasses import replace

    client, settings = setup_settings(app)
    app.state.settings = replace(settings, raya_key="fixture-execution-key")
    body = {"request_id": "internal", "documents": [{"id": "n8n-doc", "content": "text"}]}
    assert client.post("/api/ai/v1/indexing", json=body).status_code == 401
    headers = {"X-Noedaeri-Raya-Key": "fixture-execution-key"}
    indexed = client.post("/api/ai/v1/indexing", json=body, headers=headers)
    assert indexed.status_code == 200
    assert indexed.json()["status"] == "succeeded"
    search = client.post("/api/ai/v1/indexing/search", json={"query": "text"}, headers=headers)
    assert search.status_code == 200
    assert search.json()["results"][0]["document_id"] == "n8n-doc"
