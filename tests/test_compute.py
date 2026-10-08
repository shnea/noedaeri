import asyncio
from uuid import uuid4

import pytest
from conftest import login
from fastapi import HTTPException

from noedaeri.compute import Compute
from noedaeri.execution import native_compute_lock
from noedaeri.integration import PLATFORM_OWNER


def scheduler(app):
    _, owner = login(app, role="admin")
    return Compute(app.state.db, app.state.settings.storage_root), owner


def test_global_admission_fifo_and_lost_waiter(app):
    compute, owner = scheduler(app)
    with compute.ticket("media", uuid4(), "tts.synthesize", owner) as first:
        assert first.try_start()
        with compute.ticket("operation", uuid4(), "raya.route", owner) as second:
            with compute.ticket("indexing", uuid4(), "indexing.documents", owner) as third:
                assert not second.try_start()
                assert not third.try_start()
                first.close()
                # Ticket ownership closes its connection after the execution context exits.
                first.conn.close()
                assert not third.try_start()
                second.conn.close()  # A queued owner died; it cannot block subsequent admission.
                assert third.try_start()
                states = {item["id"]: item["state"] for item in compute.snapshot()}
                assert states[third.id] == "running"


def test_workflow_child_borrows_parent_but_serializes_siblings(app):
    compute, owner = scheduler(app)
    with compute.ticket("ai", uuid4(), "ai.workflow", owner) as parent:
        assert parent.try_start()
        with compute.ticket("media", uuid4(), "video.package", owner) as unrelated:
            assert not unrelated.try_start()
            with compute.ticket("operation", uuid4(), "raya.route", owner, parent.token) as child:
                assert child.try_start()  # It bypasses the root queue, not other children.
                with compute.ticket(
                    "operation", uuid4(), "indexing.search", owner, parent.token
                ) as sibling:
                    assert not sibling.try_start()
                    child.close()
                    child.conn.close()
                    assert sibling.try_start()
            assert not unrelated.try_start()
    with pytest.raises(HTTPException, match="compute_context_expired"):
        with compute.ticket("operation", uuid4(), "raya.route", owner, parent.token):
            pass


def test_restart_blocks_unknown_execution_and_os_lock_prevents_release(app):
    compute, owner = scheduler(app)
    with compute.ticket("ai", uuid4(), "ai.workflow", owner) as orphan:
        assert orphan.try_start()
        orphan.conn.close()  # Simulate process/database ownership loss without a finish report.
    rows = compute.snapshot()
    assert rows[0]["state"] == "interrupted"
    with compute.ticket("media", uuid4(), "tts.synthesize", owner) as next_job:
        assert not next_job.try_start()
        with native_compute_lock(compute.root):
            with pytest.raises(HTTPException, match="compute_process_still_running"):
                compute.acknowledge_stopped(orphan.id)
        compute.acknowledge_stopped(orphan.id)
        assert next_job.try_start()


def test_wait_timeout_cleans_ticket_without_releasing_other_owner(app):
    compute, owner = scheduler(app)

    async def wait():
        with pytest.raises(HTTPException, match="compute_wait_timeout"):
            async with compute.slot("media", uuid4(), "image.package", owner, 0):
                pytest.fail("blocked task entered execution")

    with compute.ticket("ai", uuid4(), "ai.workflow", owner) as active:
        assert active.try_start()
        asyncio.run(wait())
        assert [(row["id"], row["state"]) for row in compute.snapshot()] == [(active.id, "running")]


def test_scheduler_authorization_and_secret_redaction(app):
    compute, owner = scheduler(app)
    client = app.state.client
    with compute.ticket("ai", uuid4(), "ai.workflow", owner) as active:
        assert active.try_start()
        response = client.get("/api/compute")
        assert response.status_code == 200
        assert active.token not in response.text
        assert "token_hash" not in response.text
        assert (
            client.post(f"/api/admin/compute/{active.id}/acknowledge-stopped", json={}).status_code
            == 422
        )
    client, _ = login(app)
    assert client.get("/api/compute").json()["requests"] == []
    assert (
        client.post(
            f"/api/admin/compute/{active.id}/acknowledge-stopped", json={"execution_stopped": True}
        ).status_code
        == 403
    )


