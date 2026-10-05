import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import login

from noedaeri.config import Settings
from noedaeri.raya import Raya, RayaError

RESULT = {
    "task_type": "chat.general",
    "model_tier": "L1",
    "probabilities": {"L1": 0.8, "L2": 0.15, "L3": 0.05},
    "confidence": 0.7,
    "input_tokens": 40,
    "input_truncated": False,
    "inference_ms": 1.2,
    "model": "TextCortex/raya",
    "revision": "a" * 40,
    "device": "cpu",
    "runtime": "onnx-fp32",
}
PAYLOAD = {"task_type": "chat.general", "prompt": "sample", "instruction": ""}


@pytest.fixture
def fake_runtime(tmp_path, monkeypatch):
    import noedaeri.raya as module

    monkeypatch.setattr(module, "ROOT", tmp_path)
    (tmp_path / "model").mkdir()
    python = tmp_path / "raya_runtime/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    child = tmp_path / "scripts/raya_inference.py"
    child.parent.mkdir()
    child.write_text(
        "import json,os,sys,time\n"
        "assert 'DATABASE_URL' not in os.environ and 'NOEDAERI_RAYA_API_KEY' not in os.environ\n"
        "print(json.dumps({'ready':True}),flush=True)\n"
        "for line in sys.stdin:\n"
        "    payload=json.loads(line)\n"
        "    if payload['prompt']=='partial':\n"
        "        print('{',end='',flush=True)\n"
        "        time.sleep(5)\n"
        "    if payload['prompt']=='slow': time.sleep(0.4)\n"
        "    if payload['prompt']=='crash': os._exit(3)\n"
        f"    result=json.loads({json.dumps(RESULT)!r})\n"
        "    result['task_type']=payload['task_type']\n"
        "    if payload['prompt']=='bad_probs': result['probabilities']['L1']=1.9\n"
        "    if payload['prompt']=='old_tier': result['model_tier']='small_model'\n"
        "    if payload['prompt']=='removed_tier': result['model_tier']='L4'\n"
        "    print(json.dumps(result),flush=True)\n"
    )
    return tmp_path


@pytest.fixture
def router(fake_runtime):
    router = Raya(
        Settings(
            database_url="",
            worker_key="",
            public_origin="",
            storage_root=Path(fake_runtime),
            raya_enabled=True,
            raya_model_root=fake_runtime / "model",
            raya_timeout=2,
            raya_wait=0.05,
            raya_minimum_keep=60,
            raya_idle=5,
        )
    )
    yield router
    router.close()


def test_reuse_then_idle_reap_observes_minimum_keep(router):
    first = router.route(PAYLOAD)
    process = router.process
    second = router.route(PAYLOAD)
    assert first["cold_start"] and not second["cold_start"]
    assert router.process is process
    router.last_used = time.monotonic() - 10
    router.reap_idle()
    assert router.process is process
    router.started = time.monotonic() - 61
    router.reap_idle()
    assert process.poll() is not None and router.process is None
    assert router.snapshot()["state"] == "off"


def test_busy_is_bounded_and_does_not_stop_active_request(router):
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(router.route, dict(PAYLOAD, prompt="slow"))
        deadline = time.monotonic() + 2
        while router.state != "processing" and time.monotonic() < deadline:
            time.sleep(0.005)
        assert router.state == "processing"
        process = router.process
        router.reap_idle()
        with pytest.raises(RayaError, match="raya_busy"):
            router.route(PAYLOAD)
        with pytest.raises(RayaError, match="raya_busy"):
            router.release()
        assert future.result()["model_tier"] == "L1"
        assert router.process is process and process.poll() is None


@pytest.mark.parametrize(
    "prompt,error",
    [
        ("partial", "raya_timeout"),
        ("crash", "raya_process_failed"),
        ("bad_probs", "raya_process_failed"),
        ("old_tier", "raya_process_failed"),
        ("removed_tier", "raya_process_failed"),
    ],
)
def test_failure_reaps_child_before_retry(router, prompt, error):
    router.settings = replace(router.settings, raya_timeout=0.15)
    with pytest.raises(RayaError, match=error):
        router.route(dict(PAYLOAD, prompt=prompt))
    assert router.process is None
    assert router.snapshot()["state"] == "error"
    assert router.route(PAYLOAD)["cold_start"]


