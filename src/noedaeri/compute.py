"""FIFO admission shared by native workers, direct calls and delegated workflows.

Session locks prove ownership; losing one never proves an external execution stopped.
Native children additionally inherit the existing OS compute lock.
"""

import asyncio
import hashlib
import secrets
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from uuid import uuid4

from fastapi import HTTPException

from .execution import native_compute_lock

ROOT_LOCK = 756910
CHILD_LOCK = 756911
MUTATION_LOCK = 756912
compute_context = ContextVar("compute_context", default=None)


def guard_key(identifier):
    return int.from_bytes(hashlib.sha256(str(identifier).encode()).digest()[:8], "big", signed=True)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class Compute:
    def __init__(self, db, root):
        self.db, self.root = db, root

    def owner_for_context(self, token):
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT r.owner_id FROM compute_requests r JOIN users u ON u.id=r.owner_id "
                "LEFT JOIN ai_jobs j ON j.id=r.job_id "
                "WHERE r.token_hash=%s AND r.source='ai' AND r.state='running' "
                "AND u.status='approved' AND (j.id IS NULL OR "
                "(j.status='running' AND NOT j.cancel_requested))",
                (token_hash(token),),
            ).fetchone()
        if not row:
            raise HTTPException(409, "compute_context_expired")
        return row["owner_id"]

    def reap(self, conn, exclude=None):
        # Serialize inspection against admission/release, never against a long execution.
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (MUTATION_LOCK,))
        rows = conn.execute(
            "SELECT id,state FROM compute_requests WHERE state IN ('queued','running')"
        ).fetchall()
        for row in rows:
            if row["id"] == exclude:
                continue
            key = guard_key(row["id"])
            if conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (key,)).fetchone()["ok"]:
                conn.execute("SELECT pg_advisory_unlock(%s)", (key,))
                conn.execute(
                    "UPDATE compute_requests SET state=%s,error_code=%s,finished_at=now() "
                    "WHERE id=%s",
                    (
                        "interrupted" if row["state"] == "running" else "cancelled",
                        "execution_unconfirmed" if row["state"] == "running" else "owner_lost",
                        row["id"],
                    ),
                )
                if row["state"] == "running":
                    work = conn.execute(
                        "SELECT source,job_id FROM compute_requests WHERE id=%s", (row["id"],)
                    ).fetchone()
                    table, state = {
                        "media": ("jobs", "interrupted"),
                        "ai": ("ai_jobs", "failed"),
                        "operation": ("operation_jobs", "interrupted"),
                        "indexing": ("ai_indexing_jobs", "failed"),
                    }[work["source"]]
                    conn.execute(
                        f"UPDATE {table} SET status=%s,error_code='execution_unconfirmed',"
                        "finished_at=now() WHERE id=%s AND status IN ('running','pending')",
                        (state, work["job_id"]),
                    )

    def snapshot(self, owner_id=None):
        with self.db.connect() as conn:
            self.reap(conn)
            return conn.execute(
                "SELECT id,source,job_id,kind,state,parent_id,error_code,created_at,started_at "
                "FROM compute_requests WHERE state IN ('queued','running','interrupted') "
                "AND (%s::uuid IS NULL OR owner_id=%s) ORDER BY sequence",
                (owner_id, owner_id),
            ).fetchall()

    def acknowledge_stopped(self, identifier):
        with self.db.connect() as conn:
            self.reap(conn)
            row = conn.execute(
                "SELECT * FROM compute_requests WHERE id=%s AND state='interrupted' FOR UPDATE",
                (identifier,),
            ).fetchone()
            if not row:
                raise HTTPException(409, "compute_not_interrupted")
            if not conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (CHILD_LOCK,)).fetchone()[
                "ok"
            ]:
                raise HTTPException(409, "compute_child_still_running")
            try:
                # An orphan local model/FFmpeg process keeps this reservation until exit.
                with native_compute_lock(self.root):
                    conn.execute(
                        "UPDATE compute_requests SET state='released',finished_at=now() "
                        "WHERE (id=%s OR parent_id=%s) AND state='interrupted'",
                        (identifier, identifier),
                    )
            except BlockingIOError:
                raise HTTPException(409, "compute_process_still_running") from None
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (CHILD_LOCK,))

    @contextmanager
    def ticket(self, source, job_id, kind, owner_id, parent_token=None):
        identifier = uuid4()
        token = secrets.token_urlsafe(32)
        with self.db.connect() as conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (guard_key(identifier),))
            self.reap(conn)
            waiting = conn.execute(
                "SELECT count(*) AS count FROM compute_requests WHERE state='queued'"
            ).fetchone()["count"]
            if waiting >= 32:
                raise HTTPException(429, "compute_queue_full", headers={"Retry-After": "5"})
            parent = None
            if parent_token:
                parent = conn.execute(
                    "SELECT r.id FROM compute_requests r JOIN users u ON u.id=r.owner_id "
                    "LEFT JOIN ai_jobs j ON j.id=r.job_id "
                    "WHERE r.token_hash=%s AND r.source='ai' AND r.state='running' "
                    "AND u.status='approved' AND (j.id IS NULL OR "
                    "(j.status='running' AND NOT j.cancel_requested))",
                    (token_hash(parent_token),),
                ).fetchone()
                if not parent:
                    raise HTTPException(409, "compute_context_expired")
            conn.execute(
                "INSERT INTO compute_requests(id,source,job_id,kind,owner_id,token_hash,parent_id) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s)",
                (
                    identifier,
                    source,
                    job_id,
                    kind,
                    owner_id,
                    token_hash(token),
                    parent["id"] if parent else None,
                ),
            )
            conn.commit()
            lease = ComputeLease(self, conn, identifier, token, parent["id"] if parent else None)
            context = compute_context.set(lease)
            try:
                yield lease
            finally:
                compute_context.reset(context)
                lease.close()

    @asynccontextmanager
    async def slot(
        self, source, job_id, kind, owner_id, wait_seconds, parent_token=None, alive=None
    ):
        with self.ticket(source, job_id, kind, owner_id, parent_token) as lease:
            deadline = time.monotonic() + wait_seconds
            while not lease.try_start():
                if alive and not alive():
                    raise HTTPException(409, "compute_job_cancelled")
                if time.monotonic() >= deadline:
                    raise HTTPException(429, "compute_wait_timeout", headers={"Retry-After": "5"})
                await asyncio.sleep(0.1)
            if alive and not alive():
                raise HTTPException(409, "compute_job_cancelled")
            try:
                yield lease
            except (asyncio.CancelledError, HTTPException) as error:
                # Cancellation/HTTP timeout does not establish that remote work stopped.
                if (
                    isinstance(error, asyncio.CancelledError)
                    or (source == "ai" and error.status_code in {502, 504})
                    or (
                        isinstance(error, HTTPException)
                        and error.detail == "embedding_provider_unavailable"
                    )
                ):
                    lease.unconfirmed = True
                raise