def test_internal_continuation_cannot_bypass_platform_auth(app):
    compute, owner = scheduler(app)
    client = app.state.client
    with compute.ticket("ai", uuid4(), "ai.workflow", owner) as active:
        assert active.try_start()
        body = {"task_type": "chat.general", "prompt": "hello"}
        headers = {"X-Noedaeri-Compute-Token": active.token}
        assert client.post("/api/ai/v1/raya/route", headers=headers, json=body).status_code == 401
        headers["X-Noedaeri-API-Key"] = app.state.settings.integration_key
        assert client.post("/api/v1/ai/raya/route", headers=headers, json=body).status_code == 403
        headers = {
            "X-Noedaeri-Raya-Key": app.state.settings.raya_key,
            "X-Noedaeri-Compute-Token": "expired-fixture-token",
        }
        assert client.post("/api/ai/v1/raya/route", headers=headers, json=body).status_code == 409


def test_n8n_continuation_enters_internal_api_without_deadlock(app, monkeypatch):
    import httpx

    from noedaeri import ai_jobs
    from noedaeri.compute import compute_context

    settings = app.state.settings
    compute = app.state.compute
    seen = []

    async def workflow(current, data):
        lease = compute_context.get()
        seen.append(lease.id)
        with compute.ticket("media", uuid4(), "image.package", PLATFORM_OWNER) as media:
            assert not media.try_start()
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://testserver"
            ) as caller:
                response = await caller.post(
                    "/api/ai/v1/indexing/search",
                    headers={
                        "X-Noedaeri-Raya-Key": current.raya_key,
                        "X-Noedaeri-Compute-Token": lease.token,
                    },
                    json={"query": "fixture", "collection": "empty"},
                )
            assert response.status_code == 200, response.text
            assert (
                len(
                    [
                        r
                        for r in compute.snapshot()
                        if r["state"] == "running" and r["parent_id"] is None
                    ]
                )
                == 1
            )
        await asyncio.sleep(0.01)
        return {"ai_result": "mock answer"}

    monkeypatch.setattr(ai_jobs, "run_n8n_workflow", workflow)

    async def execute():
        responses = await asyncio.wait_for(
            asyncio.gather(
                *(
                    ai_jobs.execute_or_reuse_ai_job(
                        app.state.db,
                        settings,
                        PLATFORM_OWNER,
                        ai_jobs.AiJobCreate(
                            request_id=f"nested-{n}", task_type="chat.general", prompt="x"
                        ),
                    )
                    for n in range(2)
                )
            ),
            5,
        )
        assert all(r["status"] == "succeeded" for r in responses)

    asyncio.run(execute())
    assert len(set(seen)) == 2
    assert compute.snapshot() == []


def test_cancelled_workflow_discards_late_result(app, monkeypatch):
    from noedaeri import ai_jobs

    compute, owner = scheduler(app)
    called = 0

    async def execute():
        nonlocal called
        started, finish = asyncio.Event(), asyncio.Event()

        async def workflow(settings, data):
            nonlocal called
            called += 1
            started.set()
            await finish.wait()
            return {"ai_result": "late result"}

        monkeypatch.setattr(ai_jobs, "run_n8n_workflow", workflow)
        task = asyncio.create_task(
            ai_jobs.execute_or_reuse_ai_job(
                app.state.db,
                app.state.settings,
                owner,
                ai_jobs.AiJobCreate(
                    request_id="cancel-running", task_type="chat.general", prompt="x"
                ),
            )
        )
        await asyncio.wait_for(started.wait(), 5)
        with app.state.db.connect() as conn:
            job = conn.execute(
                "SELECT id FROM ai_jobs WHERE request_id='cancel-running'"
            ).fetchone()
        response = app.state.client.post(f"/api/ai/jobs/{job['id']}/cancel")
        assert response.status_code == 200
        with compute.ticket("media", uuid4(), "tts.synthesize", owner) as waiting:
            assert not waiting.try_start()  # Cancellation alone does not release execution.
        finish.set()
        result = await task
        assert result["status"] == "cancelled"
        assert result["result"] is None
        repeated = await ai_jobs.execute_or_reuse_ai_job(
            app.state.db,
            app.state.settings,
            owner,
            ai_jobs.AiJobCreate(request_id="cancel-running", task_type="chat.general", prompt="x"),
        )
        assert repeated["reused"] and repeated["status"] == "cancelled"

    asyncio.run(execute())
    assert called == 1


