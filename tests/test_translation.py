import copy
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from noedaeri.translation import TranslationOptions, TranslationRequest, translation_result

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from prepare_n8n_translation import NAME, prepare_translation  # noqa: E402


def test_translation_input_and_result_contract():
    options = TranslationOptions(target_language="en")
    request = TranslationRequest(request_id="sample", text="  안녕하세요  ", target_language="en")
    assert request.text == "안녕하세요" and request.source_language == "auto"
    for overrides in (
        {"target_language": "auto"},
        {"text": " "},
        {"text": "x" * 4001},
        {"url": "https://example.invalid"},
    ):
        with pytest.raises(ValidationError):
            TranslationRequest.model_validate(
                {"request_id": "sample", "text": "fixture", "target_language": "en", **overrides}
            )
    assert (
        translation_result({"ai_result": json.dumps({"translated_text": " Hello "})}, options)[
            "translated_text"
        ]
        == "Hello"
    )
    for raw in ("not-json", {}, {"translated_text": " "}, {"translated_text": ["hello"]}):
        assert translation_result({"ai_result": raw}, options) is None


def test_translation_workflow_preserves_branches_and_is_idempotent():
    workflow = {
        "nodes": [
            {
                "name": "작업 종류 분기",
                "type": "n8n-nodes-base.switch",
                "parameters": {
                    "rules": {
                        "values": [
                            {
                                "conditions": {
                                    "conditions": [
                                        {
                                            "leftValue": "={{ $json.task_type }}",
                                            "rightValue": "chat.general",
                                            "id": "fixture",
                                        }
                                    ]
                                },
                                "outputKey": "일반",
                            }
                        ]
                    },
                    "options": {"fallbackOutput": "extra"},
                },
            },
            {"name": "existing", "credentials": {"fixture": "kept"}},
        ],
        "connections": {
            "작업 종류 분기": {
                "main": [
                    [{"node": "일반 질답 · 지침 대기", "type": "main", "index": 0}],
                    [{"node": "fallback", "type": "main", "index": 0}],
                ]
            },
            "일반 질답 · 지침 대기": {
                "main": [[{"node": "AI 프롬프트 정리", "type": "main", "index": 0}]]
            },
        },
        "settings": {"saveDataSuccessExecution": "none"},
    }
    before = copy.deepcopy(workflow)
    prepared = prepare_translation(workflow)
    assert workflow == before
    assert prepared["nodes"][1] == before["nodes"][1]
    assert prepared["settings"] == before["settings"]
    outputs = prepared["connections"]["작업 종류 분기"]["main"]
    assert outputs[0] == before["connections"]["작업 종류 분기"]["main"][0]
    assert outputs[-1] == before["connections"]["작업 종류 분기"]["main"][-1]
    assert outputs[1][0]["node"] == NAME
    assert prepare_translation(prepared) == prepared


def test_translation_job_validation_queue_and_ownership(app):
    from dataclasses import replace

    from conftest import login

    client, owner = login(app)
    payload = {"request_id": "translation-test", "text": "안녕하세요", "target_language": "en"}
    assert client.post("/api/translations", json=payload).status_code == 503
    app.state.settings = replace(
        app.state.settings,
        n8n_ai_webhook_url="https://example.invalid/webhook",
        n8n_compute_context_ready=True,
    )
    assert (
        client.post("/api/translations", json={**payload, "target_language": "auto"}).status_code
        == 422
    )
    assert client.post("/api/translations", json={**payload, "text": " "}).status_code == 422
    response = client.post("/api/translations", json=payload)
    assert response.status_code == 202, response.text
    job = response.json()
    assert job["task_type"] == "text.translate" and job["status"] == "pending"
    assert client.post("/api/translations", json=payload).json()["id"] == job["id"]
    assert client.post("/api/translations", json={**payload, "text": "changed"}).status_code == 409
    tasks = client.get("/api/tasks?service=translation").json()
    assert tasks[0]["id"] == job["id"] and tasks[0]["kind"] == "text.translate"
    assert client.get(f"/api/ai/jobs/{job['id']}").status_code == 200
    assert client.get(f"/api/ai/jobs/{job['id']}/translation.txt").status_code == 409
    login(app, status="pending")
    assert client.post("/api/translations", json=payload).status_code == 403


def test_translation_execution_usage_result_validation_and_expiry(app, monkeypatch):
    from dataclasses import replace

    from conftest import login

    from noedaeri.ai_jobs import cleanup_ai_results
    from noedaeri.compute import compute_context

    client, _ = login(app)
    app.state.settings = replace(
        app.state.settings,
        n8n_ai_webhook_url="https://example.invalid/webhook",
        n8n_compute_context_ready=True,
    )

    async def workflow(settings, data):
        assert compute_context.get() is not None
        return {
            "status": "completed",
            "result": {"translated_text": "Hello"} if data.prompt != "invalid" else {},
            "provider": "fixture",
            "model": "fixture-model",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr("noedaeri.ai_jobs.run_n8n_workflow", workflow)
    payload = {
        "request_id": "sync-translation",
        "task_type": "text.translate",
        "prompt": "안녕하세요",
        "input": {"target_language": "en"},
    }
    response = client.post("/api/ai/jobs", json=payload)
    assert response.status_code == 200, response.text
    job = response.json()
    assert job["status"] == "succeeded" and job["result"]["translated_text"] == "Hello"
    assert client.get(f"/api/ai/jobs/{job['id']}/translation.txt").text == "Hello"
    platform_headers = {"X-Noedaeri-API-Key": app.state.settings.integration_key}
    assert client.get(f"/api/v1/ai/jobs/{job['id']}", headers=platform_headers).status_code == 404
    assert (
        client.get(
            f"/api/v1/ai/jobs/{job['id']}/translation.txt", headers=platform_headers
        ).status_code
        == 404
    )
    assert (
        client.get("/api/ai/usage?task_type=text.translate").json()["summary"][0]["total_tokens"]
        == 15
    )
    assert (
        client.post(
            "/api/ai/jobs", json={**payload, "request_id": "invalid-result", "prompt": "invalid"}
        ).status_code
        == 502
    )
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE ai_jobs SET expires_at=now()-interval '1 second' WHERE id=%s", (job["id"],)
        )
    assert client.get(f"/api/ai/jobs/{job['id']}").json()["result"] is None
    assert client.get(f"/api/ai/jobs/{job['id']}/translation.txt").status_code == 410
    cleanup_ai_results(app.state.db)
    with app.state.db.connect() as conn:
        row = conn.execute(
            "SELECT prompt,input,result FROM ai_jobs WHERE id=%s", (job["id"],)
        ).fetchone()
        assert row["prompt"] == "" and row["input"] == {} and row["result"] is None
