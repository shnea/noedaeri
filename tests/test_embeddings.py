import asyncio
from dataclasses import replace

import pytest
from conftest import login

from noedaeri.embeddings import (
    generate_embeddings,
)


@pytest.fixture
def mock_gemini(monkeypatch):
    import httpx

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None, **kwargs):
            if "fail" in str(json):
                return httpx.Response(
                    500, request=httpx.Request("POST", url), json={"error": "failed"}
                )
            if "batchEmbedContents" in url:
                reqs = json.get("requests", [])
                dim = reqs[0].get("outputDimensionality", 768) if reqs else 768
                embeddings = [{"values": [0.1] * dim} for _ in reqs]
                return httpx.Response(
                    200, request=httpx.Request("POST", url), json={"embeddings": embeddings}
                )
            else:
                dim = json.get("outputDimensionality", 768)
                return httpx.Response(
                    200,
                    request=httpx.Request("POST", url),
                    json={"embedding": {"values": [0.1] * dim}},
                )

    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)


def test_generate_embeddings_direct(mock_gemini):
    embs, tokens = asyncio.run(
        generate_embeddings("fake_key", ["hello world"], "models/gemini-embedding-001", 768)
    )
    assert len(embs) == 1
    assert len(embs[0]) == 768
    assert tokens == 2

    embs, tokens = asyncio.run(
        generate_embeddings("fake_key", ["hello", "world"], "models/gemini-embedding-001", 512)
    )
    assert len(embs) == 2
    assert len(embs[0]) == 512
    assert len(embs[1]) == 512


def test_embedding_api_auth_and_validation(app, mock_gemini):
    client = app.state.client
    app.state.settings = replace(
        app.state.settings,
        gemini_api_key="fake-gemini-key",
    )

    # 1. Without auth -> 401
    assert client.post("/api/v1/ai/embeddings", json={"input": "test"}).status_code == 401
    assert client.post("/api/ai/v1/embeddings", json={"input": "test"}).status_code == 401
    assert client.post("/api/ai/embeddings", json={"input": "test"}).status_code == 401

    # 2. Platform auth
    platform_headers = {"X-Noedaeri-API-Key": app.state.settings.integration_key}
    resp = client.post(
        "/api/v1/ai/embeddings", json={"input": "단일 텍스트"}, headers=platform_headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["model"] == "models/gemini-embedding-001"
    assert data["dimensions"] == 768
    assert len(data["data"]) == 1
    assert len(data["data"][0]["embedding"]) == 768

    history = client.get("/api/v1/tasks", headers=platform_headers).json()
    assert history[0]["kind"] == "embedding.encode"
    assert history[0]["status"] == "succeeded"
    assert history[0]["data"]["result"]["vector_count"] == 1
    assert "data" not in history[0]["data"]["result"]

    # Batch test via platform
    resp = client.post(
        "/api/v1/ai/embeddings",
        json={"input": ["첫 번째", "두 번째"], "dimensions": 256},
        headers=platform_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dimensions"] == 256
    assert len(data["data"]) == 2
    assert len(data["data"][0]["embedding"]) == 256

    # 3. AI / Raya Key auth
    ai_headers = {"X-Noedaeri-Raya-Key": app.state.settings.raya_key}
    resp = client.post(
        "/api/ai/v1/embeddings", json={"input": "Raya 키 테스트"}, headers=ai_headers
    )
    assert resp.status_code == 200

    # 4. User session auth
    user_client, _ = login(app)
    resp = user_client.post("/api/ai/embeddings", json={"input": "세션 테스트"})
    assert resp.status_code == 200

    # 5. Validation errors
    bad_req = client.post("/api/v1/ai/embeddings", json={"input": ""}, headers=platform_headers)
    assert bad_req.status_code == 422

    # Oversized payload
    big_req = client.post("/api/v1/ai/embeddings", content=b"x" * 300000, headers=platform_headers)
    assert big_req.status_code == 413