def test_timeout_blocks_slot_and_failed_request_is_not_replayed(app, monkeypatch):
    from noedaeri import ai_jobs

    compute, owner = scheduler(app)

    async def timeout(settings, data):
        raise HTTPException(504, "ai_execution_timeout")

    monkeypatch.setattr(ai_jobs, "run_n8n_workflow", timeout)
    data = ai_jobs.AiJobCreate(request_id="timeout", task_type="chat.general", prompt="x")
    with pytest.raises(HTTPException, match="ai_execution_timeout"):
        asyncio.run(ai_jobs.execute_or_reuse_ai_job(app.state.db, app.state.settings, owner, data))
    rows = compute.snapshot()
    assert rows[0]["state"] == "interrupted"
    result = asyncio.run(
        ai_jobs.execute_or_reuse_ai_job(app.state.db, app.state.settings, owner, data)
    )
    assert result["reused"] and result["status"] == "failed"


def test_n8n_without_forwarding_token_fails_promptly(app):
    compute, owner = scheduler(app)
    with compute.ticket("ai", uuid4(), "ai.workflow", owner) as parent:
        assert parent.try_start()
        response = app.state.client.post(
            "/api/ai/v1/indexing/search",
            json={"query": "fixture"},
            headers={"X-Noedaeri-Raya-Key": app.state.settings.raya_key},
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "compute_context_required"


def test_unprepared_n8n_is_not_called_or_shown_as_available(app, monkeypatch):
    from dataclasses import replace

    from noedaeri import ai_jobs

    app.state.settings = replace(
        app.state.settings,
        n8n_ai_webhook_url="https://workflow.example/ai",
    )
    data = ai_jobs.AiJobCreate(request_id="unprepared", task_type="chat.general", prompt="x")
    with pytest.raises(HTTPException, match="n8n_compute_context_not_ready"):
        asyncio.run(ai_jobs.run_n8n_workflow(app.state.settings, data))
    client, _ = login(app)
    catalog = client.get("/api/services").json()
    workflow = next(s for s in catalog if s["service"] == "n8n")
    assert not workflow["available"]
    assert workflow["unavailable_reason"]


def test_offline_n8n_patch_preserves_export_and_credentials():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("prepare", Path("scripts/prepare_n8n_compute.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    export = {
        "nodes": [
            {"name": "웹훅 접수"},
            *(
                {
                    "name": name,
                    "type": "n8n-nodes-base.httpRequest",
                    "credentials": {"httpHeaderAuth": {"id": "fixture"}},
                    "parameters": {"authentication": "genericCredentialType"},
                }
                for name in module.HTTP_STEPS
            ),
            {
                "name": "요청 정규화",
                "type": "n8n-nodes-base.code",
                "parameters": {"jsCode": "return $input.all();"},
            },
        ]
    }
    prepared = module.prepare(export)
    for original, node in zip(export["nodes"][1:3], prepared["nodes"][1:3], strict=True):
        assert node["credentials"] == original["credentials"]
        assert "sendHeaders" not in original["parameters"]
        assert node["parameters"]["headerParameters"]["parameters"] == [
            {"name": module.HEADER, "value": module.EXPRESSION}
        ]
    assert module.prepare(prepared) == prepared


def test_prepared_normalizer_keeps_request_and_pairing_but_omits_compute_token():
    import importlib.util
    import json
    import subprocess
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("prepare", Path("scripts/prepare_n8n_compute.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    export = {
        "nodes": [
            {"name": "웹훅 접수"},
            *(
                {"name": name, "type": "n8n-nodes-base.httpRequest", "parameters": {}}
                for name in module.HTTP_STEPS
            ),
            {
                "name": "요청 정규화",
                "type": "n8n-nodes-base.code",
                "parameters": {"jsCode": "return $input.all();"},
            },
        ],
        "settings": {"executionOrder": "v1"},
    }
    prepared = module.prepare(export)
    code = prepared["nodes"][-1]["parameters"]["jsCode"]
    runner = (
        "const fs=require('fs'); const {code,input}=JSON.parse(fs.readFileSync(0,'utf8'));"
        "const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;"
        "new AsyncFunction('$input',code)({all:()=>input})"
        ".then(r=>process.stdout.write(JSON.stringify(r)));"
    )
    item = {
        "json": {"prompt": "fixture", "compute_context": {"token": "short-lived-secret"}},
        "pairedItem": {"item": 0},
    }
    result = subprocess.run(
        ["node", "-e", runner],
        input=json.dumps({"code": code, "input": [item]}),
        text=True,
        capture_output=True,
        check=True,
    )
    assert json.loads(result.stdout) == [{"json": {"prompt": "fixture"}, "pairedItem": {"item": 0}}]
    assert export["nodes"][-1]["parameters"]["jsCode"] == "return $input.all();"
    assert prepared["settings"]["executionOrder"] == "v1"
    assert all(prepared["settings"][key] == val for key, val in module.EXECUTION_SETTINGS.items())
