"""Opt-in AI completion outbox. Original prompts and model results never enter events."""

import json
from datetime import UTC, datetime

from .integration import PLATFORM_OWNER


def collect_ai_events(db, job_id=None):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT j.id,j.terminal_event_id,j.status,j.request_id,j.project,j.environment,"
            "j.task_type,j.error_code,j.expires_at,j.finished_at FROM ai_jobs j "
            "WHERE owner_id=%s AND notify "
            "AND status IN ('succeeded','failed','cancelled') "
            "AND NOT EXISTS(SELECT 1 FROM ai_deliveries d WHERE d.job_id=j.id) "
            "AND (%s::uuid IS NULL OR j.id=%s) ORDER BY finished_at LIMIT 200",
            (PLATFORM_OWNER, job_id, job_id),
        ).fetchall()
        for job in rows:
            path = f"/api/v1/ai/jobs/{job['id']}"
            payload = {
                "version": 1,
                "source": "ai",
                "event_id": str(job["terminal_event_id"]),
                "type": "ai.job." + job["status"],
                "job_id": str(job["id"]),
                "request_id": job["request_id"],
                "project": job["project"],
                "environment": job["environment"],
                "occurred_at": job["finished_at"].isoformat(),
                "job": {
                    "task_type": job["task_type"],
                    "status": job["status"],
                    "error_code": job["error_code"],
                    "expires_at": job["expires_at"].isoformat(),
                },
                "job_path": path,
                "result_path": path if job["status"] == "succeeded" else None,
                "receipt_path": path + "/receipt" if job["status"] == "succeeded" else None,
            }
            conn.execute(
                "INSERT INTO ai_deliveries(id,job_id,body) VALUES(%s,%s,%s) "
                "ON CONFLICT DO NOTHING",
                (
                    job["terminal_event_id"],
                    job["id"],
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                ),
            )


def present_ai_delivery(conn, job, settings):
    if not job.get("notify"):
        return None
    row = conn.execute(
        "SELECT state,attempts,last_http_status,next_attempt_at FROM ai_deliveries WHERE job_id=%s",
        (job["id"],),
    ).fetchone()
    return {
        **(
            dict(row)
            if row
            else {
                "state": "waiting",
                "attempts": 0,
                "last_http_status": None,
                "next_attempt_at": None,
            }
        ),
        "configured": bool(settings and settings.ai_webhook_url and settings.webhook_secret),
    }


def ai_result_available(job):
    return not job.get("received_at") and (
        not job.get("expires_at") or job["expires_at"] > datetime.now(UTC)
    )
