from uuid import UUID, uuid4

from psycopg.types.json import Jsonb

from .db import Database


class Queue:
    def __init__(self, db: Database, lease_seconds: int, ttl: int):
        self.db, self.lease_seconds, self.ttl = db, lease_seconds, ttl

    def claim(self, worker_id: UUID, kinds: list[str]):
        with self.db.connect() as conn:
            conn.execute(
                "INSERT INTO workers(id) VALUES (%s) ON CONFLICT(id) DO UPDATE SET last_seen=now()",
                (worker_id,),
            )
            # Expired owners never cause automatic re-execution of unknown live processes.
            conn.execute(
                "UPDATE jobs SET status='interrupted', error_code='lease_lost', updated_at=now() "
                "WHERE status='running' AND lease_until < now()"
            )
            return conn.execute(
                "UPDATE jobs SET status='running', stage='executing', lease_token=%s, "
                "worker_id=%s, "
                "lease_until=now()+make_interval(secs=>%s), updated_at=now() "
                "WHERE id=(SELECT id FROM jobs WHERE status='queued' AND kind=ANY(%s) "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
                (uuid4(), worker_id, self.lease_seconds, kinds),
            ).fetchone()

    def heartbeat(self, job_id: UUID, token: UUID, stage: str | None = None):
        with self.db.connect() as conn:
            job = conn.execute(
                "UPDATE jobs SET lease_until=now()+make_interval(secs=>%s), updated_at=now(), "
                "stage=COALESCE(%s,stage) "
                "WHERE id=%s AND lease_token=%s AND status='running' AND lease_until>now() "
                "RETURNING cancel_requested, worker_id",
                (self.lease_seconds, stage, job_id, token),
            ).fetchone()
            if job:
                conn.execute("UPDATE workers SET last_seen=now() WHERE id=%s", (job["worker_id"],))
            return job

    def finish(self, job_id: UUID, token: UUID, status: str, code: str | None, result=None):
        if status not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("Invalid terminal status")
        with self.db.connect() as conn:
            job = conn.execute(
                "SELECT * FROM jobs WHERE id=%s AND lease_token=%s "
                "AND status='running' AND lease_until>now() FOR UPDATE",
                (job_id, token),
            ).fetchone()
            if not job:
                return False
            if job["cancel_requested"]:
                status, code = "cancelled", None
            conn.execute(
                "UPDATE jobs SET stage=CASE WHEN %s='succeeded' THEN 'finished' ELSE stage END, "
                "status=%s, error_code=%s, "
                "finished_at=now(), updated_at=now(), "
                "expires_at=CASE WHEN %s='succeeded' THEN now()+make_interval(secs=>%s) END, "
                "result_state=CASE WHEN %s='succeeded' THEN 'available' ELSE 'none' END, "
                "lease_until=NULL, result=%s WHERE id=%s",
                (
                    status,
                    status,
                    code,
                    status,
                    self.ttl,
                    status,
                    Jsonb(result) if status == "succeeded" else None,
                    job_id,
                ),
            )
            return True

    def cancel(self, job_id: UUID, owner_id: UUID):
        with self.db.connect() as conn:
            return conn.execute(
                "UPDATE jobs SET cancel_requested=true, updated_at=now(), "
                "status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END, "
                "finished_at=CASE WHEN status='queued' THEN now() ELSE finished_at END "
                "WHERE id=%s AND owner_id=%s AND status IN ('queued','running') RETURNING id",
                (job_id, owner_id),
            ).fetchone()