def test_closed_router_cannot_restart(router):
    router.route(PAYLOAD)
    process = router.process
    router.close()
    assert process.poll() is not None
    with pytest.raises(RayaError, match="raya_not_configured"):
        router.route(PAYLOAD)


def test_other_api_process_cannot_reload_until_child_reaped(router):
    router.route(PAYLOAD)
    original = router.process
    # Simulate loss of the parent's descriptor; the model child retains ownership.
    router.execution_lock.close()
    router.execution_lock = None
    replacement = Raya(router.settings)
    try:
        with pytest.raises(RayaError, match="raya_busy"):
            replacement.route(PAYLOAD)
        assert replacement.process is None and original.poll() is None
        router.close()
        assert original.poll() is not None
        assert replacement.route(PAYLOAD)["cold_start"]
    finally:
        replacement.close()


def test_route_auth_limits_and_role_separation(app, fake_runtime):
    client = app.state.client
    assert client.post("/api/ai/v1/raya/route", json=PAYLOAD).status_code == 401
    assert client.post("/api/raya/route", json=PAYLOAD).status_code == 401
    app.state.raya.settings = replace(
        app.state.settings,
        raya_enabled=True,
        raya_timeout=2,
        raya_model_root=fake_runtime / "model",
    )
    client, user_id = login(app)
    assert client.get("/api/admin/raya/status").status_code == 403
    assert client.post("/api/admin/raya/release").status_code == 403
    assert client.post("/api/raya/route", json=PAYLOAD).status_code == 200
    assert client.post("/api/raya/route", json=dict(PAYLOAD, has_images=True)).status_code == 200
    assert client.post("/api/raya/route", json=dict(PAYLOAD, has_images="true")).status_code == 422
    bad = client.post("/api/raya/route", json=dict(PAYLOAD, prompt="", secret="private"))
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_raya_request"
    assert "private" not in bad.text
    assert client.post("/api/raya/route", content=b"x" * 65537).status_code == 413
    client.headers["X-CSRF-Token"] = "wrong"
    assert client.post("/api/raya/route", json=PAYLOAD).status_code == 403
    client.cookies.clear()
    assert (
        client.post(
            "/api/ai/v1/raya/route",
            json=PAYLOAD,
            headers={"X-Noedaeri-API-Key": app.state.settings.integration_key},
        ).status_code
        == 401
    )
    headers = {"X-Noedaeri-Raya-Key": app.state.settings.raya_key}
    assert client.post("/api/ai/v1/raya/route", json=PAYLOAD, headers=headers).status_code == 200
    assert client.get("/api/jobs", headers=headers).status_code == 401
    assert client.get("/api/admin/raya/status", headers=headers).status_code == 401
    client, _ = login(app, status="pending")
    assert client.post("/api/raya/route", json=PAYLOAD).status_code == 403


def test_admin_policy_persists_and_release_reaps(app, fake_runtime):
    client, _ = login(app, role="admin")
    app.state.raya.settings = replace(
        app.state.settings,
        raya_enabled=True,
        raya_timeout=2,
        raya_model_root=fake_runtime / "model",
    )
    assert client.post("/api/raya/route", json=PAYLOAD).status_code == 200
    process = app.state.raya.process
    response = client.patch(
        "/api/admin/raya/policy",
        json={
            "minimum_keep_seconds": 20,
            "idle_seconds": 45,
        },
    )
    assert response.status_code == 200
    assert response.json()["idle_seconds"] == 45
    with app.state.db.connect() as conn:
        policy = conn.execute("SELECT * FROM raya_policy").fetchone()
    assert policy["minimum_keep_seconds"] == 20 and policy["idle_seconds"] == 45
    assert (
        client.patch(
            "/api/admin/raya/policy",
            json={
                "minimum_keep_seconds": 0,
                "idle_seconds": 0,
            },
        ).status_code
        == 422
    )
    assert client.post("/api/admin/raya/release").json()["state"] == "off"
    assert process.poll() is not None
