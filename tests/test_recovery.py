import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from conftest import login
from test_jobs import new_job, queued

from noedaeri.execution import execution_lock


def test_reservation_fencing_and_capacity(app):
    job_id, _ = queued(app)
    job = app.state.queue.claim(uuid4(), ["video.thumbnail"])
    storage, db = app.state.storage, app.state.db
    assert storage.reserve(db, job_id, uuid4(), 1) == "lease_lost"
    assert storage.reserve(db, job_id, job["lease_token"], 8_000_000) is None
    assert storage.reserve(db, job_id, job["lease_token"], 8_000_000) is None
    with db.connect() as conn:
        assert (
            conn.execute("SELECT output_reserved FROM jobs WHERE id=%s", (job_id,)).fetchone()[
                "output_reserved"
            ]
            == 8_000_000
        )
    assert (
        storage.reserve(db, job_id, job["lease_token"], 100_000_000) == "storage_capacity_exceeded"
    )
    assert app.state.queue.finish(job_id, job["lease_token"], "failed", "worker_failed")
    with db.connect() as conn:
        assert (
            conn.execute("SELECT output_reserved FROM jobs WHERE id=%s", (job_id,)).fetchone()[
                "output_reserved"
            ]
            == 0
        )


def test_concurrent_reservations_do_not_overbook(app):
    first, _ = queued(app)
    second, _ = queued(app)
    one = app.state.queue.claim(uuid4(), ["video.thumbnail"])
    two = app.state.queue.claim(uuid4(), ["video.thumbnail"])

    def reserve(job):
        return app.state.storage.reserve(app.state.db, job["id"], job["lease_token"], 12_000_000)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, [one, two]))
    assert results.count(None) == 1
    assert results.count("storage_capacity_exceeded") == 1


def test_recovery_waits_for_inherited_child_lock(app, tmp_path):
    job_id, _ = queued(app)
    app.state.queue.claim(uuid4(), ["video.thumbnail"])
    marker = tmp_path / "child-pid"
    root = app.state.storage.root
    script = """
import sys
from pathlib import Path
from noedaeri.execution import execution_lock
from noedaeri.media import run_process
with execution_lock(Path(sys.argv[1]), sys.argv[2]):
    child = ('import os,sys,time;from pathlib import Path;'
             'Path(sys.argv[1]).write_text(str(os.getpid()));time.sleep(30)')
    run_process([sys.executable, '-c', child, sys.argv[3]], 60, lambda: True)
"""
    parent = subprocess.Popen(
        [sys.executable, "-c", script, str(root), str(job_id), str(marker)],
        env=dict(os.environ, PYTHONPATH=str(Path("src").resolve())),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    child = None
    try:
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists()
        child = int(marker.read_text())
        parent.kill()
        parent.wait(timeout=5)
        with app.state.db.connect() as conn:
            conn.execute(
                "UPDATE jobs SET status='interrupted',output_reserved=100 WHERE id=%s", (job_id,)
            )
        app.state.storage.recover(app.state.db)
        with app.state.db.connect() as conn:
            row = conn.execute(
                "SELECT status,output_reserved FROM jobs WHERE id=%s", (job_id,)
            ).fetchone()
            assert row == {"status": "interrupted", "output_reserved": 100}
        os.kill(child, signal.SIGTERM)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            app.state.storage.recover(app.state.db)
            with app.state.db.connect() as conn:
                row = conn.execute(
                    "SELECT status,output_reserved FROM jobs WHERE id=%s", (job_id,)
                ).fetchone()
            if row["status"] == "failed":
                break
            time.sleep(0.05)
        assert row == {"status": "failed", "output_reserved": 0}
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()
        if child:
            try:
                os.kill(child, signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_retry_requires_safe_terminal_state_and_new_input(app):
    client, _ = login(app)
    first = new_job(client).json()
    payload = {
        "kind": first["kind"],
        "title": first["title"],
        "input": {"type": "upload"},
        "options": {"seconds": 0},
        "idempotency_key": str(uuid4()),
        "retry_of": first["id"],
    }
    assert client.post("/api/jobs", json=payload).status_code == 409
    with app.state.db.connect() as conn:
        conn.execute("UPDATE jobs SET status='failed' WHERE id=%s", (first["id"],))
    with execution_lock(app.state.storage.root, first["id"]):
        assert client.post("/api/jobs", json=payload).status_code == 409
    retried = client.post("/api/jobs", json=payload)
    assert retried.status_code == 201
    assert retried.json()["id"] != first["id"]
    assert retried.json()["retry_of"] == first["id"]
    assert retried.json()["status"] == "uploading"
    assert client.post("/api/jobs", json=payload).json()["id"] == retried.json()["id"]
    login(app)
    assert (
        client.post("/api/jobs", json={**payload, "idempotency_key": str(uuid4())}).status_code
        == 404
    )


def test_legacy_interruption_is_not_assumed_safe(app):
    job_id, _ = queued(app)
    with app.state.db.connect() as conn:
        conn.execute(
            "UPDATE jobs SET status='interrupted',execution_guarded=false WHERE id=%s", (job_id,)
        )
    app.state.storage.recover(app.state.db)
    with app.state.db.connect() as conn:
        assert (
            conn.execute("SELECT status FROM jobs WHERE id=%s", (job_id,)).fetchone()["status"]
            == "interrupted"
        )
