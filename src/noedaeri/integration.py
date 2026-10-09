"""Dedicated platform identity and durable, signed completion delivery."""

import hashlib
import hmac
import json
import time
from uuid import UUID

import httpx

PLATFORM_OWNER = UUID("00000000-0000-5000-8000-000000000001")


def signature(secret, timestamp, body):
    return hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()


class Webhooks:
    def __init__(self, db, settings, *, table="deliveries", url=None):
        if table not in {"deliveries", "ai_deliveries"}:
            raise ValueError("Unsupported outbox")
        self.db, self.settings = db, settings
        self.table = table
        self.url = settings.webhook_url if url is None else url
        self.transport = None

    def collect(self, job_id=None):
        if self.table == "ai_deliveries":
            from .ai_delivery import collect_ai_events

            return collect_ai_events(self.db, job_id)
        # Terminal rows are durable. Catch up after downtime, including upload timeout/recovery.
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT j.* FROM jobs j WHERE origin='platform' "
                "AND status IN ('succeeded','failed','cancelled') "
                "AND NOT EXISTS(SELECT 1 FROM deliveries d WHERE d.job_id=j.id) "
                "AND (%s::uuid IS NULL OR j.id=%s) ORDER BY finished_at",
                (job_id, job_id),
            ).fetchall()
            for job in rows:
                payload = {
                    "version": 1,
                    "event_id": str(job["terminal_event_id"]),
                    "type": "job." + job["status"],
                    "job_id": str(job["id"]),
                    "idempotency_key": str(job["idempotency_key"]),
                    "occurred_at": job["finished_at"].isoformat(),
                    "job": {
                        "kind": job["kind"],
                        "status": job["status"],
                        "error_code": job["error_code"],
                        "result": job["result"],
                        "expires_at": job["expires_at"].isoformat() if job["expires_at"] else None,
                    },
                    "job_path": f"/api/v1/jobs/{job['id']}",
                    "result_path": f"/api/v1/jobs/{job['id']}/result"
                    if job["status"] == "succeeded"
                    else None,
                }
                conn.execute(
                    "INSERT INTO deliveries(id,job_id,body) VALUES(%s,%s,%s) "
                    "ON CONFLICT DO NOTHING",
                    (
                        job["terminal_event_id"],
                        job["id"],
                        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    ),
                )

    def dispatch_one(self):
        if not self.url or not self.settings.webhook_secret:
            return False
        # Keep the row locked across the bounded request; process death rolls back the attempt.
        # Remote acceptance then local crash can duplicate delivery, hence stable event IDs.
        with self.db.connect() as conn:
            event = conn.execute(
                f"SELECT * FROM {self.table} WHERE state='pending' AND next_attempt_at<=now() "
                "ORDER BY next_attempt_at FOR UPDATE SKIP LOCKED LIMIT 1"
            ).fetchone()
            if not event:
                return False
            body = event["body"].encode()
            timestamp = str(int(time.time()))
            headers = {
                "Content-Type": "application/json",
                "X-Noedaeri-Event-ID": str(event["id"]),
                "X-Noedaeri-Timestamp": timestamp,
                "X-Noedaeri-Signature": "sha256="
                + signature(self.settings.webhook_secret, timestamp, body),
            }
            status = None
            try:
                with httpx.Client(
                    timeout=5, follow_redirects=False, trust_env=False, transport=self.transport
                ) as client:
                    with client.stream("POST", self.url, content=body, headers=headers) as response:
                        status = response.status_code
            except httpx.HTTPError:
                pass
            attempts = event["attempts"] + 1
            state = (
                "delivered"
                if status and 200 <= status < 300
                else ("failed" if attempts >= 8 else "pending")
            )
            conn.execute(
                f"UPDATE {self.table} SET state=%s,attempts=%s,last_http_status=%s,"
                "last_attempt_at=now(),next_attempt_at=now()+make_interval(secs=>%s) "
                "WHERE id=%s",
                (state, attempts, status, min(3600, 30 * 2 ** (attempts - 1)), event["id"]),
            )
        return True