class ComputeLease:
    def __init__(self, compute, conn, identifier, token, parent):
        self.compute, self.conn = compute, conn
        self.id, self.token, self.parent = identifier, token, parent
        self.started = self.unconfirmed = False

    def try_start(self):
        self.compute.reap(self.conn, exclude=self.id)
        if self.parent:
            active = self.conn.execute(
                "SELECT 1 FROM compute_requests WHERE id=%s AND state='running'", (self.parent,)
            ).fetchone()
            if not active:
                self.conn.commit()
                raise HTTPException(409, "compute_context_expired")
            lock = CHILD_LOCK
            first = True
        else:
            blocked = self.conn.execute(
                "SELECT 1 FROM compute_requests WHERE state='interrupted' LIMIT 1"
            ).fetchone()
            first = self.conn.execute(
                "SELECT id FROM compute_requests WHERE state='queued' AND parent_id IS NULL "
                "ORDER BY sequence LIMIT 1"
            ).fetchone()
            first = first and first["id"] == self.id and not blocked
            lock = ROOT_LOCK
        if first:
            try:
                with native_compute_lock(self.compute.root):
                    pass
            except BlockingIOError:
                first = False
        if (
            first
            and self.conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (lock,)).fetchone()["ok"]
        ):
            self.conn.execute(
                "UPDATE compute_requests SET state='running',started_at=now() WHERE id=%s",
                (self.id,),
            )
            self.started = True
        self.conn.commit()
        return self.started

    def alive(self):
        try:
            parent = self.parent or self.id
            active = self.conn.execute(
                "SELECT 1 FROM compute_requests r JOIN users u ON u.id=r.owner_id "
                "LEFT JOIN ai_jobs j ON j.id=r.job_id AND r.source='ai' "
                "WHERE r.id=%s AND r.state='running' AND u.status='approved' "
                "AND (j.id IS NULL OR (j.status='running' AND NOT j.cancel_requested))",
                (parent,),
            ).fetchone()
            self.conn.commit()
            return bool(active)
        except Exception:
            return False

    def close(self):
        try:
            self.conn.rollback()
            self.conn.execute("SELECT pg_advisory_xact_lock(%s)", (MUTATION_LOCK,))
            if self.started and not self.parent:
                # A workflow may have responded before an outstanding child exited.
                children = self.conn.execute(
                    "SELECT 1 FROM compute_requests WHERE parent_id=%s "
                    "AND state IN ('running','interrupted') LIMIT 1",
                    (self.id,),
                ).fetchone()
                self.unconfirmed = self.unconfirmed or bool(children)
            if self.started:
                try:
                    with native_compute_lock(self.compute.root):
                        pass
                except BlockingIOError:
                    self.unconfirmed = True
            self.conn.execute(
                "UPDATE compute_requests SET state=%s,error_code=%s,finished_at=now() WHERE id=%s",
                (
                    "interrupted"
                    if self.unconfirmed
                    else "released"
                    if self.started
                    else "cancelled",
                    "execution_unconfirmed" if self.unconfirmed else None,
                    self.id,
                ),
            )
            self.conn.commit()
        except Exception:
            # The persisted running ticket is reconciled as unconfirmed, never silently replayed.
            pass


def parent_context(request):
    token = request.headers.get("X-Noedaeri-Compute-Token")
    if token and not request.url.path.startswith("/api/ai/v1/"):
        raise HTTPException(403, "compute_context_internal_only")
    if token and len(token) > 128:
        raise HTTPException(422, "invalid_compute_context")
    if not token and request.url.path.startswith("/api/ai/v1/"):
        with request.app.state.db.connect() as conn:
            if conn.execute(
                "SELECT 1 FROM compute_requests WHERE source='ai' AND state='running' LIMIT 1"
            ).fetchone():
                # Old workflows fail promptly instead of waiting behind the root they own.
                raise HTTPException(409, "compute_context_required")
    return token
