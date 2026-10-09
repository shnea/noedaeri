from dataclasses import replace
from uuid import uuid4

from conftest import login
from psycopg.types.json import Jsonb

from noedaeri.integration import PLATFORM_OWNER
from noedaeri.tasks import cleanup_operation_results


def seed_work(app, owner):
    ids = [uuid4() for _ in range(4)]
    with app.state.db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs(id,owner_id,idempotency_key,kind,service,title,status,created_at) "
            "VALUES(%s,%s,%s,'video.thumbnail','ffmpeg','영상 작업','queued',"
            "now()-interval '4 minutes')",
            (ids[0], owner, uuid4()),
        )
        conn.execute(
            "INSERT INTO ai_jobs(id,owner_id,request_id,task_type,prompt,status,created_at) "
            "VALUES(%s,%s,%s,'blog.summary','sample','succeeded',now()-interval '3 minutes')",
            (ids[1], owner, str(uuid4())),
        )
        conn.execute(
            "INSERT INTO ai_indexing_jobs(id,owner_id,request_id,mode,status,created_at) "
            "VALUES(%s,%s,%s,'upsert','running',now()-interval '2 minutes')",
            (ids[2], owner, str(uuid4())),
        )
        conn.execute(
            "INSERT INTO operation_jobs(id,owner_id,kind,title,status,created_at) "
            "VALUES(%s,%s,'embedding.encode','임베딩 작업','succeeded',now()-interval '1 minute')",
            (ids[3], owner),
        )
    return ids


def test_unified_tasks_order_filters_pagination_and_ownership(app):
    client, owner = login(app)
    own = seed_work(app, owner)
    other = seed_work(app, PLATFORM_OWNER)
    response = client.get("/api/tasks")
    assert response.status_code == 200, response.text
    tasks = response.json()
    assert [row["id"] for row in tasks] == [str(value) for value in reversed(own)]
    assert {row["source"] for row in tasks} == {"media", "ai", "indexing", "operation"}
    assert all(row["owner_id"] == str(owner) for row in tasks)
    assert not set(str(value) for value in other) & {row["id"] for row in tasks}
    assert client.get("/api/tasks?service=n8n").json()[0]["id"] == str(own[1])
    assert len(client.get("/api/tasks?status=succeeded").json()) == 2
    assert [row["id"] for row in client.get("/api/tasks?limit=2&offset=2").json()] == [
        str(own[1]),
        str(own[0]),
    ]
    assert client.get("/api/tasks?limit=0").status_code == 422


def test_task_access_gates_and_platform_scope(app):
    client = app.state.client
    assert client.get("/api/tasks").status_code == 401
    client, _ = login(app, status="pending")
    assert client.get("/api/tasks").status_code == 403
    client, owner = login(app)
    seed_work(app, owner)
    platform_ids = seed_work(app, PLATFORM_OWNER)
    assert client.get("/api/v1/tasks").status_code == 401
    rows = client.get(
        "/api/v1/tasks", headers={"X-Noedaeri-API-Key": app.state.settings.integration_key}
    ).json()
    assert {row["id"] for row in rows} == {str(value) for value in platform_ids}
    client, _ = login(app, role="admin")
    assert any(row["origin"] == "platform" for row in client.get("/api/tasks").json())


def test_catalog_contains_every_implemented_service_and_configuration_state(app, monkeypatch):
    monkeypatch.setattr("noedaeri.services.runtime_ready", lambda: True)
    client, _ = login(app)
    rows = {row["kind"]: row for row in client.get("/api/services").json()}
    assert {
        "image.package",
        "tts.synthesize",
        "tts.voice.register",
        "video.package",
        "video.thumbnail",
        "stt.transcribe",
        "ocr.recognize",
        "pdf.extract",
        "ai.workflow",
        "raya.route",
        "embedding.encode",
        "indexing.documents",
        "indexing.search",
    } == set(rows)
    assert rows["ai.workflow"]["available"] is False
    assert rows["embedding.encode"]["available"] is False
    app.state.settings = replace(
        app.state.settings,
        n8n_ai_webhook_url="https://workflow.example/ai",
        n8n_compute_context_ready=True,
        gemini_api_key="fixture-key",
        raya_enabled=True,
        tts_enabled=True,
        stt_enabled=True,
        ocr_enabled=True,
    )
    rows = {row["kind"]: row for row in client.get("/api/services").json()}
    assert all(row["available"] for row in rows.values())
    assert "blog.summary" in {row["value"] for row in rows["ai.workflow"]["task_types"]}
    # Catalog entries do not accidentally become accepted media worker jobs.
    assert (
        client.post(
            "/api/jobs",
            json={"kind": "ai.workflow", "title": "AI", "idempotency_key": str(uuid4())},
        ).status_code
        == 422
    )


def test_direct_search_and_failed_embedding_appear_in_work_history(app):
    client, _ = login(app)
    response = client.post("/api/ai/indexing/search", json={"query": "sample"})
    assert response.status_code == 200, response.text
    assert response.json()["results"] == []
    assert client.post("/api/ai/embeddings", json={"input": "sample"}).status_code == 503
    rows = client.get("/api/tasks").json()
    assert {row["kind"] for row in rows} == {"indexing.search", "embedding.encode"}
    assert next(row for row in rows if row["kind"] == "embedding.encode")["status"] == "failed"
    assert (
        next(row for row in rows if row["kind"] == "indexing.search")["data"]["result"][
            "matched_count"
        ]
        == 0
    )
    before = len(rows)
    client.cookies.clear()
    assert client.post("/api/ai/embeddings", json={"input": "sample"}).status_code == 401
    with app.state.db.connect() as conn:
        assert (
            conn.execute("SELECT count(*) AS total FROM operation_jobs").fetchone()["total"]
            == before
        )


def test_expired_operation_results_are_hidden_and_cleaned_without_removing_history(app):
    client, owner = login(app)
    work = seed_work(app, owner)
    task = work[-1]
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE operation_jobs SET result=%s,expires_at=now()-interval '1 second' WHERE id=%s",
            (Jsonb({"sample": "expired"}), task),
        )
        conn.execute(
            "UPDATE ai_jobs SET result=%s,expires_at=now()-interval '1 second' WHERE id=%s",
            (Jsonb({"sample": "expired"}), work[1]),
        )
    rows = client.get("/api/tasks").json()
    assert next(row for row in rows if row["id"] == str(work[1]))["data"]["result"] is None
    row = next(row for row in rows if row["id"] == str(task))
    assert row["data"]["result"] is None
    cleanup_operation_results(app.state.db)
    with app.state.db.connect() as conn:
        row = conn.execute("SELECT result FROM operation_jobs WHERE id=%s", (task,)).fetchone()
        assert row is not None and row["result"] is None
